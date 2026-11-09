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
   libre Zmax. La variable Zmax est choisie par l'utilisateur dans la liste
   des variables du fichier (MAXZ « HIGH WATER MARK » / « COTE MAXIMUM »
   proposée par défaut), ou max de S sur les pas de sortie.
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
   (indice de triangle par pixel, calculé par rastérisation), puis
   H = Zs - MNT.
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

    Un menu liste les variables du résultat pour choisir Zmax. Pour un
    lancement sans menu (script, batch) : --var-zmax "HIGH WATER MARK"
    ou --non-interactif (détection automatique).

    Pour un LiDAR en dalles : gdalbuildvrt lidarhd.vrt dalles/*.tif
    Le raster est lu uniquement sur l'emprise du maillage (+ marge).

Prérequis
---------
    numpy, scipy, matplotlib, rasterio ; numba fortement conseillé
    (rastérisation des triangles, sinon localisation matplotlib très
    lente sur un grand maillage). L'environnement TELEMAC n'est pas
    nécessaire : le fichier Selafin (.slf/.res) est lu directement.
    Maillage et LiDAR dans le même système (ex. Lambert 93 / NGF-IGN69).

Mémoire
-------
    Compter environ 30 octets par pixel au pic (fusion du MNT ; la
    transformée de distance est calculée par tuiles). Emprise de
    5 km x 5 km à 0,5 m = 1e8 px ~ 3 Go. Au-delà, découper l'emprise
    avec --emprise.
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.fill import fillnodata
from rasterio.windows import Window
from scipy.ndimage import binary_erosion, distance_transform_edt, label
from matplotlib.tri import Triangulation

try:                                   # accélération (fortement conseillée)
    from numba import njit, prange
except ImportError:
    njit = None

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
    "zmax": ("HIGH WATER MARK", "MAXIMUM ELEV", "COTE MAXI"),
}

# Choix spécial du menu : max de la surface libre sur les pas de sortie
CHOIX_MAX_S = "__MAX_S__"


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


def verifier_var(varnames, nom, option):
    """Renvoie le nom du fichier correspondant à `nom` (casse et espaces ignorés)."""
    for v in varnames:
        if v.strip().upper() == nom.strip().upper():
            return v
    raise ValueError(f"Variable '{nom}' absente du résultat ({option}). Variables "
                     f"disponibles : {', '.join(v.strip() for v in varnames)}")


def choisir_var_zmax(varnames, defaut, v_s):
    """
    Demande à l'utilisateur la variable de cote max parmi celles du fichier.
    Renvoie un nom de variable, ou CHOIX_MAX_S pour le max de la surface libre.
    """
    choix = list(varnames)
    if v_s:
        choix.append(CHOIX_MAX_S)
    if defaut is not None:
        i_def = choix.index(defaut)
    elif v_s:
        i_def = len(choix) - 1
    else:
        i_def = None

    print("\nVariables du fichier résultat :")
    for i, v in enumerate(choix, 1):
        lib = (f"max de '{v_s.strip()}' sur les pas de sortie" if v == CHOIX_MAX_S
               else v.strip())
        print(f"  {i:2d}  {lib}{'   [proposé]' if i - 1 == i_def else ''}")

    invite = "Numéro de la variable Zmax (cote max de la surface libre)"
    invite += f" [{i_def + 1}] : " if i_def is not None else " : "
    while True:
        try:
            rep = input(invite).strip()
        except EOFError:
            rep = ""
        if not rep and i_def is not None:
            return choix[i_def]
        if rep.isdigit() and 1 <= int(rep) <= len(choix):
            return choix[int(rep) - 1]
        print(f"  Réponse invalide : entrer un numéro entre 1 et {len(choix)}.")


def lire_telemac(chemin, var_fond=None, var_surface=None, var_zmax=None, interactif=True):
    """Lit maillage, fond et Zmax. Retourne x, y, ikle (base 0), zb, zmax."""
    res = SelafinLecteur(chemin)
    try:
        noms = list(res.varnames)
        log.info("Variables du résultat : %s", ", ".join(v.strip() for v in noms))
        nrec = len(res.times)

        if var_fond:
            v_fond = verifier_var(noms, var_fond, "--var-fond")
        else:
            v_fond = trouver_var(noms, NOMS_VAR["fond"])
        if v_fond is None:
            raise ValueError("Variable de fond introuvable (utiliser --var-fond).")
        zb = np.asarray(res.get_data_value(v_fond, 0), dtype=np.float64)
        log.info("Fond lu dans '%s'.", v_fond.strip())

        if var_surface:
            v_s = verifier_var(noms, var_surface, "--var-surface")
        else:
            v_s = trouver_var(noms, NOMS_VAR["surface"])

        # Choix de la variable Zmax : option, sinon menu, sinon détection auto
        if var_zmax:
            v_zmax = verifier_var(noms, var_zmax, "--var-zmax")
        else:
            v_zmax = trouver_var(noms, NOMS_VAR["zmax"])
            if interactif and sys.stdin is not None and sys.stdin.isatty():
                v_zmax = choisir_var_zmax(noms, v_zmax, v_s)
            if v_zmax == CHOIX_MAX_S:
                v_zmax = None

        if v_zmax:
            zmax = np.asarray(res.get_data_value(v_zmax, nrec - 1), dtype=np.float64)
            log.info("Zmax lu dans '%s' (dernier enregistrement, %d / %d).",
                     v_zmax.strip(), nrec, nrec)
            # Contrôle : une cote max doit être >= fond ; sinon c'est
            # probablement une hauteur, une vitesse ou un temps
            sous_fond = (zmax < zb - 0.01).mean()
            if sous_fond > 0.5:
                log.warning("'%s' est sous le fond sur %.0f %% des nœuds : ce n'est "
                            "probablement pas une cote de surface libre.",
                            v_zmax.strip(), 100 * sous_fond)
        else:
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


# Rastérisation des triangles : chaque centre de pixel reçoit l'indice du
# triangle qui le contient (calcul fait une fois, réutilisé pour toutes les
# interpolations). Avec numba, parcours direct des triangles ; sinon,
# localisation par le TriFinder de matplotlib (beaucoup plus lent).
if njit is not None:
    @njit(cache=True)
    def _raster_triangles(xr, yr, ikle, a, e, idx):
        H, W = idx.shape
        eps = 1e-9
        for t in range(ikle.shape[0]):
            i0, i1, i2 = ikle[t, 0], ikle[t, 1], ikle[t, 2]
            xa, ya, xb, yb, xc, yc = xr[i0], yr[i0], xr[i1], yr[i1], xr[i2], yr[i2]
            det = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
            if det == 0.0:
                continue
            # colonnes / lignes dont le centre tombe dans la bbox du triangle
            c0 = max(int(np.ceil(min(xa, xb, xc) / a - 0.5 - eps)), 0)
            c1 = min(int(np.floor(max(xa, xb, xc) / a - 0.5 + eps)), W - 1)
            r0 = max(int(np.ceil(max(ya, yb, yc) / e - 0.5 - eps)), 0)
            r1 = min(int(np.floor(min(ya, yb, yc) / e - 0.5 + eps)), H - 1)
            tol = -eps * abs(det)
            for r in range(r0, r1 + 1):
                py = (r + 0.5) * e - yc
                for c in range(c0, c1 + 1):
                    qx = (c + 0.5) * a - xc
                    l1 = (yb - yc) * qx + (xc - xb) * py
                    l2 = (yc - ya) * qx + (xa - xc) * py
                    l3 = det - l1 - l2
                    if det > 0:
                        dedans = l1 >= tol and l2 >= tol and l3 >= tol
                    else:
                        dedans = l1 <= -tol and l2 <= -tol and l3 <= -tol
                    if dedans:
                        idx[r, c] = t

    @njit(parallel=True, cache=True)
    def _interp_index(idx, xr, yr, ikle, v, actif, a, e, out):
        H, W = idx.shape
        for r in prange(H):
            py0 = (r + 0.5) * e
            for c in range(W):
                t = idx[r, c]
                if t < 0 or not actif[t]:
                    continue
                i0, i1, i2 = ikle[t, 0], ikle[t, 1], ikle[t, 2]
                xa, ya, xb, yb, xc, yc = xr[i0], yr[i0], xr[i1], yr[i1], xr[i2], yr[i2]
                det = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
                qx = (c + 0.5) * a - xc
                py = py0 - yc
                l1 = ((yb - yc) * qx + (xc - xb) * py) / det
                l2 = ((yc - ya) * qx + (xa - xc) * py) / det
                out[r, c] = l1 * v[i0] + l2 * v[i1] + (1.0 - l1 - l2) * v[i2]


def localiser_triangles(x, y, ikle, tr, H, W, bloc=512):
    """Indice du triangle contenant chaque centre de pixel (-1 hors maillage)."""
    idx = np.full((H, W), -1, dtype=np.int32)
    if njit is not None:
        # coordonnées relatives au coin de la grille (précision en Lambert 93)
        _raster_triangles(x - tr.c, y - tr.f, ikle, tr.a, tr.e, idx)
        return idx
    log.info("numba absent : localisation par matplotlib (lent, 'pip install numba').")
    finder = Triangulation(x, y, ikle).get_trifinder()
    xc = tr.c + (np.arange(W) + 0.5) * tr.a
    for r0 in range(0, H, bloc):
        r1 = min(r0 + bloc, H)
        yc = tr.f + (np.arange(r0, r1) + 0.5) * tr.e
        xx, yy = np.meshgrid(xc, yc)
        idx[r0:r1] = finder(xx, yy)
    return idx


def interp_index(idx, x, y, ikle, valeurs, tr, actif=None, bloc=512):
    """
    Interpolation linéaire des valeurs nodales sur la grille, à partir des
    indices de triangles. `actif` (booléen par triangle) masque des triangles
    (pixels laissés à NaN).
    """
    H, W = idx.shape
    if actif is None:
        actif = np.ones(ikle.shape[0], dtype=bool)
    v = np.asarray(valeurs, dtype=np.float64)
    out = np.full((H, W), np.nan, dtype=np.float32)
    xr, yr = x - tr.c, y - tr.f
    if njit is not None:
        _interp_index(idx, xr, yr, ikle, v, actif, tr.a, tr.e, out)
        return out
    actif_ext = np.append(actif, False)        # idx = -1 -> dernier = False
    for r0 in range(0, H, bloc):
        r1 = min(r0 + bloc, H)
        t = idx[r0:r1]
        rr, cc = np.nonzero(actif_ext[t])
        tt = t[rr, cc]
        i0, i1, i2 = ikle[tt, 0], ikle[tt, 1], ikle[tt, 2]
        xa, ya, xb, yb, xc, yc = xr[i0], yr[i0], xr[i1], yr[i1], xr[i2], yr[i2]
        det = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
        qx = (cc + 0.5) * tr.a - xc
        py = (rr + r0 + 0.5) * tr.e - yc
        l1 = ((yb - yc) * qx + (xc - xb) * py) / det
        l2 = ((yc - ya) * qx + (xa - xc) * py) / det
        out[r0 + rr, cc] = l1 * v[i0] + l2 * v[i1] + (1.0 - l1 - l2) * v[i2]
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
def remplir_grands_trous(z, bathy, grand, px, l_trans, bloc=2048):
    """
    Remplit en place les pixels `grand` de z par la bathy, avec transition
    linéaire sur l_trans depuis le bord du LiDAR et plafonnement par la cote
    de berge dans cette bande.

    Au-delà de l_trans, le résultat ne dépend plus du LiDAR (bathy seule) :
    la transformée de distance est donc calculée par tuiles, avec un
    recouvrement couvrant la bande, ce qui borne la mémoire sans changer le
    résultat.
    """
    H, W = z.shape
    ok = np.isfinite(z)            # pixels sources (jamais modifiés ici)
    m = int(np.ceil(max(l_trans, 2 * px) / px)) + 2
    contacts = []

    # Chaque tuile n'écrit que ses pixels `grand` et ne lit que des pixels
    # `ok` (jamais modifiés) : traitement parallèle sans conflit.
    def tuile(j):
        r0, r1, c0, c1 = j
        g = grand[r0:r1, c0:c1]
        b = bathy[r0:r1, c0:c1]
        zt = z[r0:r1, c0:c1]                      # vue : écriture en place
        R0, R1 = max(r0 - m, 0), min(r1 + m, H)
        C0, C1 = max(c0 - m, 0), min(c1 + m, W)
        ok_t = ok[R0:R1, C0:C1]
        if not ok_t.any():                        # aucun LiDAR à portée
            zt[g] = b[g]
            return

        d, (iy, ix) = distance_transform_edt(~ok_t, return_indices=True)
        sl = (slice(r0 - R0, r1 - R0), slice(c0 - C0, c1 - C0))
        d = (d[sl] * px).astype(np.float32)
        z_bord = z[R0:R1, C0:C1][iy[sl], ix[sl]]
        del iy, ix
        if l_trans > 0:
            w = np.clip(d / l_trans, 0.0, 1.0)
        else:
            w = np.ones_like(d)
        z_trou = w * b + (1.0 - w) * z_bord
        bande = d < l_trans
        z_trou[bande] = np.fmin(z_trou[bande], z_bord[bande])
        zt[g] = z_trou[g]

        contact = g & (d <= 2 * px) & np.isfinite(b)
        if contact.any():
            contacts.append(z_bord[contact] - b[contact])

    en_parallele(tuile, tuiles(grand, bloc))
    if contacts:
        log.info("Berge LiDAR - bathy au contact des trous : médiane %.2f m "
                 "(négatif = bathy trop haute en bord de lit).",
                 float(np.median(np.concatenate(contacts))))


def combler_petits_trous(z, lidar, valid, petit, fill_dist, bloc=1024):
    """
    Comble en place les pixels `petit` de z par interpolation GDAL du LiDAR
    valide. Par tuiles avec un recouvrement couvrant la distance de recherche
    (résultat identique au calcul global, mais parallèle et sans grille
    complète supplémentaire).
    """
    H, W = z.shape
    m = int(np.ceil(fill_dist)) + 2

    def tuile(j):
        r0, r1, c0, c1 = j
        R0, R1 = max(r0 - m, 0), min(r1 + m, H)
        C0, C1 = max(c0 - m, 0), min(c1 + m, W)
        v = valid[R0:R1, C0:C1]
        src = np.where(v, lidar[R0:R1, C0:C1], 0).astype(np.float32)
        zf = fillnodata(src, mask=v.astype(np.uint8), max_search_distance=fill_dist)
        sl = (slice(r0 - R0, r1 - R0), slice(c0 - C0, c1 - C0))
        p = petit[r0:r1, c0:c1]
        z[r0:r1, c0:c1][p] = zf[sl][p]

    en_parallele(tuile, tuiles(petit, bloc))


def tuiles(masque, bloc):
    """Tuiles (r0, r1, c0, c1) de la grille contenant au moins un pixel du masque."""
    H, W = masque.shape
    return [(r0, min(r0 + bloc, H), c0, min(c0 + bloc, W))
            for r0 in range(0, H, bloc) for c0 in range(0, W, bloc)
            if masque[r0:r0 + bloc, c0:c0 + bloc].any()]


def en_parallele(f, taches):
    """Exécute f sur chaque tâche dans un pool de threads (numpy, scipy et
    GDAL libèrent le GIL). Nombre de threads borné pour la mémoire."""
    n = min(8, os.cpu_count() or 1, max(len(taches), 1))
    with ThreadPoolExecutor(n) as ex:
        for _ in ex.map(f, taches):            # propage les exceptions
            pass


def fusion_mnt(lidar, bathy, px, n_ero, s_min, l_trans, fill_dist):
    """
    LiDAR prioritaire, petits trous comblés par le LiDAR, grands trous par la
    bathy avec transition depuis le bord. Retourne z, classes.
    """
    valid = np.isfinite(lidar)
    if n_ero > 0:
        valid = binary_erosion(valid, iterations=n_ero, border_value=1)

    lab, n = label(~valid)
    est_petit = np.bincount(lab.ravel()) * px * px < s_min   # par zone
    est_petit[0] = False                                     # 0 = LiDAR valide
    petit = est_petit[lab]
    grand = (lab > 0) & ~petit
    del lab
    log.info("Trous LiDAR : %d zones, %.0f m² comblés par le LiDAR, %.0f m² par la bathy.",
             n, petit.sum() * px * px, grand.sum() * px * px)

    z = np.where(valid, lidar, np.nan).astype(np.float32)

    # petits trous : interpolation du LiDAR environnant
    if petit.any():
        combler_petits_trous(z, lidar, valid, petit, fill_dist)

    # grands trous : bathy avec transition depuis le bord
    if grand.any():
        remplir_grands_trous(z, bathy, grand, px, l_trans)

    nan = ~np.isfinite(z)
    z[nan] = bathy[nan]
    del nan

    classes = np.zeros(z.shape, dtype=np.uint8)
    classes[valid] = 1
    classes[petit] = 2
    classes[grand] = 3
    return z, classes


# ---------------------------------------------------------------------------
# Hauteur d'eau
# ---------------------------------------------------------------------------
def hauteur_eau(zs, z, coeur, h_min):
    """
    H = Zs - MNT, seuil h_min, suppression des zones non connectées au cœur.
    Calcul en place dans zs (économie d'une grille complète).
    """
    h = zs
    h -= z
    h[~(h > h_min)] = np.nan

    lab, n = label(np.isfinite(h))
    garder = np.zeros(n + 1, dtype=bool)       # table zone -> conservée
    garder[lab[coeur]] = True
    garder[0] = True                           # 0 = sec, déjà NaN
    log.info("Zones inondées : %d, dont %d non connectées supprimées.",
             n, n + 1 - garder.sum())
    h[~garder[lab]] = np.nan
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
    ap.add_argument("--var-zmax", help="Nom exact de la variable cote max "
                    "(sinon choix proposé dans un menu)")
    ap.add_argument("--non-interactif", action="store_true",
                    help="Pas de menu : Zmax détecté automatiquement ou --var-zmax")
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
    x, y, ikle, zb, zmax = lire_telemac(a.resultat, a.var_fond, a.var_surface, a.var_zmax,
                                        interactif=not a.non_interactif)

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
                  BIGTIFF="IF_SAFER", NUM_THREADS="ALL_CPUS")

    # Indice de triangle par pixel : recalculé après la fusion plutôt que
    # gardé en mémoire (rapide, et le pic mémoire est dans la fusion)
    log.info("Localisation des pixels dans le maillage...")
    idx = localiser_triangles(x, y, ikle, tr, H, W)
    log.info("Interpolation de la bathy du modèle...")
    bathy = interp_index(idx, x, y, ikle, zb, tr)
    del idx

    log.info("Fusion du MNT...")
    z, classes = fusion_mnt(lidar, bathy, px, a.erosion, a.s_min, a.l_trans, a.fill_dist)
    del lidar, bathy
    if not a.sans_intermediaires:
        ecrire(a.sortie / "mnt_fusion.tif", z, profil)
        ecrire(a.sortie / "classes_fusion.tif", classes, profil, dtype="uint8", nodata=0)
    del classes

    # 4. Surface libre max sur la grille
    log.info("Interpolation de la surface libre max...")
    idx = localiser_triangles(x, y, ikle, tr, H, W)
    zs = interp_index(idx, x, y, ikle, zsurf, tr, actif=tri_part)
    if not a.sans_intermediaires:
        ecrire(a.sortie / "zs_max.tif", zs, profil)

    coeur = np.append(tri_coeur, False)[idx]       # idx = -1 -> False
    del idx

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