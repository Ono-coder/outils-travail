#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hmax_lidar.py
=============

Projection de la hauteur d'eau maximale d'un calcul TELEMAC-2D (rupture de
digue / brèche) sur un MNT LiDAR HD, pour obtenir une carte de Hmax à la
résolution du LiDAR plutôt qu'aux nœuds du maillage.

Chaîne de traitement
--------------------
1. Lecture du résultat TELEMAC : cote du fond Zf et cote max de la surface
   libre Zmax (variable MAXZ si présente, sinon max de S sur les pas de sortie).
2. Seuil de mise en eau aux nœuds (Hmax > h_sec). Sur les nœuds secs bordant
   un nœud mouillé, la ligne d'eau est prolongée horizontalement (moyenne des
   Zmax mouillés voisins) : c'est le MNT fin qui dessinera la limite, sur une
   maille au plus.
3. Fusion du MNT :
     - LiDAR HD là où il existe (masque valide légèrement érodé) ;
     - petits trous (< s_min) comblés par interpolation du LiDAR ;
     - grands trous (lit mineur, bâti) remplis par la bathy du modèle, avec
       une transition depuis le bord et un plafonnement par la cote de berge
       limité à la bande de transition.
4. Interpolation linéaire de la surface libre max sur la grille LiDAR
   (triangles du maillage), puis H = Zs - MNT.
5. Nettoyage : hauteur minimale, suppression des zones non connectées au
   cœur inondé (triangles entièrement mouillés).

Sorties (dossier -o)
--------------------
    mnt_fusion.tif       MNT fusionné LiDAR / bathy
    classes_fusion.tif   1 = LiDAR, 2 = trou comblé par le LiDAR, 3 = bathy
    zs_max.tif           surface libre max interpolée
    hmax.tif             hauteur d'eau max projetée sur le MNT

Usage
-----
    python hmax_lidar.py r2d_breche.slf lidarhd.vrt -o sorties

    Pour un LiDAR en dalles : gdalbuildvrt lidarhd.vrt dalles/*.tif
    Le raster est lu uniquement sur l'emprise du maillage (+ marge).

Prérequis
---------
    numpy, scipy, matplotlib, rasterio. L'environnement TELEMAC n'est pas
    nécessaire : le fichier Selafin (.slf/.res) est lu directement.
    Maillage et LiDAR dans le même système (ex. Lambert 93 / NGF-IGN69).

Mémoire
-------
    Compter environ 50 octets par pixel au pic (transformée de distance).
    Emprise de 5 km x 5 km à 0,5 m = 1e8 px ~ 5 Go. Au-delà, découper
    l'emprise avec --emprise.
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.fill import fillnodata
from rasterio.windows import Window
from scipy.ndimage import binary_erosion, distance_transform_edt, label
from matplotlib.tri import Triangulation, LinearTriInterpolator

log = logging.getLogger("hmax_lidar")


# ---------------------------------------------------------------------------
# Lecteur Selafin autonome (pas besoin de l'environnement TELEMAC)
# ---------------------------------------------------------------------------
class SelafinLecteur:
    """
    Lecture d'un fichier Selafin 2D (simple ou double précision, big ou
    little endian). Interface calquée sur TelemacFile :
    varnames, times, meshx, meshy, ikle2 (base 0), npoin2,
    get_data_value(nom, enregistrement), close().
    """

    def __init__(self, chemin):
        self.chemin = str(chemin)
        self._f = open(self.chemin, "rb")
        f = self._f

        # boutisme : le premier marqueur de record vaut 80 (titre)
        m = f.read(4)
        if np.frombuffer(m, ">i4")[0] == 80:
            self._e = ">"
        elif np.frombuffer(m, "<i4")[0] == 80:
            self._e = "<"
        else:
            raise ValueError(f"{self.chemin} : pas un fichier Selafin reconnu.")
        f.seek(0)

        self.titre = self._record().decode("latin-1", "replace")
        nbv1, nbv2 = self._ints()
        self.varnames = []
        for _ in range(nbv1 + nbv2):
            r = self._record().decode("latin-1", "replace")
            self.varnames.append(r[:16])
        self.nbv = nbv1 + nbv2
        self.iparam = self._ints()
        if self.iparam[9] == 1:
            self._ints()                                  # date
        nelem, npoin, ndp, _ = self._ints()[:4]
        if ndp != 3:
            raise ValueError(f"Éléments à {ndp} nœuds : seul le 2D triangulaire est géré.")
        if len(self.iparam) > 6 and self.iparam[6] > 1:
            raise ValueError("Fichier 3D (NPLAN > 1) : utiliser un résultat 2D.")
        self.npoin2 = npoin
        self.ikle2 = np.frombuffer(self._record(), self._e + "i4").reshape(nelem, ndp).astype(np.int64) - 1
        self._record()                                    # IPOBO / KNOLG

        # précision déduite de la taille du record X
        rx = self._record()
        self._fs = len(rx) // npoin
        if self._fs not in (4, 8):
            raise ValueError("Précision des réels non reconnue.")
        self._dt = np.dtype(self._e + ("f4" if self._fs == 4 else "f8"))
        self.meshx = np.frombuffer(rx, self._dt).astype(np.float64)
        self.meshy = np.frombuffer(self._record(), self._dt).astype(np.float64)

        # origine éventuelle du maillage (IPARAM 3 et 4)
        x0, y0 = self.iparam[2], self.iparam[3]
        if x0 or y0:
            log.info("Origine du maillage (IPARAM) : %d, %d ajoutée aux coordonnées.", x0, y0)
            self.meshx += x0
            self.meshy += y0

        # pas de temps
        self._debut = f.tell()
        self._taille_t = 4 + self._fs + 4
        self._taille_v = 4 + self._fs * npoin + 4
        self._taille_frame = self._taille_t + self.nbv * self._taille_v
        f.seek(0, 2)
        nframes = (f.tell() - self._debut) // self._taille_frame
        self.times = []
        for i in range(nframes):
            f.seek(self._debut + i * self._taille_frame + 4)
            self.times.append(float(np.frombuffer(f.read(self._fs), self._dt)[0]))

    def _record(self):
        n = int(np.frombuffer(self._f.read(4), self._e + "i4")[0])
        data = self._f.read(n)
        self._f.read(4)
        return data

    def _ints(self):
        return [int(v) for v in np.frombuffer(self._record(), self._e + "i4")]

    def get_data_value(self, nom, rec):
        iv = [v.strip() for v in self.varnames].index(nom.strip())
        if rec < 0:
            rec += len(self.times)
        pos = self._debut + rec * self._taille_frame + self._taille_t + iv * self._taille_v + 4
        self._f.seek(pos)
        return np.frombuffer(self._f.read(self._fs * self.npoin2), self._dt).astype(np.float64)

    def close(self):
        self._f.close()

NODATA = -9999.0

# Débuts de noms de variables reconnus (anglais / français, tronqués à 16 car.)
NOMS_VAR = {
    "fond": ("BOTTOM", "FOND"),
    "surface": ("FREE SURFACE", "SURFACE LIBRE"),
    "zmax": ("MAXIMUM ELEV", "COTE MAXI"),
}


# ---------------------------------------------------------------------------
# Lecture TELEMAC
# ---------------------------------------------------------------------------
def trouver_var(varnames, debuts):
    """Renvoie le premier nom de variable commençant par l'un des débuts."""
    for v in varnames:
        vv = v.strip().upper()
        if any(vv.startswith(d) for d in debuts):
            return v
    return None


def lire_telemac(chemin, var_fond=None, var_surface=None, var_zmax=None):
    """Lit maillage, fond et Zmax. Retourne x, y, ikle (base 0), zb, zmax."""
    res = SelafinLecteur(chemin)
    try:
        noms = list(res.varnames)
        log.info("Variables du résultat : %s", ", ".join(v.strip() for v in noms))
        nrec = len(res.times)

        v_fond = var_fond or trouver_var(noms, NOMS_VAR["fond"])
        if v_fond is None:
            raise ValueError("Variable de fond introuvable (utiliser --var-fond).")
        zb = np.asarray(res.get_data_value(v_fond, 0), dtype=np.float64)

        v_zmax = var_zmax or trouver_var(noms, NOMS_VAR["zmax"])
        if v_zmax:
            zmax = np.asarray(res.get_data_value(v_zmax, nrec - 1), dtype=np.float64)
            log.info("Zmax lu dans '%s' (max au pas de calcul).", v_zmax.strip())
        else:
            v_s = var_surface or trouver_var(noms, NOMS_VAR["surface"])
            if v_s is None:
                raise ValueError("Ni Zmax ni surface libre trouvés "
                                 "(utiliser --var-zmax ou --var-surface).")
            log.warning("Pas de variable Zmax : max de '%s' sur %d pas de sortie. "
                        "Le pic peut être sous-estimé si la sortie est espacée.",
                        v_s.strip(), nrec)
            zmax = np.full(zb.shape, -np.inf)
            for i in range(nrec):
                zmax = np.maximum(zmax, res.get_data_value(v_s, i))

        x = np.asarray(res.meshx, dtype=np.float64)
        y = np.asarray(res.meshy, dtype=np.float64)
        ikle = np.asarray(res.ikle2, dtype=np.int64)
    finally:
        res.close()

    if ikle.min() == 1:          # sécurité si connectivité en base 1
        ikle = ikle - 1
    log.info("Maillage : %d nœuds, %d triangles.", x.size, ikle.shape[0])
    return x, y, ikle, zb, zmax


# ---------------------------------------------------------------------------
# Ligne d'eau max aux nœuds
# ---------------------------------------------------------------------------
def ligne_eau_max(zb, zmax, ikle, h_sec):
    """
    Seuil de mise en eau et prolongement horizontal de la ligne d'eau.

    Retourne :
        zsurf     cote de surface libre max par nœud (prolongée en bordure)
        mouille   nœuds mouillés (Hmax > h_sec)
        tri_part  triangles avec au moins un nœud mouillé
        tri_coeur triangles entièrement mouillés
    """
    mouille = (zmax - zb) > h_sec
    if not mouille.any():
        raise ValueError(f"Aucun nœud avec Hmax > {h_sec} m.")
    log.info("Nœuds mouillés (Hmax > %.3f m) : %d / %d", h_sec, mouille.sum(), mouille.size)

    m_tri = mouille[ikle]
    tri_part = m_tri.any(axis=1)
    tri_coeur = m_tri.all(axis=1)

    somme = np.zeros_like(zmax)
    cnt = np.zeros_like(zmax)
    for k in range(3):
        for j in range(3):
            if j == k:
                continue
            sel = tri_part & ~m_tri[:, k] & m_tri[:, j]
            np.add.at(somme, ikle[sel, k], zmax[ikle[sel, j]])
            np.add.at(cnt, ikle[sel, k], 1.0)

    zsurf = zmax.copy()
    bord = ~mouille & (cnt > 0)
    zsurf[bord] = somme[bord] / cnt[bord]
    log.info("Nœuds secs en bordure (ligne d'eau prolongée) : %d", bord.sum())
    return zsurf, mouille, tri_part, tri_coeur


# ---------------------------------------------------------------------------
# Raster
# ---------------------------------------------------------------------------
def lire_lidar(chemin, bbox):
    """Lit le LiDAR sur la bbox (xmin, ymin, xmax, ymax). Retourne arr, transform, crs."""
    xmin, ymin, xmax, ymax = bbox
    with rasterio.open(chemin) as src:
        tr = src.transform
        if tr.b != 0 or tr.d != 0:
            raise ValueError("Raster tourné non pris en charge.")
        if abs(abs(tr.a) - abs(tr.e)) > 1e-9:
            raise ValueError("Pixels non carrés non pris en charge.")
        r0, c0 = src.index(xmin, ymax)
        r1, c1 = src.index(xmax, ymin)
        r0, c0 = max(r0, 0), max(c0, 0)
        r1, c1 = min(r1, src.height - 1), min(c1, src.width - 1)
        if r1 < r0 or c1 < c0:
            raise ValueError("Le LiDAR ne recouvre pas le maillage : vérifier "
                             "les systèmes de coordonnées.")
        win = Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)
        arr = src.read(1, window=win, masked=True).astype(np.float32).filled(np.nan)
        tr_w = src.window_transform(win)
        crs = src.crs
    arr[arr < -1000] = np.nan                     # nodata non déclaré
    log.info("LiDAR lu : %d x %d px (%.2f m), %.1f %% valide.",
             arr.shape[1], arr.shape[0], tr_w.a, 100 * np.isfinite(arr).mean())
    return arr, tr_w, crs


def interp_grille(tri, valeurs, tr, H, W, bloc=512):
    """Interpolation linéaire sur triangles vers la grille, par blocs de lignes."""
    interp = LinearTriInterpolator(tri, valeurs)
    out = np.full((H, W), np.nan, dtype=np.float32)
    xc = tr.c + (np.arange(W) + 0.5) * tr.a
    for r0 in range(0, H, bloc):
        r1 = min(r0 + bloc, H)
        yc = tr.f + (np.arange(r0, r1) + 0.5) * tr.e
        xx, yy = np.meshgrid(xc, yc)
        out[r0:r1] = interp(xx, yy).filled(np.nan)
    return out


def ecrire(chemin, arr, profil, dtype="float32", nodata=NODATA):
    p = dict(profil, dtype=dtype, nodata=nodata)
    if dtype == "uint8":
        data = arr.astype(np.uint8)
    else:
        data = np.where(np.isfinite(arr), arr, nodata).astype(dtype)
    with rasterio.open(chemin, "w", **p) as dst:
        dst.write(data, 1)
    log.info("Écrit : %s", chemin)


# ---------------------------------------------------------------------------
# Fusion MNT
# ---------------------------------------------------------------------------
def fusion_mnt(lidar, bathy, px, n_ero, s_min, l_trans, fill_dist):
    """
    LiDAR prioritaire, petits trous comblés par le LiDAR, grands trous par la
    bathy avec transition depuis le bord. Retourne z, classes.
    """
    valid = np.isfinite(lidar)
    if n_ero > 0:
        valid = binary_erosion(valid, iterations=n_ero, border_value=1)

    lab, n = label(~valid)
    surf = np.bincount(lab.ravel()) * px * px
    petit = (lab > 0) & (surf[lab] < s_min)
    grand = (lab > 0) & ~petit
    del lab
    log.info("Trous LiDAR : %d zones, %.0f m² comblés par le LiDAR, %.0f m² par la bathy.",
             n, petit.sum() * px * px, grand.sum() * px * px)

    z = np.where(valid, lidar, np.nan).astype(np.float32)

    # petits trous : interpolation du LiDAR environnant
    if petit.any():
        z_fill = fillnodata(np.where(valid, lidar, 0).astype(np.float32),
                            mask=valid.astype(np.uint8),
                            max_search_distance=fill_dist)
        z[petit] = z_fill[petit]
        del z_fill

    # grands trous : bathy avec transition depuis le bord
    if grand.any():
        ok = np.isfinite(z)
        if not ok.any():
            z[grand] = bathy[grand]
        else:
            d, (iy, ix) = distance_transform_edt(~ok, return_indices=True)
            d = (d * px).astype(np.float32)
            z_bord = z[iy, ix]
            del iy, ix
            if l_trans > 0:
                w = np.clip(d / l_trans, 0.0, 1.0)
            else:
                w = np.ones_like(d)
            z_trou = w * bathy + (1.0 - w) * z_bord
            bande = d < l_trans
            z_trou[bande] = np.fmin(z_trou[bande], z_bord[bande])
            z[grand] = z_trou[grand]

            contact = grand & (d <= 2 * px) & np.isfinite(bathy)
            if contact.any():
                log.info("Berge LiDAR - bathy au contact des trous : médiane %.2f m "
                         "(négatif = bathy trop haute en bord de lit).",
                         float(np.median(z_bord[contact] - bathy[contact])))
            del d, z_bord, w, z_trou

    z = np.where(np.isfinite(z), z, bathy).astype(np.float32)

    classes = np.zeros(z.shape, dtype=np.uint8)
    classes[valid] = 1
    classes[petit] = 2
    classes[grand] = 3
    return z, classes


# ---------------------------------------------------------------------------
# Hauteur d'eau
# ---------------------------------------------------------------------------
def hauteur_eau(zs, z, coeur, h_min):
    """H = Zs - MNT, seuil h_min, suppression des zones non connectées au cœur."""
    h = zs - z
    h[~(h > h_min)] = np.nan

    lab, n = label(np.isfinite(h))
    garder = np.unique(lab[coeur & (lab > 0)])
    isole = (lab > 0) & ~np.isin(lab, garder)
    log.info("Zones inondées : %d, dont %d non connectées supprimées.",
             n, n - garder.size)
    h[isole] = np.nan
    return h


# ---------------------------------------------------------------------------
# Programme principal
# ---------------------------------------------------------------------------
def chemin_local(p):
    """Accepte un chemin normal ou une URL file:/// (copiée depuis QGIS)."""
    p = str(p).strip().strip('"')
    if p.lower().startswith("file:"):
        from urllib.parse import unquote
        reste = unquote(p[5:]).lstrip("/")
        if len(reste) > 1 and reste[1] == ":":      # C:/... (Windows)
            p = reste
        else:                                       # /home/... (Linux)
            p = "/" + reste
    return Path(p)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Hmax TELEMAC-2D projeté sur MNT LiDAR HD.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("resultat", help="Fichier résultat TELEMAC (.slf/.res)")
    ap.add_argument("lidar", help="MNT LiDAR HD (GeoTIFF ou VRT)")
    ap.add_argument("-o", "--sortie", type=Path, default=Path("."), help="Dossier de sortie")
    ap.add_argument("--h-sec", type=float, default=0.02, help="Seuil nœud mouillé (m)")
    ap.add_argument("--h-min", type=float, default=0.01, help="Hauteur minimale conservée sur le raster (m)")
    ap.add_argument("--erosion", type=int, default=1, help="Pixels retirés en bord de trou LiDAR")
    ap.add_argument("--s-min", type=float, default=50.0, help="Surface max d'un trou comblé par le LiDAR (m²)")
    ap.add_argument("--l-trans", type=float, default=3.0, help="Largeur de transition bord de trou -> bathy (m)")
    ap.add_argument("--fill-dist", type=float, default=20.0, help="Distance max d'interpolation des petits trous (px)")
    ap.add_argument("--marge", type=float, default=20.0, help="Marge autour du maillage pour lire le LiDAR (m)")
    ap.add_argument("--emprise", type=float, nargs=4, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                    help="Restreindre le calcul à cette emprise")
    ap.add_argument("--var-fond", help="Nom exact de la variable fond")
    ap.add_argument("--var-surface", help="Nom exact de la variable surface libre")
    ap.add_argument("--var-zmax", help="Nom exact de la variable cote max")
    ap.add_argument("--sans-intermediaires", action="store_true",
                    help="N'écrire que hmax.tif")
    ap.add_argument("-v", "--verbeux", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbeux else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    a.sortie.mkdir(parents=True, exist_ok=True)

    # chemins collés depuis QGIS au format file:///C:/...
    for att in ("resultat", "lidar"):
        setattr(a, att, chemin_local(getattr(a, att)))
    for att in ("resultat", "lidar"):
        if not getattr(a, att).exists():
            sys.exit(f"Fichier introuvable : {getattr(a, att)}")

    # 1. TELEMAC
    x, y, ikle, zb, zmax = lire_telemac(a.resultat, a.var_fond, a.var_surface, a.var_zmax)

    # 2. Ligne d'eau max aux nœuds
    zsurf, mouille, tri_part, tri_coeur = ligne_eau_max(zb, zmax, ikle, a.h_sec)

    # Emprise de travail : nœuds des triangles mouillés + marge
    noeuds = np.unique(ikle[tri_part])
    bbox = [x[noeuds].min() - a.marge, y[noeuds].min() - a.marge,
            x[noeuds].max() + a.marge, y[noeuds].max() + a.marge]
    if a.emprise:
        bbox = [max(bbox[0], a.emprise[0]), max(bbox[1], a.emprise[1]),
                min(bbox[2], a.emprise[2]), min(bbox[3], a.emprise[3])]
    log.info("Emprise de calcul : %.1f %.1f %.1f %.1f", *bbox)

    # 3. LiDAR + bathy sur la grille
    lidar, tr, crs = lire_lidar(a.lidar, bbox)
    H, W = lidar.shape
    px = abs(tr.a)
    profil = dict(driver="GTiff", width=W, height=H, count=1, crs=crs, transform=tr,
                  compress="deflate", tiled=True, blockxsize=256, blockysize=256,
                  BIGTIFF="IF_SAFER")

    log.info("Interpolation de la bathy du modèle...")
    bathy = interp_grille(Triangulation(x, y, ikle), zb, tr, H, W)

    log.info("Fusion du MNT...")
    z, classes = fusion_mnt(lidar, bathy, px, a.erosion, a.s_min, a.l_trans, a.fill_dist)
    del lidar, bathy
    if not a.sans_intermediaires:
        ecrire(a.sortie / "mnt_fusion.tif", z, profil)
        ecrire(a.sortie / "classes_fusion.tif", classes, profil, dtype="uint8", nodata=0)
    del classes

    # 4. Surface libre max sur la grille
    log.info("Interpolation de la surface libre max...")
    tri_s = Triangulation(x, y, ikle)
    tri_s.set_mask(~tri_part)
    zs = interp_grille(tri_s, zsurf, tr, H, W)
    if not a.sans_intermediaires:
        ecrire(a.sortie / "zs_max.tif", zs, profil)

    tri_c = Triangulation(x, y, ikle)
    tri_c.set_mask(~tri_coeur)
    coeur = np.isfinite(interp_grille(tri_c, np.ones_like(zb), tr, H, W))

    # 5. Hauteur d'eau
    h = hauteur_eau(zs, z, coeur, a.h_min)
    ecrire(a.sortie / "hmax.tif", h, profil)

    n_inonde = np.isfinite(h).sum()
    if n_inonde:
        log.info("Surface inondée : %.2f ha | Hmax max : %.2f m | Hmax médian : %.2f m",
                 n_inonde * px * px / 1e4, float(np.nanmax(h)), float(np.nanmedian(h)))
    else:
        log.warning("Aucun pixel inondé après traitement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())