#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
projection_crete_digue.py
=========================

Projection de la crete d'une digue (rive gauche ou droite) sur le profil en long
d'une riviere, en resolvant les deux difficultes classiques :

1) DECALAGE DES PK
   La digue meandre davantage que l'axe hydraulique : son abscisse curviligne
   propre n'est pas le PK du profil en long. On reporte donc chaque point de
   crete sur l'AXE DE REFERENCE unique (celui qui porte les PK du profil en
   long) par projection orthogonale (plus proche point), et non par abscisse
   curviligne propre.

2) DEPRESSIONS PARASITES
   Un profil en travers pose "au juge" sur la crete peut tomber dans une
   depression (orniere, passage busé, bruit LiDAR, ligne numerisee decalee).
   On echantillonne donc le MNT sur une BANDE transversale centree sur la ligne
   de crete numerisee (+/- demi-largeur) et on retient le MAXIMUM transversal :
   c'est la vraie crete locale.

   /!\ Le maximum est pris UNIQUEMENT dans le sens transversal. Aucun lissage
   n'est applique dans le sens longitudinal : un point bas reel de la crete est
   un point faible (surverse) qu'il faut conserver.

Sorties :
  - crete_points.csv    : un enregistrement par point densifie (PK, deport, Z, QA)
  - profil_crete_pk.csv : profil agrege par pas de PK (min / median / max)
  - qa_points.gpkg      : couche de controle (points de crete retenus + drapeaux)
  - qa_bandes.gpkg      : bandes transversales echantillonnees (controle visuel)
  - profil_en_long.png  : figure

Dependances : numpy, pandas, geopandas, shapely>=2, rasterio, matplotlib

Exemple :
  python projection_crete_digue.py --axe axe_profil_long.shp --crete crete_digue_RD.shp \
      --mnt mnt_lidar.tif --pas-crete 0.5 --demi-largeur 4 --pas-transversal 0.25 \
      --lissage-axe 50 --pas-pk 5 --angle-max 70 --longueur-min-retour 10 --retours exclure
"""

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.transform import rowcol
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, unary_union

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker


# ---------------------------------------------------------------------------
# Utilitaires geometriques
# ---------------------------------------------------------------------------

def charger_ligne(chemin, couche=None):
    """Charge un fichier vecteur et retourne une LineString unique + CRS."""
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
            f"{chemin} : {len(troncons)} troncons non connectes. "
            f"Seul le plus long est conserve ({troncons[0].length:.0f} m sur "
            f"{sum(t.length for t in troncons):.0f} m). "
            "Corrigez la topologie si ce n'est pas voulu."
        )
        fusion = troncons[0]
    return LineString(fusion.coords), gdf.crs


def densifier(ligne, pas):
    """Retourne les coordonnees (N,2) rechantillonnees a pas constant."""
    s = np.arange(0.0, ligne.length, pas)
    s = np.append(s, ligne.length)
    return np.array([[p.x, p.y] for p in (ligne.interpolate(si) for si in s)]), s


def moyenne_glissante_xy(xy, n_fenetre):
    """Moyenne glissante sur les coordonnees (bords traites par replication)."""
    if n_fenetre < 3:
        return xy.copy()
    if n_fenetre % 2 == 0:
        n_fenetre += 1
    demi = n_fenetre // 2
    noyau = np.ones(n_fenetre) / n_fenetre
    lisse = np.empty_like(xy)
    for k in range(2):
        pad = np.pad(xy[:, k], demi, mode="edge")
        lisse[:, k] = np.convolve(pad, noyau, mode="valid")
    return lisse


def lisser_axe(ligne, longueur_lissage, pas=1.0):
    """
    Lisse l'axe de reference par moyenne glissante, EXTREMITES FIGEES.

    Indispensable si le rayon de courbure local de l'axe est inferieur a la
    distance axe-digue : sinon la projection orthogonale devient ambigue
    (plusieurs points de crete tombent sur le meme PK dans les concavites,
    et des trous apparaissent dans les convexites).

    Le poids du lissage est ramene progressivement a 0 sur une demi-fenetre a
    chaque bout. Sans cela la moyenne glissante deplace les points extremes de
    (demi-fenetre)/2 le long de la ligne (6,37 m pour une fenetre de 51 points),
    ce qui ampute l'axe et decale tous les PK d'une constante.
    """
    if longueur_lissage <= 0:
        return ligne
    xy, _ = densifier(ligne, pas)
    n_fenetre = max(3, int(round(longueur_lissage / pas)))
    lisse = moyenne_glissante_xy(xy, n_fenetre)

    n = len(xy)
    k = min(max(1, n_fenetre // 2), n // 2)
    poids = np.ones(n)
    rampe = np.linspace(0.0, 1.0, k, endpoint=False)
    poids[:k] = rampe
    poids[n - k:] = rampe[::-1]
    return LineString(xy * (1 - poids[:, None]) + lisse * poids[:, None])


def tangentes_normales(xy, n_fenetre_lissage=1):
    """Tangentes unitaires et normales unitaires (gauche) en chaque point."""
    xy_l = moyenne_glissante_xy(xy, n_fenetre_lissage) if n_fenetre_lissage >= 3 else xy
    t = np.gradient(xy_l, axis=0)
    norme = np.hypot(t[:, 0], t[:, 1])
    norme[norme == 0] = 1.0
    t /= norme[:, None]
    n = np.column_stack([-t[:, 1], t[:, 0]])
    return t, n


# ---------------------------------------------------------------------------
# Echantillonnage raster
# ---------------------------------------------------------------------------

def echantillonner_mnt(src, X, Y, bande=1):
    """
    Echantillonne le MNT aux coordonnees X, Y (memes formes).
    Retourne un tableau float avec NaN hors emprise / nodata.
    Lecture integrale en memoire si le raster est raisonnable, sinon sample().
    """
    forme = X.shape
    xs = X.ravel()
    ys = Y.ravel()

    if src.width * src.height <= 250_000_000:
        arr = src.read(bande, masked=True).astype("float64").filled(np.nan)
        lignes, cols = rowcol(src.transform, xs, ys, op=np.floor)
        lignes = np.asarray(lignes, dtype=np.int64)
        cols = np.asarray(cols, dtype=np.int64)
        valide = (lignes >= 0) & (lignes < src.height) & (cols >= 0) & (cols < src.width)
        z = np.full(xs.shape, np.nan)
        z[valide] = arr[lignes[valide], cols[valide]]
    else:
        nodata = src.nodata
        z = np.array([v[0] for v in src.sample(zip(xs, ys), bande)], dtype="float64")
        if nodata is not None:
            z[z == nodata] = np.nan

    return z.reshape(forme)


# ---------------------------------------------------------------------------
# Traitement principal
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Recalage des PK sur les bornes reelles
# ---------------------------------------------------------------------------

NOMS_CHAMP_PK = ("pk", "pk_km", "pk_m", "borne", "num_borne", "numero")


def charger_bornes(args, axe, crs_axe):
    """Projette les bornes sur l'axe brut.

    Renvoie (s_bornes, pk_bornes, etiquettes, distances) tries le long de
    l'axe, pk en metres. Les bornes doivent etre dans le meme ordre le long
    de l'axe que leurs PK (tous croissants ou tous decroissants).
    """
    gdf = gpd.read_file(args.bornes, layer=args.couche_bornes) if args.couche_bornes \
        else gpd.read_file(args.bornes)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError(f"Aucune borne dans {args.bornes}")
    if crs_axe is not None and gdf.crs is not None and gdf.crs != crs_axe:
        gdf = gdf.to_crs(crs_axe)

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
    s = np.array([axe.project(p) for p in points])
    dist = np.array([axe.distance(p) for p in points])
    pk = valeurs.to_numpy(dtype=float) * (1000.0 if args.unite_bornes == "km" else 1.0)
    etiquettes = gdf[champ].astype(str).to_numpy()

    ordre = np.argsort(s)
    s, pk, etiquettes, dist = s[ordre], pk[ordre], etiquettes[ordre], dist[ordre]
    if np.any(np.diff(s) <= 0):
        raise ValueError("Deux bornes se projettent au meme endroit de l'axe : "
                         "verifier les doublons ou une borne mal placee.")
    if len(pk) > 1:
        d = np.diff(pk)
        if not (np.all(d > 0) or np.all(d < 0)):
            i = int(np.argmax(np.sign(d) != np.sign(d[0]))) + 1
            raise ValueError(
                f"Les PK des bornes ne varient pas dans un seul sens le long de l'axe "
                f"(probleme vers la borne {etiquettes[i]}) : borne mal placee, mal "
                f"numerotee, ou trop loin de l'axe.")
    return s, pk, etiquettes, dist


def recaler_pk(s, s_b, pk_b):
    """PK officiel (m) des abscisses s de l'axe brut.

    Entre deux bornes : interpolation lineaire (l'ecart reel entre bornes est
    respecte). Avant la premiere et apres la derniere : 1 m de PK par metre
    d'axe, a partir de la borne la plus proche.
    Renvoie aussi le facteur local dPK/ds (1 hors bornes).
    """
    sens = 1.0 if len(pk_b) < 2 or pk_b[-1] > pk_b[0] else -1.0
    if len(s_b) == 1:
        return pk_b[0] + sens * (s - s_b[0]), np.ones_like(s)
    pk = np.interp(s, s_b, pk_b)
    avant, apres = s < s_b[0], s > s_b[-1]
    pk[avant] = pk_b[0] + sens * (s[avant] - s_b[0])
    pk[apres] = pk_b[-1] + sens * (s[apres] - s_b[-1])
    pentes = np.abs(np.diff(pk_b) / np.diff(s_b))
    i = np.clip(np.searchsorted(s_b, s) - 1, 0, len(pentes) - 1)
    facteur = pentes[i]
    facteur[avant | apres] = 1.0
    return pk, facteur


def traiter(args):
    os.makedirs(args.sortie, exist_ok=True)

    # --- 1. Chargement -----------------------------------------------------
    axe, crs_axe = charger_ligne(args.axe, args.couche_axe)
    crete, crs_crete = charger_ligne(args.crete, args.couche_crete)

    if crs_axe != crs_crete:
        raise ValueError(f"CRS differents : axe {crs_axe} / crete {crs_crete}")
    if crs_axe is not None and crs_axe.is_geographic:
        raise ValueError("CRS geographique detecte. Utilisez un SCR projete metrique "
                         "(ex. EPSG:2056 pour la Suisse, EPSG:2154 pour la France).")

    if args.inverser_axe:
        axe = LineString(list(axe.coords)[::-1])

    axe_ref = lisser_axe(axe, args.lissage_axe)

    with rasterio.open(args.mnt) as src:
        crs_mnt = src.crs
        if args.crs_mnt:
            try:
                crs_mnt = rasterio.crs.CRS.from_user_input(args.crs_mnt)
            except Exception as e:
                warnings.warn(
                    f"Impossible de resoudre --crs-mnt {args.crs_mnt} ({e}).\n"
                    "  -> Installation PROJ incoherente. La verification de CRS est "
                    "ignoree ; l'echantillonnage utilise la transformation affine du "
                    "GeoTIFF et reste correct si les donnees sont bien dans le meme "
                    "repere. Voir --ignorer-crs.")
                crs_mnt = None
        if crs_axe is not None and crs_mnt is not None and not args.ignorer_crs:
            try:
                identiques = (crs_mnt == crs_axe
                              or (crs_mnt.to_epsg() is not None
                                  and crs_mnt.to_epsg() == crs_axe.to_epsg()))
            except Exception:
                identiques = True  # PROJ hors service : on ne bloque pas
            if not identiques:
                indice = ""
                if "LOCAL_CS" in (crs_mnt.to_wkt() or ""):
                    indice = ("\n  -> Le MNT porte un CRS degrade de type LOCAL_CS "
                              "(frequent avec les sorties PDAL) : les coordonnees sont "
                              "correctes mais le code EPSG est perdu.\n"
                              "     Corrigez l'en-tete :  gdal_edit -a_srs EPSG:xxxx mon_mnt.tif\n"
                              "     ou relancez avec     --crs-mnt EPSG:xxxx / --ignorer-crs")
                raise ValueError(
                    f"CRS du MNT ({crs_mnt.to_string()}) different des vecteurs "
                    f"({crs_axe.to_string()}).{indice}")
        res = abs(src.transform.a)

        # --- 2. Densification de la crete + geometrie locale ---------------
        xy, _ = densifier(crete, args.pas_crete)
        n_lissage = max(3, int(round(args.lissage_tangente / args.pas_crete)))
        tangentes, normales = tangentes_normales(xy, n_lissage)

        # --- 3. Bande transversale : le MAX = la vraie crete ---------------
        deports = np.arange(-args.demi_largeur, args.demi_largeur + 1e-9,
                            args.pas_transversal)
        X = xy[:, 0][:, None] + normales[:, 0][:, None] * deports[None, :]
        Y = xy[:, 1][:, None] + normales[:, 1][:, None] * deports[None, :]
        Z = echantillonner_mnt(src, X, Y)

    if args.pas_transversal > res:
        warnings.warn(f"--pas-transversal ({args.pas_transversal} m) > resolution MNT "
                      f"({res:.2f} m) : la crete peut etre sous-echantillonnee.")

    n_valides = np.sum(np.isfinite(Z), axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        z_crete = np.nanmax(Z, axis=1)
        i_max = np.nanargmax(np.where(np.isfinite(Z), Z, -np.inf), axis=1)
    z_crete[n_valides == 0] = np.nan
    deport_crete = deports[i_max]
    deport_crete[n_valides == 0] = np.nan

    # Z brut sur la ligne numerisee (pour mesurer l'effet des depressions)
    i_zero = int(np.argmin(np.abs(deports)))
    z_ligne = Z[:, i_zero]

    # --- 4. Projection sur l'axe de reference -> PK ------------------------
    # L'axe LISSE ne sert qu'a rendre la projection non ambigue.
    s_lisse = np.array([axe_ref.project(Point(p)) for p in xy])
    proj = np.array([[axe_ref.interpolate(s).x, axe_ref.interpolate(s).y]
                     for s in s_lisse])
    deport_axe = np.hypot(xy[:, 0] - proj[:, 0], xy[:, 1] - proj[:, 1])

    # Les PK sont ensuite re-exprimes sur l'axe BRUT, celui qui porte les PK du
    # profil en long existant : le lissage raccourcit la ligne et introduirait
    # sinon une derive cumulee de plusieurs metres sur quelques kilometres.
    if args.lissage_axe > 0:
        pk = np.array([axe.project(Point(p)) for p in proj])
        derive = float(np.max(np.abs(pk - s_lisse)))
    else:
        pk = s_lisse
        derive = 0.0
    abscisse_axe = pk.copy()

    # PK officiels : recales sur les bornes (ecart variable entre bornes), ou
    # simple decalage a l'origine
    bornes = None
    if args.bornes:
        s_b, pk_b, etiq_b, dist_b = charger_bornes(args, axe, crs_axe)
        pk, facteur_pk = recaler_pk(abscisse_axe, s_b, pk_b)
        bornes = pd.DataFrame({"borne": etiq_b, "pk_officiel": pk_b,
                               "abscisse_axe": s_b, "distance_axe": dist_b})
    else:
        pk = pk + args.pk_origine
        facteur_pk = np.ones_like(pk)

    # Angle local entre la tangente de la crete et celle de l'axe.
    # 0 deg = crete parallele a la riviere (cas nominal).
    # 90 deg = crete perpendiculaire : un long lineaire de digue s'ecrase sur
    # quelques metres de PK (retour de digue, ouvrage transverse, confluence).
    # La projection reste juste, c'est la REPRESENTATION en profil en long qui
    # degenere : ces tronçons doivent etre identifies, pas noyes dans le profil.
    xy_axe, s_axe = densifier(axe_ref, 1.0)
    t_axe, _ = tangentes_normales(xy_axe, 5)
    idx_axe = np.clip(np.searchsorted(s_axe, s_lisse), 0, len(t_axe) - 1)
    cos_angle = np.abs(np.sum(tangentes * t_axe[idx_axe], axis=1))
    angle_axe = np.degrees(np.arccos(np.clip(cos_angle, 0.0, 1.0)))

    # --- 5. Controle qualite ----------------------------------------------
    df = pd.DataFrame({
        "pk": pk,
        "abscisse_axe": abscisse_axe,
        "x": xy[:, 0], "y": xy[:, 1],
        "z_crete": z_crete,
        "z_ligne_numerisee": z_ligne,
        "deport_crete": deport_crete,
        "distance_axe": deport_axe,
        "angle_axe": angle_axe,
        "n_pixels_valides": n_valides,
        "facteur_pk": facteur_pk,
    })
    df["gain_vs_ligne"] = df["z_crete"] - df["z_ligne_numerisee"]

    df["qa_perpendiculaire"] = df["angle_axe"] > args.angle_max
    df["qa_hors_bande"] = np.abs(df["deport_crete"]) >= args.demi_largeur - 1e-9
    df["qa_trous_mnt"] = df["n_pixels_valides"] < 0.5 * len(deports)
    df["qa_repli_pk"] = False
    ordre = np.sign(np.diff(pk, prepend=pk[0]))
    sens_dominant = 1.0 if np.nansum(ordre) >= 0 else -1.0
    df.loc[1:, "qa_repli_pk"] = (np.diff(pk) * sens_dominant) < 0
    if args.distance_max is not None:
        df["qa_hors_emprise"] = df["distance_axe"] > args.distance_max
    else:
        df["qa_hors_emprise"] = False
    # La crete deborde-t-elle de l'emprise de l'axe ? shapely sature alors la
    # projection a 0 ou a L : ces points s'empilent sur un meme PK, faux.
    tol = max(1e-6, 0.5 * args.pas_crete)
    df["qa_hors_axe"] = (s_lisse <= tol) | (s_lisse >= axe_ref.length - tol)
    df["qa_ok"] = ~(df["qa_hors_bande"] | df["qa_trous_mnt"] | df["qa_hors_axe"] |
                    df["qa_hors_emprise"]) & df["z_crete"].notna()

    # --- 5bis. Tronçons perpendiculaires (retours de digue) ----------------
    # Segmentation en plages contigues le long de la crete, avec leur propre
    # abscisse curviligne : un retour de digue est un ouvrage distinct, il se
    # profile le long de son propre axe, pas sur les PK de la riviere.
    plages = []
    masque = df["qa_perpendiculaire"].to_numpy().copy()
    df["id_retour"] = -1
    if masque.any():
        bornes = np.flatnonzero(np.diff(np.concatenate(([0], masque.view(np.int8), [0]))))
        for k, (i0, i1) in enumerate(zip(bornes[::2], bornes[1::2])):
            longueur = (i1 - i0) * args.pas_crete
            if longueur < args.longueur_min_retour:
                masque[i0:i1] = False   # bruit angulaire ponctuel, on ignore
                continue
            df.loc[df.index[i0:i1], "id_retour"] = k
            sous = df.iloc[i0:i1]
            plages.append({
                "id_retour": k,
                "longueur_crete": longueur,
                "pk_min": float(sous["pk"].min()),
                "pk_max": float(sous["pk"].max()),
                "z_min": float(sous["z_crete"].min()),
                "z_max": float(sous["z_crete"].max()),
                "angle_median": float(sous["angle_axe"].median()),
            })
        df["qa_perpendiculaire"] = masque
    plages = pd.DataFrame(plages)
    if not plages.empty:
        plages["compression"] = (plages["longueur_crete"] /
                                 (plages["pk_max"] - plages["pk_min"]).clip(lower=1e-6))

    if args.retours in ("exclure", "separer"):
        df["qa_ok"] &= ~df["qa_perpendiculaire"]

    # --- 6. Agregation par pas de PK --------------------------------------
    d = df[df["qa_ok"]].copy()
    if d.empty:
        raise RuntimeError("Aucun point valide apres controle qualite.")
    d["bin"] = np.floor((d["pk"] - d["pk"].min()) / args.pas_pk).astype(int)
    g = d.groupby("bin")
    prof = pd.DataFrame({
        "pk": g["pk"].mean(),
        "z_min": g["z_crete"].min(),
        "z_median": g["z_crete"].median(),
        "z_max": g["z_crete"].max(),
        "n_points": g["z_crete"].size(),
        "distance_axe_moy": g["distance_axe"].mean(),
        "angle_axe_median": g["angle_axe"].median(),
        "facteur_pk": g["facteur_pk"].mean(),
    })
    # Lineaire de crete reellement represente par chaque pas de PK.
    # Un taux de compression >> 1 signale que le profil comprime beaucoup de
    # digue sur peu de PK : le point bas affiche peut etre un ouvrage transverse.
    prof["lineaire_crete"] = prof["n_points"] * args.pas_crete
    # longueur reelle d'axe couverte par la tranche (pas_pk / facteur avec bornes)
    prof["compression"] = prof["lineaire_crete"] / (args.pas_pk / prof["facteur_pk"])
    prof["pk_du_min"] = g.apply(
        lambda x: x.loc[x["z_crete"].idxmin(), "pk"], include_groups=False
    ).sort_index().values
    prof = prof.sort_values("pk").reset_index(drop=True)

    # --- 7. Revanche eventuelle -------------------------------------------
    ligne_eau = None
    if args.ligne_eau:
        ligne_eau = pd.read_csv(args.ligne_eau)
        cols = {c.lower().strip(): c for c in ligne_eau.columns}
        c_pk = cols.get("pk") or list(ligne_eau.columns)[0]
        c_z = cols.get("z") or cols.get("z_eau") or list(ligne_eau.columns)[1]
        ligne_eau = ligne_eau[[c_pk, c_z]].rename(columns={c_pk: "pk", c_z: "z_eau"})
        ligne_eau = ligne_eau.sort_values("pk")
        prof["z_eau"] = np.interp(prof["pk"], ligne_eau["pk"], ligne_eau["z_eau"],
                                  left=np.nan, right=np.nan)
        prof["revanche_min"] = prof["z_min"] - prof["z_eau"]

    # --- 8. Ecritures ------------------------------------------------------
    f_pts = os.path.join(args.sortie, "crete_points.csv")
    f_prof = os.path.join(args.sortie, "profil_crete_pk.csv")
    df.to_csv(f_pts, index=False, float_format="%.3f")
    prof.to_csv(f_prof, index=False, float_format="%.3f")

    if bornes is not None:
        intervalles = pd.DataFrame({
            "borne_debut": bornes["borne"].to_numpy()[:-1],
            "borne_fin": bornes["borne"].to_numpy()[1:],
            "pk_officiel_m": np.abs(np.diff(bornes["pk_officiel"])),
            "longueur_axe_m": np.diff(bornes["abscisse_axe"]),
        })
        intervalles["ecart_m"] = intervalles["longueur_axe_m"] - intervalles["pk_officiel_m"]
        intervalles["ecart_pct"] = 100 * intervalles["ecart_m"] / intervalles["pk_officiel_m"]
        bornes.to_csv(os.path.join(args.sortie, "bornes_recalage.csv"),
                      index=False, float_format="%.3f")
        intervalles.to_csv(os.path.join(args.sortie, "bornes_intervalles.csv"),
                           index=False, float_format="%.3f")

    if not plages.empty:
        plages.to_csv(os.path.join(args.sortie, "retours_digue.csv"),
                      index=False, float_format="%.3f")
        if args.retours == "separer":
            det = df[df["id_retour"] >= 0].copy()
            det["abscisse_retour"] = det.groupby("id_retour").cumcount() * args.pas_crete
            det[["id_retour", "abscisse_retour", "pk", "x", "y", "z_crete",
                 "angle_axe"]].to_csv(
                os.path.join(args.sortie, "retours_digue_points.csv"),
                index=False, float_format="%.3f")

    gdf_pts = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(
            xy[:, 0] + normales[:, 0] * np.nan_to_num(deport_crete),
            xy[:, 1] + normales[:, 1] * np.nan_to_num(deport_crete)),
        crs=crs_axe)
    gdf_pts.to_file(os.path.join(args.sortie, "qa_points.gpkg"),
                    layer="crete_detectee", driver="GPKG")

    pas_bande = max(1, int(round(args.pas_qa_bandes / args.pas_crete)))
    bandes = [LineString([(X[i, 0], Y[i, 0]), (X[i, -1], Y[i, -1])])
              for i in range(0, len(xy), pas_bande)]
    gpd.GeoDataFrame({"pk": pk[::pas_bande][:len(bandes)]},
                     geometry=bandes, crs=crs_axe).to_file(
        os.path.join(args.sortie, "qa_bandes.gpkg"), layer="bandes", driver="GPKG")

    # --- 9. Figure ---------------------------------------------------------
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.fill_between(prof["pk"], prof["z_min"], prof["z_max"], alpha=0.25,
                    color="tab:brown", label="Enveloppe crete (min-max par bin PK)")
    ax.plot(prof["pk"], prof["z_median"], color="tab:brown", lw=1.2,
            label="Crete digue - mediane")
    ax.plot(prof["pk"], prof["z_min"], color="darkred", lw=1.0,
            label="Crete digue - enveloppe basse (dimensionnante)")
    if ligne_eau is not None:
        ax.plot(prof["pk"], prof["z_eau"], color="tab:blue", lw=1.5,
                label="Ligne d'eau")
    if not plages.empty:
        for _, r in plages.iterrows():
            ax.axvspan(r["pk_min"], r["pk_max"], color="0.5", alpha=0.30,
                       hatch="//", lw=0, zorder=0)
        ax.axvspan(np.nan, np.nan, color="0.5", alpha=0.30, hatch="//", lw=0,
                   label="Crete perpendiculaire a l'axe (retour de digue)")
    if bornes is not None:
        tol = args.pas_pk
        x_min, x_max = d["pk"].min() - tol, d["pk"].max() + tol
        for _, b in bornes.iterrows():
            if x_min <= b["pk_officiel"] <= x_max:
                ax.axvline(b["pk_officiel"], color="0.35", lw=0.8, ls=":", zorder=1)
                ax.annotate(f"PK {b['borne']}", (b["pk_officiel"], 1.0),
                            xycoords=("data", "axes fraction"), xytext=(2, -2),
                            textcoords="offset points", rotation=90,
                            va="top", ha="left", fontsize=8, color="0.25")
    unite = "km" if args.axe_km else "m"
    if args.axe_km:
        ax.xaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:g}"))
    ax.set_xlabel(f"PK ({unite})" + (" - recales sur les bornes" if bornes is not None
                                      else " sur l'axe de reference"))
    ax.set_ylabel("Cote (m)")
    ax.set_title("Profil en long - crete de digue projetee sur l'axe hydraulique")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    f_png = os.path.join(args.sortie, "profil_en_long.png")
    fig.savefig(f_png, dpi=150)
    plt.close(fig)

    # --- 10. Rapport -------------------------------------------------------
    print("\n=== Synthese ===")
    print(f"Longueur crete densifiee : {crete.length:,.0f} m ({len(df)} points)")
    print(f"Longueur axe de reference: {axe.length:,.0f} m brut / "
          f"{axe_ref.length:,.0f} m lisse")
    print(f"Derive de PK corrigee    : {derive:.1f} m max "
          f"(PK exprimes sur l'axe brut)")
    print(f"PK couverts              : {df['pk'].min():.1f} -> {df['pk'].max():.1f} m")
    if bornes is not None:
        print(f"Bornes                   : {len(bornes)} (distance a l'axe max "
              f"{bornes['distance_axe'].max():.1f} m)")
        if len(bornes) > 1:
            print("  Intervalle          PK officiel   longueur axe    ecart")
            for _, r in intervalles.iterrows():
                print(f"  {r['borne_debut']:>8} -> {r['borne_fin']:<8} {r['pk_officiel_m']:9.0f} m "
                      f"{r['longueur_axe_m']:11.0f} m  {r['ecart_m']:+7.0f} m ({r['ecart_pct']:+.1f} %)")
            if (intervalles["ecart_pct"].abs() > 10).any():
                print("  /!\\ Ecart > 10 % sur un intervalle : verifier la position ou le "
                      "numero de la borne, ou le trace de l'axe.")
        if bornes["distance_axe"].max() > 200:
            print("  /!\\ Une borne est a plus de 200 m de l'axe : verifier qu'elle "
                  "appartient bien a ce canal.")
    print(f"Distance crete-axe       : {df['distance_axe'].min():.1f} / "
          f"{df['distance_axe'].mean():.1f} / {df['distance_axe'].max():.1f} m (min/moy/max)")
    print(f"Gain du max transversal  : mediane {df['gain_vs_ligne'].median():.2f} m, "
          f"max {df['gain_vs_ligne'].max():.2f} m")
    print(f"  -> {int((df['gain_vs_ligne'] > 0.10).sum())} points ou la ligne numerisee "
          f"tombait >10 cm sous la vraie crete (depressions evitees)")
    print(f"Points rejetes           : hors bande {int(df['qa_hors_bande'].sum())} | "
          f"trous MNT {int(df['qa_trous_mnt'].sum())} | "
          f"hors emprise {int(df['qa_hors_emprise'].sum())} | "
          f"hors axe {int(df['qa_hors_axe'].sum())}")
    if df["qa_hors_axe"].sum() > 0:
        print(f"  /!\\ {int(df['qa_hors_axe'].sum())} points de crete se projettent "
              "au-dela des extremites de l'axe : prolongez l'axe de reference, "
              "sinon ce lineaire de digue est perdu.")
    print(f"Replis de PK (meandres)  : {int(df['qa_repli_pk'].sum())} points "
          f"({100 * df['qa_repli_pk'].mean():.1f} %)")
    if not plages.empty:
        print(f"Tronçons perpendiculaires: {len(plages)} plage(s), "
              f"{plages['longueur_crete'].sum():.0f} m de crete "
              f"(mode : {args.retours})")
        for _, r in plages.iterrows():
            print(f"   PK {r['pk_min']:.0f}-{r['pk_max']:.0f} : "
                  f"{r['longueur_crete']:.0f} m de digue comprimes sur "
                  f"{r['pk_max'] - r['pk_min']:.0f} m de PK "
                  f"(x{r['compression']:.0f}), angle median {r['angle_median']:.0f} deg, "
                  f"z_min {r['z_min']:.2f}")
    if "compression" in prof and (prof["compression"] > 3).any():
        pire = prof.loc[prof["compression"].idxmax()]
        print(f"  /!\\ Compression max hors retours : x{pire['compression']:.1f} "
              f"au PK {pire['pk']:.0f}")
    if df["qa_repli_pk"].mean() > 0.02:
        print("  /!\\ Beaucoup de replis : augmentez --lissage-axe (rayon de courbure "
              "de l'axe < distance axe-digue).")
    if df["qa_hors_bande"].mean() > 0.05:
        print("  /!\\ Beaucoup de maxima en bord de bande : augmentez --demi-largeur "
              "ou recalez la ligne de crete numerisee.")
    if "revanche_min" in prof:
        print(f"Revanche minimale        : {prof['revanche_min'].min():.2f} m "
              f"au PK {prof.loc[prof['revanche_min'].idxmin(), 'pk']:.0f}")
    print(f"\nFichiers : {f_pts}\n           {f_prof}\n           {f_png}")

    return df, prof


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Projection de la crete de digue sur le profil en long d'une riviere",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--axe", required=True,
                   help="Vecteur de l'axe de reference portant les PK (ligne d'eau / axe hydraulique)")
    p.add_argument("--crete", required=True, help="Vecteur de la ligne de crete de digue")
    p.add_argument("--mnt", required=True, help="MNT (GeoTIFF/VRT) - MNT et non MNS")
    p.add_argument("--couche-axe", default=None)
    p.add_argument("--couche-crete", default=None)
    p.add_argument("--crs-mnt", default=None,
                   help="Force l'interpretation du CRS du MNT (ex. EPSG:2154) sans "
                        "reprojection - pour les rasters au CRS degrade (LOCAL_CS)")
    p.add_argument("--ignorer-crs", action="store_true",
                   help="Ignore la verification de coherence des CRS (a vos risques)")
    p.add_argument("--pas-crete", type=float, default=0.5,
                   help="Pas de densification le long de la crete (m)")
    p.add_argument("--demi-largeur", type=float, default=4.0,
                   help="Demi-largeur de la bande transversale echantillonnee (m)")
    p.add_argument("--pas-transversal", type=float, default=0.25,
                   help="Pas d'echantillonnage transversal (m), <= resolution MNT")
    p.add_argument("--lissage-tangente", type=float, default=10.0,
                   help="Longueur de lissage pour la direction locale de la crete (m)")
    p.add_argument("--lissage-axe", type=float, default=50.0,
                   help="Longueur de lissage de l'axe de reference (m), 0 = aucun")
    p.add_argument("--pas-pk", type=float, default=5.0,
                   help="Pas d'agregation du profil en long (m)")
    p.add_argument("--angle-max", type=float, default=60.0,
                   help="Angle max (deg) entre tangente de crete et tangente de l'axe "
                        "avant de considerer le tronçon comme perpendiculaire")
    p.add_argument("--longueur-min-retour", type=float, default=10.0,
                   help="Longueur min (m) d'une plage perpendiculaire pour etre "
                        "retenue comme retour de digue (filtre le bruit angulaire)")
    p.add_argument("--retours", choices=["signaler", "exclure", "separer"],
                   default="signaler",
                   help="Traitement des tronçons perpendiculaires : signaler = "
                        "conserves dans le profil mais hachures ; exclure = retires "
                        "du profil ; separer = retires et exportes avec leur propre "
                        "abscisse curviligne")
    p.add_argument("--distance-max", type=float, default=None,
                   help="Distance max crete-axe acceptee (m) ; au-dela le point est rejete")
    p.add_argument("--pk-origine", type=float, default=0.0,
                   help="PK a l'origine de l'axe (decalage additif) ; inutile avec --bornes")
    p.add_argument("--bornes", default=None,
                   help="Couche de points des bornes PK reelles : les PK sont recales "
                        "intervalle par intervalle (ecart variable entre bornes)")
    p.add_argument("--couche-bornes", default=None)
    p.add_argument("--champ-pk", default=None,
                   help="Champ portant le PK des bornes (defaut : pk, pk_km, borne...)")
    p.add_argument("--unite-bornes", choices=["km", "m"], default="km",
                   help="Unite du champ PK des bornes")
    p.add_argument("--axe-km", action="store_true",
                   help="Graduer l'axe des PK du graphique en km")
    p.add_argument("--inverser-axe", action="store_true",
                   help="Inverse le sens de l'axe (PK croissants vers l'amont)")
    p.add_argument("--ligne-eau", default=None,
                   help="CSV optionnel colonnes pk,z de la ligne d'eau -> calcul de revanche")
    p.add_argument("--pas-qa-bandes", type=float, default=25.0,
                   help="Pas d'export des bandes de controle (m)")
    p.add_argument("--sortie", default="./resultats")
    args = p.parse_args(argv)

    if args.pas_transversal <= 0 or args.pas_crete <= 0:
        sys.exit("Les pas doivent etre strictement positifs.")
    if args.bornes and args.pk_origine:
        sys.exit("--pk-origine et --bornes sont incompatibles : avec --bornes, "
                 "les PK viennent des bornes.")
    traiter(args)


if __name__ == "__main__":
    main()