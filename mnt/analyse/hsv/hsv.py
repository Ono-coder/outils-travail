#!/usr/bin/env python3
"""Calcul d'une loi Hauteur / Surface / Volume (HSV) à partir d'un MNT.

Le MNT (GeoTIFF) est découpé par l'emprise définie dans une couche GeoPackage,
puis, pour une série de cotes de remplissage, on calcule :

    - la surface inondée  : surface des pixels dont l'altitude est <= cote (en ha)
    - le volume stocké    : somme sur ces pixels de (cote - altitude) * aire_pixel (en hm³)

Exemple :
    python hsv.py mnt.tif emprise.gpkg --pas 0.10 --sortie hsv.csv --graphique hsv.png
"""

import argparse
import csv
import sys

import numpy as np

M2_PAR_HA = 1e4
M3_PAR_HM3 = 1e6
import geopandas as gpd
import rasterio
from rasterio.mask import mask


def lire_emprise(chemin_gpkg, couche=None, filtre=None, crs_cible=None):
    """Lit la couche d'emprise, applique un filtre éventuel et la reprojette."""
    gdf = gpd.read_file(chemin_gpkg, layer=couche)
    if filtre:
        gdf = gdf.query(filtre)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError("Aucune géométrie dans l'emprise (couche vide ou filtre trop restrictif).")
    if not gdf.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("La couche d'emprise doit contenir des polygones.")
    if crs_cible is not None:
        if gdf.crs is None:
            print("Attention : la couche d'emprise n'a pas de SCR, on suppose celui du MNT.",
                  file=sys.stderr)
            gdf = gdf.set_crs(crs_cible)
        elif gdf.crs != crs_cible:
            gdf = gdf.to_crs(crs_cible)
    return gdf


def extraire_altitudes(src, geometries, all_touched=False):
    """Retourne les altitudes valides du MNT à l'intérieur des géométries et l'aire d'un pixel."""
    donnees, transform = mask(src, geometries, crop=True, filled=False,
                              all_touched=all_touched, indexes=1)
    valeurs = np.ma.compressed(donnees).astype(np.float64)
    valeurs = valeurs[np.isfinite(valeurs)]
    aire_pixel = abs(transform.a * transform.e - transform.b * transform.d)
    return valeurs, aire_pixel


def loi_hsv(altitudes, aire_pixel, pas=None, cotes=None, cote_min=None, cote_max=None):
    """Calcule la loi HSV.

    Les cotes vont de cote_min (défaut : point le plus bas de l'emprise) à
    cote_max (défaut : point le plus haut), par pas de `pas`.
    Retourne une liste de tuples (cote, hauteur, surface en ha, volume en hm³).
    La hauteur est comptée depuis le point le plus bas de l'emprise et le volume
    est le volume total stocké sous la cote (y compris sous cote_min).
    """
    if altitudes.size == 0:
        raise ValueError("Aucun pixel valide du MNT dans l'emprise.")

    z = np.sort(altitudes)
    z_min, z_max = z[0], z[-1]

    if cotes is None:
        bas = z_min if cote_min is None else cote_min
        haut = z_max if cote_max is None else cote_max
        n = int(np.floor((haut - bas) / pas + 1e-9))
        cotes = bas + pas * np.arange(n + 1)
        if cotes[-1] < haut - 1e-9:
            cotes = np.append(cotes, haut)
    cotes = np.asarray(cotes, dtype=np.float64)

    # Somme cumulée pour un calcul vectorisé : pour une cote h,
    # k = nb de pixels <= h, volume = (k*h - somme(z[:k])) * aire_pixel
    somme_cumulee = np.concatenate(([0.0], np.cumsum(z)))
    k = np.searchsorted(z, cotes, side="right")
    surfaces = k * aire_pixel / M2_PAR_HA
    volumes = (k * cotes - somme_cumulee[k]) * aire_pixel / M3_PAR_HM3

    return [(float(c), float(c - z_min), float(s), float(v))
            for c, s, v in zip(cotes, surfaces, volumes)]


def ecrire_csv(chemin, resultats, separateur=";"):
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=separateur)
        w.writerow(["entite", "cote_m", "hauteur_m", "surface_ha", "volume_hm3"])
        for entite, lignes in resultats.items():
            for cote, h, s, v in lignes:
                w.writerow([entite, f"{cote:.3f}", f"{h:.3f}", f"{s:.4f}", f"{v:.6f}"])


def tracer(chemin, resultats, cote_min=None, cote_max=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_s, ax_v) = plt.subplots(1, 2, figsize=(11, 5), sharey=True)
    for entite, lignes in resultats.items():
        cotes = [l[0] for l in lignes]
        ax_s.plot([l[2] for l in lignes], cotes, label=str(entite))
        ax_v.plot([l[3] for l in lignes], cotes, label=str(entite))
    ax_s.set_xlabel("Surface (ha)")
    ax_s.set_ylabel("Cote (m)")
    ax_s.set_title("Loi cote / surface")
    ax_v.set_xlabel("Volume (hm³)")
    ax_v.set_title("Loi cote / volume")
    toutes_cotes = [l[0] for lignes in resultats.values() for l in lignes]
    ax_s.set_ylim(min(toutes_cotes) if cote_min is None else cote_min,
                  max(toutes_cotes) if cote_max is None else cote_max)
    for ax in (ax_s, ax_v):
        ax.set_xlim(left=0)
        ax.grid(True, alpha=0.3)
    if len(resultats) > 1:
        ax_v.legend()
    fig.tight_layout()
    fig.savefig(chemin, dpi=150)
    plt.close(fig)


def main(argv=None):
    p = argparse.ArgumentParser(description="Loi Hauteur / Surface / Volume d'un MNT dans une emprise GPKG.")
    p.add_argument("mnt", help="MNT au format GeoTIFF")
    p.add_argument("emprise", help="Couche d'emprise (GeoPackage)")
    p.add_argument("--couche", help="Nom de la couche dans le GeoPackage (défaut : la première)")
    p.add_argument("--filtre", help="Filtre attributaire pandas, ex. \"nom == 'Bassin A'\"")
    p.add_argument("--champ-id", help="Calcule une loi par entité selon ce champ "
                                      "(défaut : toutes les entités fusionnées)")
    p.add_argument("--pas", type=float, default=0.10, help="Pas de cote en m (défaut : 0.10)")
    p.add_argument("--cotes", type=float, nargs="+", help="Liste explicite de cotes (remplace --pas)")
    p.add_argument("--cote-min", type=float,
                   help="Borne basse de la retenue en m (défaut : altitude min dans l'emprise)")
    p.add_argument("--cote-max", type=float,
                   help="Borne haute de la retenue en m (défaut : altitude max dans l'emprise)")
    p.add_argument("--all-touched", action="store_true",
                   help="Inclut tous les pixels touchés par l'emprise (défaut : centre du pixel dans l'emprise)")
    p.add_argument("--sortie", default="hsv.csv", help="Fichier CSV de sortie (défaut : hsv.csv)")
    p.add_argument("--separateur", default=";", help="Séparateur CSV (défaut : ;)")
    p.add_argument("--graphique", help="Image PNG des courbes HSV (optionnel)")
    args = p.parse_args(argv)

    if args.cotes is None and args.pas <= 0:
        p.error("--pas doit être strictement positif")
    if args.cote_min is not None and args.cote_max is not None and args.cote_min >= args.cote_max:
        p.error("--cote-min doit être strictement inférieure à --cote-max")

    with rasterio.open(args.mnt) as src:
        if src.crs is None:
            print("Attention : le MNT n'a pas de SCR défini.", file=sys.stderr)
        gdf = lire_emprise(args.emprise, args.couche, args.filtre, src.crs)

        if args.champ_id:
            if args.champ_id not in gdf.columns:
                p.error(f"Champ '{args.champ_id}' absent de la couche "
                        f"(champs disponibles : {', '.join(c for c in gdf.columns if c != 'geometry')})")
            groupes = {str(k): g.geometry.tolist() for k, g in gdf.groupby(args.champ_id)}
        else:
            groupes = {"emprise": [gdf.geometry.union_all()]}

        resultats = {}
        for entite, geoms in groupes.items():
            altitudes, aire_pixel = extraire_altitudes(src, geoms, args.all_touched)
            if altitudes.size == 0:
                raise SystemExit(f"[{entite}] aucun pixel valide du MNT dans l'emprise.")
            if args.cote_max is not None and args.cote_max > altitudes.max():
                print(f"Attention [{entite}] : --cote-max ({args.cote_max:.2f} m) dépasse le point "
                      f"le plus haut de l'emprise ({altitudes.max():.2f} m), la surface plafonne "
                      f"au-delà.", file=sys.stderr)
            lignes = loi_hsv(altitudes, aire_pixel, pas=args.pas, cotes=args.cotes,
                             cote_min=args.cote_min, cote_max=args.cote_max)
            resultats[entite] = lignes
            print(f"[{entite}] {altitudes.size} pixels ({aire_pixel:g} m²/pixel), "
                  f"altitude min {altitudes.min():.2f} m, max {altitudes.max():.2f} m, "
                  f"surface totale {altitudes.size * aire_pixel / M2_PAR_HA:.4f} ha, "
                  f"volume à la cote {lignes[-1][0]:.2f} m : {lignes[-1][3]:.6f} hm³")

    ecrire_csv(args.sortie, resultats, args.separateur)
    print(f"Loi HSV écrite dans {args.sortie}")
    if args.graphique:
        bornes = (None, None) if args.cotes else (args.cote_min, args.cote_max)
        tracer(args.graphique, resultats, *bornes)
        print(f"Graphique écrit dans {args.graphique}")


if __name__ == "__main__":
    main()
