"""
Téléchargement des données climatologiques quotidiennes Météo-France
Source  : meteo.data.gouv.fr — dataset "Données climatologiques de base - quotidiennes"
          ID : 6569b51ae64326786e4e8e1a

Fonctionnalités :
  [MODE 1] FIND_STATIONS  — Trouve les N stations les plus proches d'un point
                            et génère une carte HTML interactive (OpenStreetMap)
  [MODE 2] DOWNLOAD       — Télécharge les données pour les stations listées
                            et génère un CSV consolidé + graphique HTML interactif
  [MODE 3] GAPS           — Analyse des lacunes sur le CSV consolidé existant :
                            heatmap annuelle (% manquant/station/an) + tableau
                            des plages continues de lacunes

Dépendances : pip install requests pandas plotly
"""

import re
import gzip
import argparse
import tomllib
import json
import math
import time
import logging
from pathlib import Path

import requests
import pandas as pd

# ============================================================================
#  CONFIGURATION — lue dans le fichier TOML passé via --config
#  (voir config_exemple.toml). Les valeurs ci-dessous sont les défauts
#  appliqués quand une clé est absente du fichier.
# ============================================================================

# Mode d'exécution :
#   "find"     → cherche les stations proches + carte HTML  (MODE 1)
#   "download" → télécharge les données + CSV + graphique   (MODE 2)
#   "gaps"     → analyse des lacunes sur le CSV existant     (MODE 3)
#   "both"     → enchaîne find + download
#   "all"      → enchaîne les trois modes
MODE: str = "both"

# [MODE 1] Recherche de stations proches — section [recherche]
REF_LAT: float | None = None           # point de référence (WGS84)
REF_LON: float | None = None
N_STATIONS_PROCHES: int = 25
RAYON_MAX_KM: float | None = None      # None = pas de limite

# [MODE 2] Téléchargement — section [telechargement]
STATION_IDS: list[str] = []            # NUM_POSTE Météo-France
VARIABLES_SOUHAITEES: list[str] | None = None   # None = toutes
INCLURE_COLONNES_QUALITE: bool = False
FORCE_REFRESH_LATEST: bool = True
VARIABLE_GRAPHIQUE: str = "RR"

# [MODE 3] Lacunes — section [lacunes]
VARIABLES_GAPS: list[str] | None = None         # None = toutes

# Répertoires de sortie — section [sorties]
OUTPUT_DIR = Path("meteo_output")
OUTPUT_CARTE = OUTPUT_DIR / "stations_proches.html"
OUTPUT_CSV = OUTPUT_DIR / "donnees_quotidiennes_consolidees.csv"
OUTPUT_GRAPH = OUTPUT_DIR / "chronique_journaliere.html"
OUTPUT_GAPS  = OUTPUT_DIR / "lacunes.html"
CACHE_DIR  = OUTPUT_DIR / "cache"


def charger_config(chemin: str) -> None:
    """Lit le fichier TOML et remplace les paramètres de configuration."""
    global MODE, REF_LAT, REF_LON, N_STATIONS_PROCHES, RAYON_MAX_KM
    global STATION_IDS, VARIABLES_SOUHAITEES, INCLURE_COLONNES_QUALITE
    global FORCE_REFRESH_LATEST, VARIABLE_GRAPHIQUE, VARIABLES_GAPS
    global OUTPUT_DIR, OUTPUT_CARTE, OUTPUT_CSV, OUTPUT_GRAPH, OUTPUT_GAPS, CACHE_DIR

    try:
        with open(chemin, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        raise SystemExit(f"Fichier de configuration introuvable : {chemin}")
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"Fichier de configuration invalide ({chemin}) : {e}")

    MODE = cfg.get("mode", MODE)

    rech = cfg.get("recherche", {})
    REF_LAT = rech.get("lat", REF_LAT)
    REF_LON = rech.get("lon", REF_LON)
    N_STATIONS_PROCHES = rech.get("n_stations", N_STATIONS_PROCHES)
    RAYON_MAX_KM = rech.get("rayon_max_km", RAYON_MAX_KM)

    tele = cfg.get("telechargement", {})
    # str() : les identifiants doivent garder leurs zéros initiaux
    STATION_IDS = [str(s) for s in tele.get("stations", STATION_IDS)]
    VARIABLES_SOUHAITEES = tele.get("variables", VARIABLES_SOUHAITEES)
    INCLURE_COLONNES_QUALITE = tele.get("inclure_colonnes_qualite", INCLURE_COLONNES_QUALITE)
    FORCE_REFRESH_LATEST = tele.get("force_refresh_latest", FORCE_REFRESH_LATEST)
    VARIABLE_GRAPHIQUE = tele.get("variable_graphique", VARIABLE_GRAPHIQUE)

    VARIABLES_GAPS = cfg.get("lacunes", {}).get("variables", VARIABLES_GAPS)

    OUTPUT_DIR = Path(cfg.get("sorties", {}).get("dossier", OUTPUT_DIR))
    OUTPUT_CARTE = OUTPUT_DIR / "stations_proches.html"
    OUTPUT_CSV = OUTPUT_DIR / "donnees_quotidiennes_consolidees.csv"
    OUTPUT_GRAPH = OUTPUT_DIR / "chronique_journaliere.html"
    OUTPUT_GAPS  = OUTPUT_DIR / "lacunes.html"
    CACHE_DIR  = OUTPUT_DIR / "cache"

    mode = MODE.strip().lower()
    if mode in ("find", "both", "all") and (REF_LAT is None or REF_LON is None):
        raise SystemExit("Mode find : renseigner [recherche] lat et lon dans la configuration")
    if mode in ("download", "both", "all") and not STATION_IDS:
        raise SystemExit("Mode download : renseigner [telechargement] stations dans la configuration")

# ============================================================================
#  CONSTANTES
# ============================================================================

DATASET_ID      = "6569b51ae64326786e4e8e1a"
API_URL         = f"https://www.data.gouv.fr/api/1/datasets/{DATASET_ID}/"
# URL du fichier de référence des postes (liste nationale)
# Jeu de données "Informations sur les stations Météo-France (métadonnées)" —
# ~14 700 stations, colonnes : id, name, long_name, lon, lat, alt, …
POSTES_URL      = "https://www.data.gouv.fr/api/1/datasets/r/5ec141e4-3650-4365-82b1-db459efe690c"
HEADERS         = {"User-Agent": "meteo-downloader/1.0 (open-data Météo-France)"}

_ID_COLS             = ["NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI", "AAAAMMJJ"]
_STATION_COL_CANDS   = ["NUM_POSTE", "POSTE", "NUM_STA"]
_DATE_COL_CANDS      = ["AAAAMMJJ", "DATE", "date"]
_DOM_TOM             = {"971","972","973","974","975","976","984","985","986","987","988"}

# Variables contenues dans les fichiers "RR-T-Vent" du dataset quotidien.
# Toutes les autres variables (INST, GLOT, UN/UX/UM, PMER, NEIGE…, ETP…)
# sont dans les fichiers "autres-parametres".
_GROUPE_RR_T_VENT = {
    "RR", "TN", "HTN", "TX", "HTX", "TM", "TNTXM", "TAMPLI", "TNSOL", "TN50", "DG",
    "FFM", "FF2M", "FXY", "DXY", "HXY", "FXI", "DXI", "HXI", "FXI3S",
}


def _groupes_requis(variables: list[str] | None) -> set[str] | None:
    """Détermine quels groupes de fichiers sont nécessaires pour les variables
    demandées. Retourne None si toutes les variables sont souhaitées
    (→ tous les groupes)."""
    if variables is None:
        return None
    groupes: set[str] = set()
    for v in variables:
        if v.upper() in _GROUPE_RR_T_VENT:
            groupes.add("rr-t-vent")
        else:
            groupes.add("autres-parametres")
    return groupes

# Métadonnées des variables pour les graphiques
_VAR_META: dict[str, dict] = {
    "RR":    {"label": "Précipitations (mm)",      "color": "#1a6fc4", "cumul": True,  "bar": True},
    "TN":    {"label": "T° minimale (°C)",          "color": "#4ea8e8", "cumul": False, "bar": False},
    "TX":    {"label": "T° maximale (°C)",          "color": "#e84b4b", "cumul": False, "bar": False},
    "TM":    {"label": "T° moyenne (°C)",           "color": "#e8943a", "cumul": False, "bar": False},
    "FFM":   {"label": "Vent moyen (m/s)",          "color": "#6ab187", "cumul": False, "bar": True},
    "FXY":   {"label": "Vent max instantané (m/s)", "color": "#2d6a4f", "cumul": False, "bar": True},
    "INST":  {"label": "Insolation (mn)",           "color": "#f4c430", "cumul": False, "bar": True},
    "UN":    {"label": "Humidité mini (%)",         "color": "#b5ead7", "cumul": False, "bar": False},
    "UX":    {"label": "Humidité maxi (%)",         "color": "#5e9e8a", "cumul": False, "bar": False},
    "UM":    {"label": "Humidité moy (%)",          "color": "#3a7d6e", "cumul": False, "bar": False},
    "GLOT":  {"label": "Rayonnement global (J/cm²)","color": "#ffb347", "cumul": False, "bar": True},
    "PMER":  {"label": "Pression mer (hPa)",        "color": "#9b59b6", "cumul": False, "bar": False},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ============================================================================
#  MODULE 1 — RECHERCHE DE STATIONS PROCHES
# ============================================================================

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique en km (formule de Haversine)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _read_postes_file(path: Path) -> pd.DataFrame | None:
    """
    Lit un fichier de référence des postes (CSV, CSV.gz ou GeoJSON) et
    normalise les noms de colonnes vers NUM_POSTE / NOM_USUEL / LAT / LON / ALTI.
    Retourne None si le fichier ne contient pas de couple de coordonnées
    exploitable.
    """
    opener = gzip.open if path.suffix == ".gz" else open

    # Lire un en-tête pour deviner le format (JSON vs CSV) et le séparateur
    try:
        with opener(path, "rt", encoding="utf-8-sig") as f:
            head = f.read(4096)
    except Exception as exc:
        log.warning(f"  Lecture impossible ({path.name}) : {exc}")
        return None

    stripped = head.lstrip()

    if stripped[:1] in ("{", "["):
        # --- Liste officielle des postes au format (Geo)JSON ---
        try:
            with opener(path, "rt", encoding="utf-8-sig") as f:
                data = json.load(f)
            feats = data.get("features", data) if isinstance(data, dict) else data
            rows = []
            for ft in feats:
                if not isinstance(ft, dict):
                    continue
                props = ft.get("properties", ft)
                geom  = ft.get("geometry", {}) or {}
                coords = geom.get("coordinates", [None, None]) if isinstance(geom, dict) else [None, None]
                lon, lat = (coords + [None, None])[:2]
                rows.append({**props, "_LON": lon, "_LAT": lat})
            df = pd.DataFrame(rows).astype(str)
        except Exception as exc:
            log.warning(f"  JSON illisible ({path.name}) : {exc}")
            return None
    else:
        # --- CSV : deviner le séparateur sur la 1ʳᵉ ligne ---
        first_line = head.split("\n", 1)[0]
        sep = ";" if first_line.count(";") >= first_line.count(",") else ","
        try:
            with opener(path, "rt", encoding="utf-8-sig") as f:
                df = pd.read_csv(f, sep=sep, dtype=str, low_memory=False)
        except Exception as exc:
            log.warning(f"  Parsing CSV impossible ({path.name}) : {exc}")
            return None

    # Normalisation des noms de colonnes (priorité : 1ʳᵉ correspondance gagnante,
    # pour éviter de créer plusieurs colonnes portant le même nom cible)
    alias_map = {
        "NUM_POSTE": ["num_poste", "id_station", "numer_sta", "num_sta", "id"],
        "NOM_USUEL": ["nom_usuel", "nom", "nom_station", "usuel", "name",
                      "long_name", "named_place", "alias"],
        "LAT":       ["lat", "latitude", "_lat"],
        "LON":       ["lon", "long", "longitude", "_lon"],
        "ALTI":      ["alti", "altitude", "alt"],
    }
    lower_cols = {str(c).strip().lower(): c for c in df.columns}
    rename: dict = {}
    for target, aliases in alias_map.items():
        if target in df.columns:
            continue  # déjà présent tel quel
        for a in aliases:
            if a in lower_cols:
                rename[lower_cols[a]] = target
                break
    df = df.rename(columns=rename)

    if {"LAT", "LON", "NUM_POSTE"} <= set(df.columns):
        return df

    log.warning(
        f"  {path.name} : colonnes coordonnées/poste absentes "
        f"(colonnes vues : {list(df.columns)[:10]})"
    )
    return None


def _load_postes_reference() -> pd.DataFrame | None:
    """
    Charge la liste de référence des postes Météo-France, dans l'ordre :
      1. Fichier postes déjà présent dans le cache
      2. Téléchargement direct du référentiel (POSTES_URL)
      3. Recherche d'une ressource « postes » dans le dataset via l'API
    """
    # 1) Cache local
    local_candidates = list(CACHE_DIR.glob("*poste*")) + list(CACHE_DIR.glob("*station*"))
    for local in local_candidates:
        if "anomalie" in local.name.lower():
            continue  # fichiers d'anomalies capteur, pas un référentiel postes
        if local.suffix in (".csv", ".gz", ".json", ".geojson"):
            df = _read_postes_file(local)
            if df is not None:
                log.info(f"Postes chargés depuis le cache : {local.name}")
                return df

    # 2) Téléchargement direct du référentiel (POSTES_URL)
    log.info("Téléchargement du référentiel des postes Météo-France...")
    local_path = CACHE_DIR / "postes_reference.csv"
    try:
        with requests.get(POSTES_URL, headers=HEADERS, timeout=120, stream=True) as r:
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "gzip" in ctype or POSTES_URL.endswith(".gz"):
                local_path = local_path.with_suffix(".csv.gz")
            elif "json" in ctype:
                local_path = local_path.with_suffix(".json")
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    f.write(chunk)
        df = _read_postes_file(local_path)
        if df is not None:
            log.info(f"Référentiel téléchargé : {len(df)} postes "
                     f"({local_path.name}).")
            return df
        log.warning("Le référentiel pointé par POSTES_URL ne contient pas de "
                    "colonnes LAT/LON/NUM_POSTE exploitables — vérifiez l'URL.")
    except Exception as exc:
        log.warning(f"Téléchargement direct du référentiel impossible : {exc}")

    # 3) Recherche d'une ressource « postes » dans le dataset via l'API
    log.info("Recherche d'une ressource « postes » dans le dataset...")
    try:
        resp = requests.get(API_URL, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        resources = resp.json().get("resources", [])
        for r in resources:
            title = (r.get("title") or "").lower()
            url   = (r.get("url")   or "").lower()
            fmt   = (r.get("format") or "").lower()
            if any(k in title + url for k in ("poste", "station", "liste", "fiche")) and (
                "anomalie" not in title + url
            ) and (
                fmt in ("csv", "gz", "json", "geojson")
                or url.endswith((".csv", ".csv.gz", ".gz", ".json", ".geojson"))
            ):
                dl_url = r.get("url", "")
                name = re.sub(r"[^\w\-.]", "_", dl_url.split("/")[-1] or "postes.csv")
                dl_path = CACHE_DIR / name
                if not dl_path.exists():
                    with requests.get(dl_url, headers=HEADERS, timeout=120, stream=True) as rr:
                        rr.raise_for_status()
                        with open(dl_path, "wb") as f:
                            for chunk in rr.iter_content(chunk_size=256 * 1024):
                                f.write(chunk)
                df = _read_postes_file(dl_path)
                if df is not None:
                    log.info(f"Référentiel trouvé dans le dataset : {r.get('title')}")
                    return df
    except Exception as exc:
        log.warning(f"Recherche via API impossible : {exc}")

    return None


def find_nearest_stations(
    ref_lat: float,
    ref_lon: float,
    n: int = 10,
    rayon_km: float | None = None,
) -> pd.DataFrame | None:
    """
    Retourne les N stations les plus proches du point (ref_lat, ref_lon).
    Si le référentiel postes n'est pas disponible, utilise les données déjà
    téléchargées dans le cache (CSV.gz du dataset quotidien).
    """
    log.info(f"Recherche des {n} stations les plus proches de ({ref_lat}, {ref_lon})...")

    df_postes = _load_postes_reference()

    # Fallback : reconstruire depuis les CSV.gz en cache
    if df_postes is None:
        log.info("Référentiel absent — reconstruction depuis les fichiers CSV.gz en cache...")
        frames = []
        for gz in CACHE_DIR.glob("*.csv.gz"):
            try:
                with gzip.open(gz, "rt", encoding="utf-8-sig") as f:
                    chunk = pd.read_csv(f, sep=";", dtype=str,
                                        usecols=lambda c: c in
                                        ["NUM_POSTE","NOM_USUEL","LAT","LON","ALTI"],
                                        low_memory=False)
                frames.append(chunk)
            except Exception:
                pass
        if not frames:
            log.error("Aucune donnée de référence disponible. Lancez d'abord un téléchargement.")
            return None
        df_postes = pd.concat(frames, ignore_index=True).drop_duplicates("NUM_POSTE")

    # Normaliser colonnes
    df = df_postes.copy()
    for col in ("NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI"):
        if col not in df.columns:
            df[col] = ""

    df["LAT"] = pd.to_numeric(df["LAT"].str.replace(",", "."), errors="coerce")
    df["LON"] = pd.to_numeric(df["LON"].str.replace(",", "."), errors="coerce")
    df = df.dropna(subset=["LAT", "LON"])

    # Calcul des distances
    df["dist_km"] = df.apply(
        lambda row: _haversine_km(ref_lat, ref_lon, row["LAT"], row["LON"]), axis=1
    )
    if rayon_km is not None:
        df = df[df["dist_km"] <= rayon_km]

    nearest = df.nsmallest(n, "dist_km")[
        ["NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI", "dist_km"]
    ].reset_index(drop=True)

    return nearest


def generate_map_html(
    nearest: pd.DataFrame,
    ref_lat: float,
    ref_lon: float,
    output_path: Path,
) -> None:
    """Génère une carte HTML interactive (Leaflet + OpenStreetMap)."""

    # Construire les marqueurs JS
    markers_js = []
    for _, row in nearest.iterrows():
        num   = row["NUM_POSTE"]
        nom   = row["NOM_USUEL"] or num
        lat   = row["LAT"]
        lon   = row["LON"]
        alti  = row.get("ALTI", "?")
        dist  = row["dist_km"]
        popup = (
            f"<b>{nom}</b><br>"
            f"NUM_POSTE : <code>{num}</code><br>"
            f"Altitude : {alti} m<br>"
            f"Distance : {dist:.1f} km"
        )
        markers_js.append(
            f'L.marker([{lat}, {lon}])'
            f'.addTo(map)'
            f'.bindPopup("{popup}")'
            f'.bindTooltip("<b>{nom}</b> ({dist:.1f} km)", {{permanent:false}});'
        )

    markers_str = "\n    ".join(markers_js)

    # Tableau HTML des stations
    rows_html = ""
    for i, row in nearest.iterrows():
        rows_html += (
            f"<tr>"
            f"<td>{i+1}</td>"
            f"<td><code>{row['NUM_POSTE']}</code></td>"
            f"<td>{row['NOM_USUEL']}</td>"
            f"<td>{row['LAT']:.4f}</td>"
            f"<td>{row['LON']:.4f}</td>"
            f"<td>{row.get('ALTI','?')}</td>"
            f"<td>{row['dist_km']:.1f} km</td>"
            f"</tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<title>Stations Météo-France les plus proches</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; color: #222; }}
  h1 {{ padding: 14px 20px; background: #1a4a8a; color: white; font-size: 1.1rem; }}
  #map {{ height: 55vh; width: 100%; border-bottom: 3px solid #1a4a8a; }}
  .info {{ padding: 10px 20px; background: #eaf0fb; font-size: 0.85rem; border-bottom: 1px solid #ccd; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th {{ background: #1a4a8a; color: white; padding: 8px 10px; text-align: left; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #dde; }}
  tr:hover {{ background: #eaf0fb; }}
  code {{ background: #e8eaf0; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }}
  .ref-dot {{ display: inline-block; width: 12px; height: 12px; background: red; border-radius: 50%; margin-right: 5px; vertical-align: middle; }}
</style>
</head>
<body>
<h1>📍 Stations Météo-France — {len(nearest)} stations les plus proches</h1>
<div class="info">
  <span class="ref-dot"></span>
  Point de référence : <b>{ref_lat:.4f}°N, {ref_lon:.4f}°E</b>
  &nbsp;|&nbsp; Source : meteo.data.gouv.fr
</div>
<div id="map"></div>
<table>
<thead><tr>
  <th>#</th><th>NUM_POSTE</th><th>Nom</th><th>Lat</th><th>Lon</th><th>Alt (m)</th><th>Distance</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
<script>
  var map = L.map('map').setView([{ref_lat}, {ref_lon}], 9);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '© <a href="https://www.openstreetmap.org/">OpenStreetMap</a> contributors',
    maxZoom: 18
  }}).addTo(map);

  // Point de référence
  L.circleMarker([{ref_lat}, {ref_lon}], {{
    radius: 10, color: 'red', fillColor: '#ff4444', fillOpacity: 0.9, weight: 2
  }}).addTo(map).bindPopup("<b>Point de référence</b><br>{ref_lat:.4f}°N, {ref_lon:.4f}°E");

  // Stations
  {markers_str}
</script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info(f"Carte générée : {output_path}")

    # Afficher le tableau dans le terminal
    print(f"\n{'#':>3} {'NUM_POSTE':^10} {'NOM':^25} {'Dist (km)':>10}")
    print("-" * 55)
    for i, row in nearest.iterrows():
        print(f"{i+1:>3} {row['NUM_POSTE']:^10} {str(row['NOM_USUEL']):<25} {row['dist_km']:>9.1f}")


# ============================================================================
#  MODULE 2 — TÉLÉCHARGEMENT
# ============================================================================

def get_all_resources() -> list[dict]:
    resp = requests.get(API_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    resources = resp.json().get("resources", [])
    log.info(f"{len(resources)} ressources disponibles.")
    return resources


def get_departments(station_ids: set[str]) -> set[str]:
    depts = set()
    for sid in station_ids:
        p3 = sid[:3]
        depts.add(p3 if p3 in _DOM_TOM else sid[:2])
    return depts


def _extract_dept_from_resource(r: dict) -> str | None:
    for text in (r.get("title", ""), r.get("url", "")):
        m = re.search(r"[_/](\d{2,3})_", text)
        if m:
            return m.group(1)
    return None


def filter_resources(resources: list[dict], departments: set[str]) -> list[dict]:
    groupes = _groupes_requis(VARIABLES_SOUHAITEES)
    selected = []
    for r in resources:
        title = r.get("title", "")
        url   = r.get("url", "")
        if not (title.lower().endswith(".csv.gz") or url.lower().endswith(".csv.gz")):
            continue
        dept = _extract_dept_from_resource(r)
        if not (dept and dept in departments):
            continue
        # Ne garder que les groupes de fichiers contenant les variables demandées
        # (évite de télécharger "autres-parametres" quand on ne veut que RR,
        # et surtout évite que ces fichiers écrasent les données à la consolidation)
        if groupes is not None:
            txt = (title + " " + url).lower()
            if not any(g in txt for g in groupes):
                continue
        selected.append(r)
    log.info(f"{len(selected)} fichiers sélectionnés "
             f"(depts {sorted(departments)}, groupes {sorted(groupes) if groupes else 'tous'}).")
    if not selected:
        log.warning("Aucun fichier sélectionné. Exemples de ressources disponibles :")
        csv_res = [r for r in resources
                   if r.get("title","").lower().endswith(".csv.gz")
                   or r.get("url","").lower().endswith(".csv.gz")]
        for r in csv_res[:5]:
            log.warning(f"  title={r.get('title')!r}  url=...{r.get('url','')[-55:]!r}")
    return selected


def download_resource(resource: dict) -> Path | None:
    title = resource.get("title", "")
    url   = resource.get("url") or (resource.get("latest") or {}).get("url")
    if not url:
        log.warning(f"  Pas d'URL pour '{title}'.")
        return None
    url_basename = url.split("/")[-1].split("?")[0]
    filename     = re.sub(r"[^\w\-.]", "_", url_basename or title or "unnamed")
    if not filename.endswith(".csv.gz"):
        filename += ".csv.gz"
    local_path = CACHE_DIR / filename
    is_latest  = "latest" in (url + title).lower()

    if local_path.exists() and not (is_latest and FORCE_REFRESH_LATEST):
        log.info(f"  [cache]    {filename}")
        return local_path

    log.info(f"  [download] {filename}")
    try:
        with requests.get(url, headers=HEADERS, timeout=180, stream=True) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=512 * 1024):
                    f.write(chunk)
        time.sleep(0.4)
        return local_path
    except Exception as exc:
        log.error(f"  Échec : {exc}")
        local_path.unlink(missing_ok=True)
        return None


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _build_columns_to_keep(
    all_cols: list[str], variables: list[str] | None, inclure_qualite: bool
) -> list[str] | None:
    if variables is None:
        return None
    keep = [c for c in _ID_COLS if c in all_cols]
    for var in variables:
        if var in all_cols:
            keep.append(var)
        if inclure_qualite:
            q = f"Q{var}"
            if q in all_cols:
                keep.append(q)
    seen, result = set(), []
    for c in keep:
        if c not in seen:
            seen.add(c); result.append(c)
    return result


def read_and_filter(
    local_path: Path, station_ids: set[str],
    variables: list[str] | None, inclure_qualite: bool,
) -> pd.DataFrame | None:
    try:
        with gzip.open(local_path, "rt", encoding="utf-8-sig") as f:
            df = pd.read_csv(f, sep=";", dtype=str, low_memory=False)
    except Exception as exc:
        log.error(f"  Erreur lecture {local_path.name} : {exc}")
        return None

    id_col = _find_col(df, _STATION_COL_CANDS)
    if id_col is None:
        log.warning(f"  Colonne station introuvable dans {local_path.name}.")
        return None

    df[id_col] = df[id_col].str.strip().str.zfill(8)
    filtered   = df[df[id_col].isin(station_ids)].copy()
    if filtered.empty:
        return None

    cols = _build_columns_to_keep(list(filtered.columns), variables, inclure_qualite)
    if cols is not None:
        cols = [c for c in cols if c in filtered.columns]
        # Si aucune variable demandée n'est présente dans ce fichier,
        # l'ignorer : sinon ses lignes "vides" (colonnes ID uniquement)
        # polluent la consolidation et écrasent les vraies données.
        if not any(c not in _ID_COLS for c in cols):
            log.info(f"  → ignoré (aucune variable demandée dans {local_path.name})")
            return None
        filtered = filtered[cols]

    log.info(f"  → {len(filtered)} lignes | {len(filtered.columns)} colonnes")
    return filtered


# ============================================================================
#  MODULE 3 — GRAPHIQUE CHRONIQUE JOURNALIÈRE (Plotly HTML)
# ============================================================================

# Palette de couleurs pour les stations (cycle automatique)
_PALETTE = [
    "#2563EB",  # bleu roi
    "#DC2626",  # rouge vif
    "#16A34A",  # vert forêt
    "#D97706",  # ambre
    "#7C3AED",  # violet
    "#0891B2",  # cyan foncé
    "#DB2777",  # rose vif
    "#65A30D",  # vert olive
    "#EA580C",  # orange brûlé
    "#0D9488",  # teal
]


def generate_timeseries_html(
    df: pd.DataFrame,
    variable: str,
    output_path: Path,
) -> None:
    """
    Génère un fichier HTML Plotly avec une chronique journalière par station.
    Mise en page : barres (variable) + courbe cumulée (axe Y droit), par station,
    style proche de l'exemple Météo-France fourni.
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        log.error("plotly non installé. Lancez : pip install plotly")
        return

    id_col   = _find_col(df, _STATION_COL_CANDS)
    date_col = _find_col(df, _DATE_COL_CANDS)

    if id_col is None or date_col is None:
        log.error("Colonnes station/date introuvables — graphique annulé.")
        return

    if variable not in df.columns:
        log.error(f"Variable '{variable}' absente du DataFrame — graphique annulé.")
        log.error(f"  Colonnes disponibles : {list(df.columns)}")
        return

    # Préparer les données
    df = df.copy()
    df["_date"] = pd.to_datetime(df[date_col].astype(str), format="%Y%m%d", errors="coerce")
    df["_val"]  = pd.to_numeric(df[variable].str.replace(",", "."), errors="coerce")
    df = df.dropna(subset=["_date", "_val"]).sort_values([id_col, "_date"])

    stations   = df[id_col].unique()
    n_stations = len(stations)

    if n_stations == 0:
        log.warning("Aucune station avec données valides — graphique annulé.")
        return

    meta      = _VAR_META.get(variable, {"label": variable, "color": "#1a6fc4", "bar": True})
    use_bar   = meta["bar"]
    var_label = meta["label"]

    log.info(f"Génération du graphique : {n_stations} station(s), variable={variable}")

    # Hauteur par subplot (px) — 250 px/subplot + 80 px de marge titre/axe X
    subplot_h = 250
    top_margin = 80
    total_h   = n_stations * subplot_h + top_margin

    # vertical_spacing est une fraction de la hauteur totale du graphique.
    # On veut 18 px fixes entre chaque subplot, quelque soit le nombre de stations.
    gap_px = 18
    if n_stations > 1:
        v_spacing = gap_px / total_h
    else:
        v_spacing = 0.0

    row_heights = [1.0] * n_stations

    # Libellés des stations (pour légende et hover)
    station_labels = []
    for sta in stations:
        nom_sta = df[df[id_col] == sta]["NOM_USUEL"].dropna()
        nom_sta = nom_sta.iloc[0] if ("NOM_USUEL" in df.columns and not nom_sta.empty) else ""
        station_labels.append(f"{sta} — {nom_sta}" if nom_sta else sta)

    specs = [[{}] for _ in range(n_stations)]
    # Pas de subplot_titles : on les injecte en annotations dans la zone de tracé
    # pour éviter tout chevauchement avec l'axe X partagé.
    fig = make_subplots(
        rows=n_stations, cols=1,
        shared_xaxes=True,
        specs=specs,
        row_heights=row_heights,
        vertical_spacing=v_spacing,
    )

    for i, sta in enumerate(stations):
        row   = i + 1
        color = _PALETTE[i % len(_PALETTE)]
        sub   = df[df[id_col] == sta].sort_values("_date")

        dates = sub["_date"].tolist()
        vals  = sub["_val"].tolist()

        nom = station_labels[i]

        # Trace principale : barres (opacité pleine) ou ligne
        if use_bar:
            fig.add_trace(
                go.Bar(
                    x=dates, y=vals,
                    name=nom,
                    marker_color=color,
                    marker_line_color=color,
                    marker_line_width=0,
                    opacity=1.0,
                    showlegend=True,
                    legendgroup=sta,
                    hovertemplate="%{x|%Y-%m-%d}<br>" + f"{variable}" + " = %{y:.1f}<extra>" + nom + "</extra>",
                ),
                row=row, col=1, secondary_y=False,
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=dates, y=vals,
                    name=nom,
                    mode="lines",
                    line=dict(color=color, width=1.4),
                    showlegend=True,
                    legendgroup=sta,
                    hovertemplate="%{x|%Y-%m-%d}<br>" + f"{variable}" + " = %{y:.1f}<extra>" + nom + "</extra>",
                ),
                row=row, col=1, secondary_y=False,
            )

        # Axe Y
        fig.update_yaxes(
            title_text=var_label,
            title_font=dict(size=10),
            tickfont=dict(size=9),
            gridcolor="#e8e8e8",
            row=row, col=1, secondary_y=False,
        )

    # Axe X commun
    fig.update_xaxes(
        tickfont=dict(size=9),
        gridcolor="#e8e8e8",
        showticklabels=True,
    )
    fig.update_xaxes(
        title_text="Date",
        title_font=dict(size=11),
        row=n_stations, col=1,
    )

    # Titres injectés comme annotations dans la zone de tracé (coin haut-gauche
    # de chaque subplot), en coordonnées "paper". Cela évite le chevauchement
    # entre le titre du subplot N+1 et l'axe X partagé du subplot N.
    n_rows      = n_stations
    total_space = 1.0 - v_spacing * (n_rows - 1)  # espace net pour les subplots
    row_frac    = total_space / n_rows              # hauteur d'un subplot en coords paper

    title_annotations = []
    for i, label in enumerate(station_labels):
        # Coordonnée Y du bord supérieur du subplot i (paper coords, 0=bas 1=haut)
        row_top = 1.0 - i * (row_frac + v_spacing)
        title_annotations.append(dict(
            text=f"<b>{label}</b>",
            x=0.0,
            y=row_top,
            xref="paper", yref="paper",
            xanchor="left", yanchor="bottom",
            showarrow=False,
            font=dict(size=11, color="#222222"),
            bgcolor="rgba(255,255,255,0.75)",
            borderpad=2,
        ))

    fig.update_layout(annotations=title_annotations)

    fig.update_layout(
        title=dict(
            text=f"meteo.data.gouv.fr — {var_label} (journalier)",
            font=dict(size=14),
            x=0.5, xanchor="center",
        ),
        height=total_h,
        paper_bgcolor="white",
        plot_bgcolor="white",
        legend=dict(
            orientation="v",
            x=1.04, y=1,
            xanchor="left",
            font=dict(size=10),
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="#cccccc",
            borderwidth=1,
        ),
        margin=dict(l=70, r=180, t=70, b=60),
        hovermode="x unified",
        barmode="overlay",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(
        output_path,
        include_plotlyjs="cdn",
        full_html=True,
        config={"scrollZoom": True, "displayModeBar": True},
    )
    log.info(f"Graphique généré : {output_path}")



# ============================================================================
#  MODULE 4 — ANALYSE DES LACUNES
# ============================================================================

import calendar as _calendar


def _get_vars_from_df(df: pd.DataFrame, variables: list | None) -> list[str]:
    """Retourne la liste des variables à analyser."""
    if variables:
        return [v for v in variables if v in df.columns]
    # Exclure les colonnes d'identification
    exclude = set(_ID_COLS) | {"_date", "_annee", "_val", "QRR","QTN","QTX","QTM",
                                "QFFM","QFXY","QINST","QUN","QUX","QUM"}
    return [c for c in df.columns if c not in exclude and not c.startswith("Q")]


def compute_annual_stats(
    df: pd.DataFrame, id_col: str, date_col: str, variables: list[str]
) -> pd.DataFrame:
    """
    Retourne un DataFrame long :
      station | nom | annee | variable | n_attendu | n_present | n_manquant | pct_manquant
    """
    df = df.copy()
    df["_date"] = pd.to_datetime(df[date_col].astype(str), format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["_date"])
    df["_annee"] = df["_date"].dt.year

    nom_map = df.groupby(id_col)["NOM_USUEL"].first().to_dict() if "NOM_USUEL" in df.columns else {}

    records = []
    for sta in sorted(df[id_col].unique()):
        sub = df[df[id_col] == sta]
        nom = nom_map.get(sta, "")
        for annee in sorted(sub["_annee"].unique()):
            n_attendu = 366 if _calendar.isleap(int(annee)) else 365
            sub_yr    = sub[sub["_annee"] == annee]
            for var in variables:
                if var not in sub_yr.columns:
                    continue
                col = sub_yr[var]
                if col.dtype == object:
                    col = pd.to_numeric(col.str.replace(",", "."), errors="coerce")
                else:
                    col = pd.to_numeric(col, errors="coerce")
                n_present    = int(col.notna().sum())
                n_manquant   = max(0, n_attendu - n_present)
                pct_manquant = round(100.0 * n_manquant / n_attendu, 1)
                records.append({
                    "station": sta, "nom": nom, "annee": int(annee), "variable": var,
                    "n_attendu": n_attendu, "n_present": n_present,
                    "n_manquant": n_manquant, "pct_manquant": pct_manquant,
                })
    return pd.DataFrame(records)


def compute_gap_ranges(
    df: pd.DataFrame, id_col: str, date_col: str, variables: list[str]
) -> pd.DataFrame:
    """
    Identifie les plages continues de lacunes (valeur absente ou NaN).
    Retourne : station | nom | variable | date_debut | date_fin | duree_jours
    """
    df = df.copy()
    df["_date"] = pd.to_datetime(df[date_col].astype(str), format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["_date"])
    nom_map = df.groupby(id_col)["NOM_USUEL"].first().to_dict() if "NOM_USUEL" in df.columns else {}

    records = []
    for sta in sorted(df[id_col].unique()):
        sub = df[df[id_col] == sta].set_index("_date").sort_index()
        nom = nom_map.get(sta, "")
        d_min, d_max = sub.index.min(), sub.index.max()
        full_idx = pd.date_range(d_min, d_max, freq="D")

        for var in variables:
            if var not in sub.columns:
                continue
            s = sub[var].reindex(full_idx)
            if s.dtype == object:
                s = pd.to_numeric(s.str.replace(",", "."), errors="coerce")
            else:
                s = pd.to_numeric(s, errors="coerce")

            missing = s.isna()
            if not missing.any():
                continue

            in_gap, gap_start = False, None
            for date, is_missing in missing.items():
                if is_missing and not in_gap:
                    gap_start, in_gap = date, True
                elif not is_missing and in_gap:
                    duree = (date - gap_start).days
                    records.append({
                        "station": sta, "nom": nom, "variable": var,
                        "date_debut": gap_start.strftime("%Y-%m-%d"),
                        "date_fin":   (date - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                        "duree_jours": duree,
                    })
                    in_gap = False
            if in_gap:
                duree = (d_max - gap_start).days + 1
                records.append({
                    "station": sta, "nom": nom, "variable": var,
                    "date_debut": gap_start.strftime("%Y-%m-%d"),
                    "date_fin":   d_max.strftime("%Y-%m-%d"),
                    "duree_jours": duree,
                })

    result = pd.DataFrame(records)
    if not result.empty:
        result = result.sort_values(["station", "variable", "date_debut"]).reset_index(drop=True)
    return result


def _pct_to_color(pct: float) -> tuple[str, str]:
    """Retourne (bg_color, text_color) pour un % de lacune."""
    pct = max(0.0, min(100.0, pct))
    if pct == 0:
        bg = "#d4edda"
    elif pct < 5:
        t  = pct / 5
        r  = int(212 + (255 - 212) * t)
        g  = int(237 - (237 - 200) * t)
        b  = int(218 * (1 - t))
        bg = f"rgb({r},{g},{b})"
    elif pct < 20:
        t  = (pct - 5) / 15
        g  = int(200 - 60 * t)
        bg = f"rgb(255,{g},0)"
    elif pct < 60:
        t  = (pct - 20) / 40
        g  = int(140 - 140 * t)
        bg = f"rgb(255,{g},0)"
    else:
        t  = (pct - 60) / 40
        r  = int(255 - 55 * t)
        bg = f"rgb({r},0,0)"
    fg = "#ffffff" if pct >= 40 else "#222222"
    return bg, fg


def generate_gaps_html(
    df_annual: pd.DataFrame,
    df_ranges: pd.DataFrame,
    variables: list[str],
    output_path: Path,
) -> None:
    """
    Génère un rapport HTML avec :
      - Un onglet par variable
      - Heatmap annuelle (stations × années), cellules colorées par % lacune
      - Tableau des plages continues de lacunes trié par durée décroissante
    """
    if df_annual.empty:
        log.warning("Aucune statistique de lacune à afficher.")
        return

    tab_buttons:  list[str] = []
    tab_contents: list[str] = []

    for vi, var in enumerate(variables):
        ann_var = df_annual[df_annual["variable"] == var]
        if ann_var.empty:
            continue

        pivot  = ann_var.pivot_table(
            index=["station", "nom"], columns="annee",
            values="pct_manquant", aggfunc="first",
        )
        annees = sorted(pivot.columns.tolist())

        # ---- Heatmap ----
        thead = "<tr><th>Station</th><th>Nom</th>"
        for a in annees:
            thead += f"<th>{a}</th>"
        thead += "</tr>"

        tbody = ""
        for (sta, nom), row_data in pivot.iterrows():
            total_attendu = int(ann_var[ann_var["station"] == sta]["n_attendu"].sum())
            total_present = int(ann_var[ann_var["station"] == sta]["n_present"].sum())
            total_manq    = total_attendu - total_present
            pct_total     = round(100.0 * total_manq / total_attendu, 1) if total_attendu > 0 else 0.0
            bg_tot, fg_tot = _pct_to_color(pct_total)
            tbody += (
                f"<tr>"
                f"<td class='sta-id'>{sta}</td>"
                f"<td class='sta-nom'>{nom}</td>"
            )
            for a in annees:
                pct = row_data.get(a, float("nan"))
                if pd.isna(pct):
                    tbody += "<td class='no-data'>—</td>"
                else:
                    bg, fg = _pct_to_color(pct)
                    tbody += (
                        f"<td style='background:{bg};color:{fg}' "
                        f"title='{pct:.1f}% manquant en {a}'>{pct:.0f}%</td>"
                    )
            tbody += (
                f"<td class='total-cell' style='background:{bg_tot};color:{fg_tot}' "
                f"title='Total période'><b>{pct_total:.0f}%</b></td>"
                f"</tr>\n"
            )

        # ---- Plages continues ----
        rng = df_ranges[df_ranges["variable"] == var] if not df_ranges.empty else pd.DataFrame()
        if rng.empty:
            ranges_html = "<p class='no-gaps'>✅ Aucune lacune continue détectée.</p>"
        else:
            ranges_html = (
                "<table class='gaps-table'>"
                "<thead><tr><th>Station</th><th>Nom</th>"
                "<th>Début</th><th>Fin</th><th>Durée (j)</th></tr></thead><tbody>"
            )
            for _, r in rng.sort_values("duree_jours", ascending=False).iterrows():
                d = r["duree_jours"]
                cls = "gap-short" if d <= 7 else ("gap-medium" if d <= 30 else "gap-long")
                ranges_html += (
                    f"<tr class='{cls}'>"
                    f"<td>{r['station']}</td><td>{r['nom']}</td>"
                    f"<td>{r['date_debut']}</td><td>{r['date_fin']}</td>"
                    f"<td><b>{d}</b></td></tr>\n"
                )
            ranges_html += "</tbody></table>"

        active = "active" if vi == 0 else ""
        tab_buttons.append(
            f'<button class="tab-btn {active}" onclick="showTab(\'{var}\', this)">{var}</button>'
        )
        tab_contents.append(f"""
<div id="tab-{var}" class="tab-content {active}">
  <h2>Variable : <span class="var-badge">{var}</span></h2>
  <h3>Taux de lacune annuel (% de jours manquants)</h3>
  <div class="legend">
    <span class="leg-cell" style="background:#d4edda">0 %</span>
    <span style="font-size:0.75rem;color:#888">→</span>
    <span class="leg-cell" style="background:rgb(255,200,0)">5 %</span>
    <span style="font-size:0.75rem;color:#888">→</span>
    <span class="leg-cell" style="background:rgb(255,100,0)">20 %</span>
    <span style="font-size:0.75rem;color:#888">→</span>
    <span class="leg-cell" style="background:rgb(200,0,0);color:white">60 %+</span>
    &nbsp;&nbsp;
    <span class="leg-cell no-data">— non disponible</span>
    &nbsp;&nbsp;
    <span class="leg-cell total-cell">Total période</span>
  </div>
  <div class="table-wrap">
    <table class="heatmap-table">
      <thead>{thead}<tr><th>Station</th><th>Nom</th>{''.join(f'<th>{a}</th>' for a in annees)}<th>Total</th></tr></thead>
      <tbody>{tbody}</tbody>
    </table>
  </div>
  <h3>Plages continues de lacunes</h3>
  {ranges_html}
</div>""")

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<title>Analyse des lacunes — Météo-France</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Segoe UI',Arial,sans-serif; background:#f4f6f9; color:#222; font-size:14px; }}
header {{ background:#1a4a8a; color:white; padding:14px 24px; }}
header h1 {{ font-size:1.1rem; font-weight:600; }}
.tabs {{ display:flex; gap:4px; padding:12px 24px 0; background:#e8eef8;
         border-bottom:2px solid #1a4a8a; flex-wrap:wrap; }}
.tab-btn {{ padding:7px 18px; border:1px solid #aac; border-bottom:none;
            border-radius:5px 5px 0 0; background:#d0daf0; cursor:pointer;
            font-size:0.9rem; font-weight:600; color:#1a4a8a; transition:background 0.15s; }}
.tab-btn.active {{ background:white; border-bottom:2px solid white; color:#1a4a8a; }}
.tab-content {{ display:none; padding:24px; }}
.tab-content.active {{ display:block; }}
h2 {{ font-size:1rem; color:#1a4a8a; margin-bottom:14px; }}
h3 {{ font-size:0.85rem; color:#555; margin:20px 0 8px; font-weight:700;
      text-transform:uppercase; letter-spacing:0.05em; border-left:3px solid #1a4a8a;
      padding-left:8px; }}
.var-badge {{ background:#1a4a8a; color:white; padding:2px 8px; border-radius:4px;
              font-family:monospace; font-size:0.9rem; }}
.legend {{ display:flex; gap:6px; margin-bottom:10px; align-items:center; flex-wrap:wrap; }}
.leg-cell {{ padding:3px 10px; border-radius:3px; font-size:0.78rem; border:1px solid #ccc; }}
.table-wrap {{ overflow-x:auto; max-height:60vh; overflow-y:auto;
               border:1px solid #dde; border-radius:6px; }}
table {{ border-collapse:collapse; font-size:0.82rem; white-space:nowrap; }}
th {{ background:#1a4a8a; color:white; padding:6px 10px; text-align:center;
      position:sticky; top:0; z-index:2; }}
th:first-child {{ text-align:left; left:0; z-index:3; min-width:90px; }}
th:nth-child(2) {{ text-align:left; left:90px; z-index:3; min-width:140px; }}
td {{ padding:5px 10px; text-align:center; border:1px solid #e4e8f0; }}
td.sta-id {{ font-family:monospace; font-size:0.85rem; font-weight:600; text-align:left;
             background:#f0f4ff; position:sticky; left:0; z-index:1; min-width:90px; }}
td.sta-nom {{ text-align:left; background:#f8f9ff; min-width:140px;
              position:sticky; left:90px; z-index:1; }}
td.no-data {{ color:#bbb; background:#fafafa; }}
td.total-cell {{ font-weight:700; border-left:2px solid #1a4a8a; }}
tr:hover td {{ filter:brightness(0.93); }}
.gaps-table {{ margin-top:6px; width:100%; border-collapse:collapse; font-size:0.83rem; }}
.gaps-table th {{ background:#374151; color:white; padding:6px 14px; text-align:left;
                  position:sticky; top:0; }}
.gaps-table td {{ padding:5px 14px; border-bottom:1px solid #e5e7eb; }}
.gap-short  {{ background:#fef9c3; }}
.gap-medium {{ background:#fed7aa; }}
.gap-long   {{ background:#fecaca; }}
.no-gaps {{ padding:10px; color:#16a34a; font-weight:600; }}
</style>
</head>
<body>
<header><h1>📊 Analyse des lacunes — Données quotidiennes Météo-France</h1></header>
<div class="tabs">{"".join(tab_buttons)}</div>
{"".join(tab_contents)}
<script>
function showTab(v, btn) {{
  document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.getElementById("tab-" + v).classList.add("active");
  btn.classList.add("active");
}}
</script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info(f"Rapport lacunes généré : {output_path}")

# ============================================================================
#  MAIN
# ============================================================================

def run_find_stations() -> pd.DataFrame | None:
    """MODE 1 : recherche des stations proches + carte HTML."""
    log.info("=" * 60)
    log.info("MODE 1 — Recherche des stations proches")
    log.info("=" * 60)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    nearest = find_nearest_stations(REF_LAT, REF_LON, N_STATIONS_PROCHES, RAYON_MAX_KM)
    if nearest is None or nearest.empty:
        log.error("Aucune station trouvée.")
        return None

    generate_map_html(nearest, REF_LAT, REF_LON, OUTPUT_CARTE)
    return nearest


def run_download() -> pd.DataFrame | None:
    """MODE 2 : téléchargement + CSV + graphique."""
    log.info("=" * 60)
    log.info("MODE 2 — Téléchargement des données")
    log.info("=" * 60)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    station_ids = {str(s).strip().zfill(8) for s in STATION_IDS}
    log.info(f"Stations  : {sorted(station_ids)}")
    log.info(f"Variables : {VARIABLES_SOUHAITEES or 'toutes'}")

    all_resources = get_all_resources()
    departments   = get_departments(station_ids)
    log.info(f"Depts     : {sorted(departments)}")

    selected = filter_resources(all_resources, departments)
    if not selected:
        log.error("Aucun fichier trouvé — vérifiez les NUM_POSTE.")
        return None

    frames: list[pd.DataFrame] = []
    for i, resource in enumerate(selected, 1):
        log.info(f"[{i}/{len(selected)}] {resource.get('title', '?')}")
        path = download_resource(resource)
        if path is None:
            continue
        df = read_and_filter(path, station_ids, VARIABLES_SOUHAITEES, INCLURE_COLONNES_QUALITE)
        if df is not None:
            frames.append(df)

    if not frames:
        log.error("Aucune donnée extraite.")
        return None

    log.info("Consolidation...")
    consolidated = pd.concat(frames, ignore_index=True)

    id_col   = _find_col(consolidated, _STATION_COL_CANDS)
    date_col = _find_col(consolidated, _DATE_COL_CANDS)

    if id_col and date_col:
        n_before = len(consolidated)
        # FUSION au lieu de drop_duplicates : si une même (station, date)
        # apparaît dans plusieurs fichiers (ex. RR-T-Vent + autres-parametres),
        # on combine les colonnes (première valeur non nulle) au lieu de
        # supprimer arbitrairement une des deux lignes — ce qui faisait
        # perdre des données (RR écrasé par des lignes sans RR).
        consolidated = (
            consolidated
            .groupby([id_col, date_col], as_index=False, sort=True)
            .first()
        )
        merged = n_before - len(consolidated)
        if merged:
            log.info(f"  {merged} lignes dupliquées fusionnées.")
        consolidated.sort_values([id_col, date_col], inplace=True)

    consolidated.reset_index(drop=True, inplace=True)
    consolidated.to_csv(OUTPUT_CSV, sep=";", index=False, encoding="utf-8-sig")
    log.info(f"CSV exporté : {OUTPUT_CSV}  ({len(consolidated)} lignes)")

    # Résumé
    if id_col and date_col:
        summary = consolidated.groupby(id_col)[date_col].agg(debut="min", fin="max", n_jours="count")
        print(f"\n{'Station':^12} {'Début':^10} {'Fin':^10} {'N jours':>8}")
        print("-" * 46)
        for sta, row in summary.iterrows():
            print(f"{sta:^12} {row['debut']:^10} {row['fin']:^10} {row['n_jours']:>8}")

    # Graphique
    log.info(f"Génération du graphique pour la variable : {VARIABLE_GRAPHIQUE}")
    generate_timeseries_html(consolidated, VARIABLE_GRAPHIQUE, OUTPUT_GRAPH)

    return consolidated


def run_gap_analysis() -> None:
    """MODE 3 : analyse des lacunes sur le CSV consolidé existant."""
    log.info("=" * 60)
    log.info("MODE 3 — Analyse des lacunes")
    log.info("=" * 60)

    if not OUTPUT_CSV.exists():
        log.error(f"CSV consolidé introuvable : {OUTPUT_CSV}")
        log.error("Lancez d'abord MODE='download' pour générer les données.")
        return

    df = pd.read_csv(OUTPUT_CSV, sep=";", dtype=str, low_memory=False)
    id_col   = _find_col(df, _STATION_COL_CANDS)
    date_col = _find_col(df, _DATE_COL_CANDS)

    if id_col is None or date_col is None:
        log.error("Colonnes station/date introuvables dans le CSV.")
        return

    variables = _get_vars_from_df(df, VARIABLES_GAPS)
    if not variables:
        log.error("Aucune variable trouvée pour l'analyse.")
        return
    log.info(f"Variables analysées : {variables}")

    log.info("Calcul des statistiques annuelles...")
    df_annual = compute_annual_stats(df, id_col, date_col, variables)

    log.info("Détection des plages continues de lacunes...")
    df_ranges = compute_gap_ranges(df, id_col, date_col, variables)

    # Résumé terminal
    if not df_annual.empty:
        print(f"\n{'Station':^12} {'Variable':^8} {'Années':>7} {'% lacune moy':>14}")
        print("-" * 48)
        for (sta, var), grp in df_annual.groupby(["station", "variable"]):
            moy = grp["pct_manquant"].mean()
            print(f"{sta:^12} {var:^8} {len(grp):>7} {moy:>13.1f}%")

    if not df_ranges.empty:
        print(f"\n{len(df_ranges)} plages de lacunes détectées "
              f"(max : {int(df_ranges['duree_jours'].max())} jours)")

    generate_gaps_html(df_annual, df_ranges, variables, OUTPUT_GAPS)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Téléchargement des données climatologiques quotidiennes Météo-France")
    parser.add_argument("--config", required=True,
                        help="Fichier de configuration TOML (voir config_exemple.toml)")
    charger_config(parser.parse_args().config)

    mode = MODE.strip().lower()
    if mode in ("find", "both", "all"):
        run_find_stations()
    if mode in ("download", "both", "all"):
        run_download()
    if mode in ("gaps", "all"):
        run_gap_analysis()
    if mode not in ("find", "download", "gaps", "both", "all"):
        log.error(f"MODE invalide : '{MODE}'. Valeurs acceptées : 'find', 'download', 'gaps', 'both', 'all'.")


if __name__ == "__main__":
    main()
