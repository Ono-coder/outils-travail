"""
Affectation du coefficient de Strickler aux noeuds d'un maillage hydraulique
a partir des polygones Corine Land Cover 2018.

Entrees :
  - shapefile de noeuds (points)
  - shapefile CLC 2018 (polygones) avec un champ de code (code_18 par defaut)
  - shapefile optionnel d'emprise du lit mineur (polygone) pour ecraser
    la valeur CLC par un Strickler cale separement

Hypothese : noeuds et CLC sont dans le meme systeme de coordonnees.

Usage :
  python strickler.py --config config.toml

Sortie :
  - CSV avec trois colonnes : X, Y, Strickler
    Une translation finale (DX, DY) est appliquee a X et Y avant export
    pour ramener les coordonnees dans le repere cible (typiquement repere
    local TELEMAC).
"""

import sys
import argparse
from pathlib import Path
import geopandas as gpd
import pandas as pd

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# ---------------------------------------------------------------------------
# 1. Parametres : lus dans le fichier de configuration TOML (--config)
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(
    description="Strickler aux noeuds d'un maillage a partir de Corine Land Cover")
parser.add_argument("--config", required=True,
                    help="Fichier de configuration TOML (voir config_exemple.toml)")
CFG_PATH = parser.parse_args().config
with open(CFG_PATH, "rb") as f:
    CFG = tomllib.load(f)


def cfg(section, cle, defaut=...):
    """Valeur CFG[section][cle] ; erreur explicite si obligatoire et absente."""
    val = CFG.get(section, {}).get(cle, defaut)
    if val is ...:
        sys.exit(f"Cle manquante dans la configuration : [{section}] {cle}")
    return val


NODES_SHP   = Path(cfg("entrees", "noeuds_shp"))
CLC_SHP     = Path(cfg("entrees", "clc_shp"))
RIVER_SHP   = Path(cfg("entrees", "lit_mineur_shp")) if cfg("entrees", "lit_mineur_shp", None) else None
OUTPUT_CSV  = Path(cfg("sorties", "csv"))

CLC_CODE_FIELD = cfg("parametres", "champ_code_clc", "Code_18")   # champ contenant le code CLC
KS_RIVER       = float(cfg("parametres", "ks_lit_mineur", 35.0))  # Strickler cale pour le lit mineur

# Translation finale appliquee aux coordonnees a l'export :
#   X_export = X_shp + DX
#   Y_export = Y_shp + DY
# DX = DY = 0.0 pour ne rien translater.
DX = float(cfg("parametres", "dx", 0.0))
DY = float(cfg("parametres", "dy", 0.0))

# Separateur et nombre de decimales du CSV
CSV_SEP = cfg("sorties", "separateur", ";")
COORD_DECIMALS = int(cfg("sorties", "decimales_coord", 3))   # decimales pour X et Y
KS_DECIMALS    = int(cfg("sorties", "decimales_ks", 2))      # decimales pour le Strickler

# Table de correspondance code CLC -> Strickler (m^(1/3)/s)
# A ajuster selon la bibliographie retenue et l'emprise du modele :
# chaque code peut etre redefini dans la section [table_strickler] de la configuration.
KS_LOOKUP = {
    "111": 5,  #20,   # tissu urbain continu
    "112": 10, #22,   # tissu urbain discontinu
    "121": 13, #25,   # zones industrielles ou commerciales
    "122": 15, #30,   # reseaux routier et ferroviaire
    "123": 10,
    "124": 15, #30,   # aeroports
    "131": 10, #25,   # extraction de materiaux
    "132": 10, #25,   # decharges
    "133": 15, #25,   # chantiers
    "141": 20, #28,   # espaces verts urbains
    "142": 13, #28,   # equipements sportifs et de loisirs
    "211": 17, #30,   # terres arables hors perimetres d'irrigation
    "221": 15, #22,   # vignobles
    "222": 15, #22,   # vergers
    "231": 20, #28,   # prairies
    "242": 17, #25,   # systemes culturaux et parcellaires complexes
    "243": 17, #25,   # surfaces agricoles avec espaces naturels
    "311": 10, #12,   # forets de feuillus
    "312": 10, #12,   # forets de coniferes
    "313": 10, #12,   # forets melangees
    "321": 20, #25,   # pelouses et paturages naturels
    "322": 17, #18,   # landes et broussailles
    "324": 10, #15,   # forets et vegetation arbustive en mutation
    "422": 15,
    "423": 15,
    "412": 41,
    "511": 1,
    "512": 40,
    "521": 39,
    "522": 34,
    "523": 28,
}
KS_LOOKUP.update({str(k): v for k, v in CFG.get("table_strickler", {}).items()})

# ---------------------------------------------------------------------------
# 2. Lecture des donnees
# ---------------------------------------------------------------------------

print("Lecture des noeuds...")
nodes = gpd.read_file(NODES_SHP)
print(f"  {len(nodes)} noeuds, CRS = {nodes.crs}")

print("Lecture de la couche CLC...")
clc = gpd.read_file(CLC_SHP, columns=[CLC_CODE_FIELD, "geometry"])
print(f"  {len(clc)} polygones, CRS = {clc.crs}")

# Le code CLC est traite comme une chaine pour eviter les ambiguites de type
clc[CLC_CODE_FIELD] = clc[CLC_CODE_FIELD].astype(str)

# ---------------------------------------------------------------------------
# 3. Jointure spatiale noeuds <- polygones CLC
# ---------------------------------------------------------------------------

print("Jointure spatiale (within)...")
joined = gpd.sjoin(
    nodes[["geometry"]],
    clc[[CLC_CODE_FIELD, "geometry"]],
    how="left",
    predicate="within",
)

# Un noeud sur une limite peut etre attribue a plusieurs polygones :
# on ne garde que la premiere occurrence pour conserver le nombre initial.
n_before = len(joined)
joined = joined[~joined.index.duplicated(keep="first")]
n_dupes = n_before - len(joined)
if n_dupes:
    print(f"  {n_dupes} doublons supprimes (noeuds sur des limites de polygones)")

# ---------------------------------------------------------------------------
# 4. Affectation du Strickler via la table de correspondance
# ---------------------------------------------------------------------------

joined["Strickler"] = joined[CLC_CODE_FIELD].map(KS_LOOKUP)

# Diagnostic : codes CLC presents dans l'emprise mais absents de la table
codes_in_zone = set(joined[CLC_CODE_FIELD].dropna().unique())
codes_missing = sorted(codes_in_zone - set(KS_LOOKUP.keys()))
if codes_missing:
    print("  attention : codes CLC sans Strickler dans KS_LOOKUP :", codes_missing)

# ---------------------------------------------------------------------------
# 5. Ecrasement par la valeur calee dans le lit mineur
# ---------------------------------------------------------------------------

if RIVER_SHP is not None:
    print("Application de la rugosite calee pour le lit mineur...")
    river = gpd.read_file(RIVER_SHP)
    river_union = river.unary_union
    in_river = joined.geometry.within(river_union)
    joined.loc[in_river, "Strickler"] = KS_RIVER
    print(f"  {int(in_river.sum())} noeuds ecrases a Ks = {KS_RIVER}")

# ---------------------------------------------------------------------------
# 6. Construction de la table de sortie X / Y / Strickler avec translation
# ---------------------------------------------------------------------------

out = pd.DataFrame({
    "X": joined.geometry.x + DX,
    "Y": joined.geometry.y + DY,
    "Strickler": joined["Strickler"].values,
})

# Diagnostic final
n_null = out["Strickler"].isna().sum()
print(f"Noeuds avec Strickler defini : {len(out) - n_null} / {len(out)}")
print(f"Noeuds laisses a NULL        : {n_null}")
print(f"Translation appliquee        : DX = {DX}, DY = {DY}")

# ---------------------------------------------------------------------------
# 7. Export CSV
# ---------------------------------------------------------------------------

OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
out.to_csv(
    OUTPUT_CSV,
    sep=CSV_SEP,
    index=False,
    float_format=None,            # on gere les arrondis colonne par colonne
    columns=["X", "Y", "Strickler"],
    decimal=".",
    lineterminator="\n",
    # Formatage manuel pour controler les decimales par colonne :
    # on arrondit avant l'ecriture.
)

# Reecriture propre avec arrondis differencies par colonne
out_rounded = out.copy()
out_rounded["X"] = out_rounded["X"].round(COORD_DECIMALS)
out_rounded["Y"] = out_rounded["Y"].round(COORD_DECIMALS)
out_rounded["Strickler"] = out_rounded["Strickler"].round(KS_DECIMALS)
out_rounded.to_csv(OUTPUT_CSV, sep=CSV_SEP, index=False)

print(f"Ecrit : {OUTPUT_CSV}")