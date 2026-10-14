#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discretiser_pk.py
=================

Discretise une ligne (axe du lit mineur, ligne d'eau...) entre des bornes PK
dont l'espacement reel varie : chaque intervalle entre deux bornes
successives est decoupe en n segments de MEME LONGUEUR le long de la ligne.

Exemple : bornes 125 et 126 distantes de 1 100 m le long de la ligne, pas
nominal 100 m -> n = 10 segments de 110 m, points PK 125+000, 125+100, ...,
125+900, 126+000. Les PK des points sont des PK officiels ; la distance
reelle entre deux points est la longueur de l'intervalle divisee par n.

Usage :
    python discretiser_pk.py --ligne axe.gpkg --bornes bornes.gpkg --pas 100
    python discretiser_pk.py --config config.toml
Les options en ligne de commande restent prioritaires sur le fichier.

Dependances : numpy, pandas, geopandas, shapely>=2
"""

import argparse
import sys
import warnings

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, unary_union

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

NOMS_CHAMP_PK = ("pk", "pk_km", "pk_m", "borne", "num_borne", "numero")


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def charger_ligne(chemin, couche=None):
    """Ligne unique (troncons fusionnes) et son CRS."""
    gdf = gpd.read_file(chemin, layer=couche) if couche else gpd.read_file(chemin)
    geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
    if not geoms:
        raise ValueError(f"Aucune geometrie dans {chemin}")
    fusion = unary_union(geoms)
    if isinstance(fusion, MultiLineString):
        fusion = linemerge(fusion)
    if isinstance(fusion, MultiLineString):
        troncons = sorted(fusion.geoms, key=lambda g: g.length, reverse=True)
        warnings.warn(
            f"{chemin} : {len(troncons)} troncons non connectes, seul le plus long est "
            f"conserve ({troncons[0].length:.0f} m sur {sum(t.length for t in troncons):.0f} m).")
        fusion = troncons[0]
    if gdf.crs is not None and gdf.crs.is_geographic:
        raise ValueError("Systeme de coordonnees geographique : utiliser un systeme projete "
                         "metrique (ex. EPSG:2154).")
    return LineString(fusion.coords), gdf.crs


def charger_bornes(args, ligne, crs):
    """Bornes projetees sur la ligne, triees, PK en metres."""
    gdf = gpd.read_file(args.bornes, layer=args.couche_bornes) if args.couche_bornes \
        else gpd.read_file(args.bornes)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError(f"Aucune borne dans {args.bornes}")
    if crs is not None and gdf.crs is not None and gdf.crs != crs:
        gdf = gdf.to_crs(crs)
    elif gdf.crs is None and crs is not None:
        warnings.warn(f"{args.bornes} n'a pas de systeme de coordonnees : suppose identique "
                      f"a celui de la ligne.")

    champ = args.champ_pk
    if champ is None:
        par_nom = {c.lower(): c for c in gdf.columns if c != "geometry"}
        champ = next((par_nom[n] for n in NOMS_CHAMP_PK if n in par_nom), None)
    if champ is None or champ not in gdf.columns:
        raise ValueError(
            f"Champ du PK introuvable dans {args.bornes} : preciser --champ-pk "
            f"(champs disponibles : {', '.join(c for c in gdf.columns if c != 'geometry')})")
    valeurs = pd.to_numeric(gdf[champ].astype(str).str.replace(",", "."), errors="coerce")
    if valeurs.isna().any():
        raise ValueError(f"Valeurs non numeriques dans le champ {champ} : "
                         f"{gdf.loc[valeurs.isna(), champ].tolist()}")

    points = [g if g.geom_type == "Point" else g.centroid for g in gdf.geometry]
    s = np.array([ligne.project(p) for p in points])
    dist = np.array([ligne.distance(p) for p in points])
    pk = valeurs.to_numpy(dtype=float) * (1000.0 if args.unite_bornes == "km" else 1.0)
    noms = gdf[champ].astype(str).to_numpy()

    # Bornes au-dela des extremites : elles s'empileraient sur l'extremite
    tol = 0.5
    hors = (s <= tol) | (s >= ligne.length - tol)
    if hors.any():
        print(f"  /!\\ {int(hors.sum())} borne(s) au-dela des extremites de la ligne, "
              f"ecartee(s) : {', '.join(noms[hors])}")
        s, pk, noms, dist = s[~hors], pk[~hors], noms[~hors], dist[~hors]
    if len(s) < 2:
        raise ValueError("Il faut au moins deux bornes le long de la ligne pour discretiser.")

    ordre = np.argsort(s)
    s, pk, noms, dist = s[ordre], pk[ordre], noms[ordre], dist[ordre]
    if np.any(np.diff(s) < 0.01):
        i = int(np.flatnonzero(np.diff(s) < 0.01)[0])
        raise ValueError(f"Bornes {noms[i]} et {noms[i + 1]} projetees au meme endroit de la ligne.")
    d = np.diff(pk)
    if not (np.all(d > 0) or np.all(d < 0)):
        i = int(np.argmax(np.sign(d) != np.sign(d[0]))) + 1
        raise ValueError(f"Les PK des bornes ne varient pas dans un seul sens le long de la "
                         f"ligne (probleme vers la borne {noms[i]}).")
    return s, pk, noms, dist


# ---------------------------------------------------------------------------
# Discretisation
# ---------------------------------------------------------------------------

def libelle_pk(pk_m):
    """125300.0 -> '125+300' (PK km + metres)."""
    signe = "-" if pk_m < 0 else ""
    m = round(abs(pk_m), 1)
    km = int(m // 1000)
    reste = m - km * 1000
    reste_txt = f"{reste:05.1f}" if abs(reste - round(reste)) > 1e-6 else f"{int(round(reste)):03d}"
    return f"{signe}{km}+{reste_txt}"


def discretiser(ligne, s_b, pk_b, noms_b, pas, n_fixe, prolonger):
    """Liste de dicts : un point par sommet de discretisation."""
    sens = 1.0 if pk_b[-1] > pk_b[0] else -1.0
    points = []

    def ajouter(s, pk, type_point, intervalle, longueur_segment, borne=""):
        p = ligne.interpolate(s)
        points.append({"pk_m": pk, "pk_km": pk / 1000.0, "pk_libelle": libelle_pk(pk),
                       "type": type_point, "borne": borne, "intervalle": intervalle,
                       "abscisse_ligne": s, "longueur_segment": longueur_segment,
                       "x": p.x, "y": p.y, "z": p.z if p.has_z else np.nan})

    # Avant la premiere borne : pas reel = pas nominal (1 m de PK par metre)
    if prolonger:
        avant = []
        s = s_b[0] - pas
        while s > 0:
            avant.append(s)
            s -= pas
        for s in reversed(avant):
            ajouter(s, pk_b[0] - sens * (s_b[0] - s), "prolongement", "avant", pas)

    bilan = []
    for i in range(len(s_b) - 1):
        longueur = s_b[i + 1] - s_b[i]
        dpk = pk_b[i + 1] - pk_b[i]
        n = n_fixe if n_fixe else max(1, int(round(abs(dpk) / pas)))
        seg = longueur / n
        nom_int = f"{noms_b[i]}-{noms_b[i + 1]}"
        for j in range(n):
            ajouter(s_b[i] + j * seg, pk_b[i] + j * dpk / n,
                    "borne" if j == 0 else "intermediaire", nom_int, seg,
                    borne=noms_b[i] if j == 0 else "")
        bilan.append({"intervalle": nom_int, "pk_officiel_m": abs(dpk), "longueur_ligne_m": longueur,
                      "n_segments": n, "longueur_segment_m": seg, "pas_pk_m": abs(dpk) / n,
                      "ecart_pct": 100 * (longueur - abs(dpk)) / abs(dpk)})
    ajouter(s_b[-1], pk_b[-1], "borne", f"{noms_b[-2]}-{noms_b[-1]}", np.nan, borne=noms_b[-1])

    if prolonger:
        s = s_b[-1] + pas
        while s < ligne.length:
            ajouter(s, pk_b[-1] + sens * (s - s_b[-1]), "prolongement", "apres", pas)
            s += pas

    return pd.DataFrame(points), pd.DataFrame(bilan)


# ---------------------------------------------------------------------------
# Configuration et programme principal
# ---------------------------------------------------------------------------

CLES_CONFIG = {
    "entrees": {"ligne": "ligne", "couche_ligne": "couche_ligne", "bornes": "bornes",
                "couche_bornes": "couche_bornes", "champ_pk": "champ_pk",
                "unite_bornes": "unite_bornes"},
    "discretisation": {"pas": "pas", "n_segments": "n_segments", "prolonger": "prolonger"},
    "sorties": {"gpkg": "sortie", "csv": "csv", "segments": "segments"},
}


def lire_config(chemin, parser):
    try:
        with open(chemin, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        parser.error(f"fichier de configuration introuvable : {chemin}")
    except tomllib.TOMLDecodeError as e:
        parser.error(f"fichier de configuration invalide ({chemin}) : {e}")
    valeurs = {}
    for section, contenu in cfg.items():
        if section not in CLES_CONFIG or not isinstance(contenu, dict):
            parser.error(f"section inconnue : [{section}] (attendues : {', '.join(CLES_CONFIG)})")
        for cle, valeur in contenu.items():
            if cle not in CLES_CONFIG[section]:
                parser.error(f"cle inconnue : [{section}] {cle} "
                             f"(attendues : {', '.join(CLES_CONFIG[section])})")
            valeurs[CLES_CONFIG[section][cle]] = valeur
    return valeurs


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Discretisation d'une ligne en segments de meme longueur entre bornes PK",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--config", help="Fichier de configuration TOML (voir config_exemple.toml)")
    p.add_argument("--ligne", help="Ligne a discretiser (axe du lit mineur...) - obligatoire")
    p.add_argument("--couche-ligne", default=None)
    p.add_argument("--bornes", help="Couche de points des bornes PK - obligatoire")
    p.add_argument("--couche-bornes", default=None)
    p.add_argument("--champ-pk", default=None,
                   help="Champ du PK des bornes (defaut : pk, pk_km, borne... detecte)")
    p.add_argument("--unite-bornes", choices=["km", "m"], default="km")
    p.add_argument("--pas", type=float, default=100.0,
                   help="Pas nominal en m de PK : n = PK entre bornes / pas, arrondi")
    p.add_argument("--n-segments", type=int, default=None,
                   help="Nombre fixe de segments par intervalle (remplace --pas)")
    p.add_argument("--prolonger", action="store_true",
                   help="Discretiser aussi avant la premiere et apres la derniere borne "
                        "(au pas nominal, 1 m de PK par metre)")
    p.add_argument("--sortie", default="points_pk.gpkg", help="GeoPackage de sortie")
    p.add_argument("--csv", default=None, help="CSV des points (optionnel)")
    p.add_argument("--segments", action="store_true",
                   help="Ecrire aussi les segments (couche 'segments_pk')")

    args_config, _ = p.parse_known_args(argv)
    if args_config.config:
        p.set_defaults(**lire_config(args_config.config, p))
    args = p.parse_args(argv)
    for nom in ("ligne", "bornes"):
        if not getattr(args, nom):
            p.error(f"--{nom} obligatoire (ou [entrees] {nom} dans la configuration)")
    if args.unite_bornes not in ("km", "m"):
        p.error(f"unite_bornes = {args.unite_bornes!r} invalide (km ou m)")
    if args.n_segments is not None and args.n_segments < 1:
        p.error("n_segments doit etre >= 1")
    if args.n_segments is None and args.pas <= 0:
        p.error("le pas doit etre strictement positif")

    ligne, crs = charger_ligne(args.ligne, args.couche_ligne)
    s_b, pk_b, noms_b, dist_b = charger_bornes(args, ligne, crs)
    points, bilan = discretiser(ligne, s_b, pk_b, noms_b, args.pas, args.n_segments, args.prolonger)

    # --- Ecritures ---------------------------------------------------------
    gdf = gpd.GeoDataFrame(points, geometry=gpd.points_from_xy(points["x"], points["y"]), crs=crs)
    if gdf["z"].isna().all():
        gdf = gdf.drop(columns="z")
    gdf.to_file(args.sortie, layer="points_pk", driver="GPKG")
    if args.segments:
        segs = []
        for a, b in zip(points.itertuples(), points.iloc[1:].itertuples()):
            morceau = ligne_entre(ligne, a.abscisse_ligne, b.abscisse_ligne)
            segs.append({"pk_debut": a.pk_m, "pk_fin": b.pk_m,
                         "libelle": f"{a.pk_libelle} -> {b.pk_libelle}",
                         "longueur": morceau.length, "geometry": morceau})
        gpd.GeoDataFrame(segs, crs=crs).to_file(args.sortie, layer="segments_pk", driver="GPKG")
    if args.csv:
        points.drop(columns=["z"] if points["z"].isna().all() else []).to_csv(
            args.csv, sep=";", index=False, float_format="%.3f")

    # --- Bilan -------------------------------------------------------------
    print("\n=== Discretisation ===")
    print(f"Ligne : {ligne.length:,.0f} m ; bornes utilisees : {len(s_b)} "
          f"(distance a la ligne max {dist_b.max():.1f} m)")
    print("  Intervalle        PK officiel  longueur ligne    n   segment")
    for r in bilan.itertuples():
        print(f"  {r.intervalle:<16} {r.pk_officiel_m:9.0f} m {r.longueur_ligne_m:11.1f} m "
              f"{r.n_segments:4d} {r.longueur_segment_m:8.2f} m  ({r.ecart_pct:+.1f} %)")
    if (bilan["ecart_pct"].abs() > 10).any():
        print("  /!\\ Ecart > 10 % sur un intervalle : verifier la borne ou le trace de la ligne.")
    print(f"{len(points)} points -> {args.sortie} (couche points_pk"
          f"{', segments_pk' if args.segments else ''})" + (f" et {args.csv}" if args.csv else ""))
    return points, bilan


def ligne_entre(ligne, s0, s1):
    """Morceau de la ligne entre les abscisses s0 et s1 (sommets intermediaires inclus)."""
    coords = [ligne.interpolate(s0).coords[0]]
    cumul = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(np.asarray(ligne.coords)[:, :2], axis=0).T))])
    for c, d in zip(ligne.coords, cumul):
        if s0 < d < s1:
            coords.append(c)
    coords.append(ligne.interpolate(s1).coords[0])
    return LineString(coords)


if __name__ == "__main__":
    main()
