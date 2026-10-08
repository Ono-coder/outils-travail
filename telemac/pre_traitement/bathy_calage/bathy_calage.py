"""
=============================================================================
bathy_calage.py
-----------------------------------------------------------------------------
Assigne la bathymétrie aux nœuds d'un maillage TELEMAC à partir de plusieurs
rasters priorisés, et exporte notamment un CSV de la bathymétrie retenue en
repère local TELEMAC : colonnes X_loc ; Y_loc ; Z_bathy
=============================================================================
"""
# ─────────────────────────────────────────────────────────────────────────────
# FIX PROJ — DOIT ÊTRE ABSOLUMENT EN PREMIER, AVANT TOUT IMPORT DE RASTERIO /
# GEOPANDAS / PYPROJ.  PROJ charge sa base de données à l'initialisation du
# module C ; si os.environ n'est pas déjà positionné à ce moment-là, le fix
# n'a aucun effet.
# ─────────────────────────────────────────────────────────────────────────────
import os, sys, argparse, tomllib

def _lire_config():
    """Lit le fichier TOML passé via --config (avant tout import géospatial,
    car il fournit aussi les chemins de secours PROJ)."""
    ap = argparse.ArgumentParser(description="Calage bathymétrique des nœuds TELEMAC multi-rasters.")
    ap.add_argument("--config", required=True,
                    help="Fichier de configuration TOML (voir config_exemple.toml)")
    a = ap.parse_args()
    try:
        with open(a.config, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        sys.exit(f"Fichier de configuration introuvable : {a.config}")
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"Fichier de configuration invalide ({a.config}) : {e}")

CFG = _lire_config()

def _cfg(section, cle, defaut=...):
    """Valeur CFG[section][cle] ; erreur explicite si obligatoire et absente."""
    val = CFG.get(section, {}).get(cle, defaut)
    if val is ...:
        sys.exit(f"Clé manquante dans la configuration : [{section}] {cle}")
    return val

def _fix_proj():
    """
    Résout le conflit 'DATABASE.LAYOUT.VERSION.MINOR' causé par deux
    installations PROJ coexistantes dans Anaconda.
    On cherche proj.db dans l'environnement Conda actif EN PREMIER,
    puis dans les emplacements GDAL/OSGeo4W standard.
    """
    candidates = []

    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        candidates += [
            os.path.join(conda_prefix, "Library", "share", "proj"),  # Windows
            os.path.join(conda_prefix, "share", "proj"),              # Linux/Mac
        ]

    # Fallbacks OSGeo4W / GDAL autonome (section [proj] de la configuration)
    candidates += _cfg("proj", "chemins_secours", [])

    for p in candidates:
        db = os.path.join(p, "proj.db")
        if os.path.exists(db):
            os.environ["PROJ_DATA"] = p
            os.environ["PROJ_LIB"]  = p   # compatibilité GDAL/rasterio ancien
            # Nettoyer toute surcharge GDAL_DATA qui pourrait pointer ailleurs
            os.environ.pop("GDAL_DATA", None)
            print(f"  [·] PROJ_DATA forcé → {p}")
            return
    print("  [⚠] proj.db introuvable — risque d'erreur CRS")

_fix_proj()   # appelé ICI, avant tout import rasterio/geopandas/pyproj

# ─────────────────────────────────────────────────────────────────────────────
# Imports standard — après le fix PROJ
# ─────────────────────────────────────────────────────────────────────────────
import shutil
import warnings
from typing import Optional
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.crs import CRS
from pyproj import Transformer
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# Paramètres lus dans le fichier de configuration TOML (--config)
# ---------------------------------------------------------------------------
NODES_SHP = _cfg("entrees", "noeuds_shp")

# (chemin, label, epsg source ou None, décalage Z) — ordre = priorité
RASTERS = [
    (r["chemin"], r["label"], r.get("epsg"), float(r.get("z_offset", 0.0)))
    for r in CFG.get("rasters", [])
]
if not RASTERS:
    sys.exit("Aucun raster défini dans la configuration ([[rasters]])")

EPSG_OUT        = _cfg("parametres", "epsg_sortie", 2154)
Z_MIN           = _cfg("parametres", "z_min", -30.0)
Z_MAX           = _cfg("parametres", "z_max", 300.0)
NODATA_FALLBACK = _cfg("parametres", "nodata_fallback", -9000.0)
OFFSET_X        = float(_cfg("parametres", "offset_x", 0.0))
OFFSET_Y        = float(_cfg("parametres", "offset_y", 0.0))
OUTPUT_DIR      = _cfg("sorties", "dossier")
OUTPUT_SHP_NAME = _cfg("sorties", "nom_shp", "noeuds_bathy")
OUTPUT_CSV_BATHY_NAME = _cfg("sorties", "nom_csv_bathy", "noeuds_bathy_locale")   # CSV : X_loc ; Y_loc ; Z_bathy
CARTE_TITRE     = _cfg("carte", "titre", "Bathymétrie")
COLORMAP        = _cfg("carte", "colormap", "terrain")


def log(msg: str, level: str = "INFO"):
    tag = {"INFO": "·", "OK": "✓", "WARN": "⚠", "ERR": "✗"}.get(level, "·")
    print(f"  [{tag}] {msg}")


def crs_from_epsg(epsg: int) -> CRS:
    """
    Construit un CRS sans passer par CRS.from_epsg() qui requiert proj.db.
    Utilise from_authority() (WKT lookup) ou from_string() comme fallback.
    """
    try:
        return CRS.from_authority("EPSG", epsg)
    except Exception:
        return CRS.from_string(f"EPSG:{epsg}")


def epsg_of(crs: CRS) -> Optional[int]:
    """Extrait l'entier EPSG d'un CRS rasterio sans appel PROJ database."""
    try:
        code = crs.to_epsg()
        if code is not None:
            return int(code)
    except Exception:
        pass
    # Fallback : parse l'authority depuis to_authority()
    try:
        auth, code = crs.to_authority()
        if auth.upper() == "EPSG":
            return int(code)
    except Exception:
        pass
    return None


def _safe_res(arr: np.ndarray) -> float:
    u = np.unique(arr)
    if len(u) < 2:
        return float("nan")
    return float(np.median(np.abs(np.diff(u))))


def xyz_to_geotiff(xyz_path: str, epsg: int, tmp_dir: str) -> str:
    log(f"Conversion XYZ → GeoTIFF : {Path(xyz_path).name}")
    try:
        data = np.loadtxt(xyz_path)
    except Exception:
        data = np.loadtxt(xyz_path, delimiter=",")

    x, y, z = data[:, 0], data[:, 1], data[:, 2]

    res_x = _safe_res(x)
    res_y = _safe_res(y)
    valid_res = [r for r in (res_x, res_y) if np.isfinite(r) and r > 0]
    res = min(valid_res) if valid_res else 1.0

    x0, x1 = x.min() - res / 2, x.max() + res / 2
    y0, y1 = y.min() - res / 2, y.max() + res / 2
    cols = max(1, int(round((x1 - x0) / res)))
    rows = max(1, int(round((y1 - y0) / res)))

    grid = np.full((rows, cols), np.nan, dtype=np.float32)
    col_idx = np.clip(((x - x0) / res).astype(int), 0, cols - 1)
    row_idx = np.clip(((y1 - y) / res).astype(int), 0, rows - 1)
    grid[row_idx, col_idx] = z

    transform = rasterio.transform.from_bounds(x0, y0, x1, y1, cols, rows)
    crs = crs_from_epsg(epsg)
    out_path = os.path.join(tmp_dir, Path(xyz_path).stem + "_tmp.tif")

    with rasterio.open(
        out_path, "w", driver="GTiff",
        height=rows, width=cols, count=1,
        dtype=np.float32, crs=crs, transform=transform,
        nodata=np.nan
    ) as dst:
        dst.write(grid, 1)

    log(f"  → {Path(out_path).name} ({cols}×{rows} px, res={res:.2f} m)", "OK")
    return out_path


def open_raster(path: str, epsg_force: Optional[int], tmp_dir: str):
    ext = Path(path).suffix.lower()
    if ext in (".xyz", ".txt"):
        epsg = epsg_force or EPSG_OUT
        tif_path = xyz_to_geotiff(path, epsg, tmp_dir)
        return rasterio.open(tif_path)

    ds = rasterio.open(path)
    if ds.crs is None and epsg_force:
        log(f"CRS manquant pour {Path(path).name}, forcé à EPSG:{epsg_force}", "WARN")
        profile = ds.profile.copy()
        profile.update(crs=crs_from_epsg(epsg_force))
        tif_path = os.path.join(tmp_dir, Path(path).stem + "_crs_forced.tif")
        with rasterio.open(tif_path, "w", **profile) as dst:
            dst.write(ds.read())
        ds.close()
        return rasterio.open(tif_path)
    return ds


def sample_raster(ds, xs_2154: np.ndarray, ys_2154: np.ndarray,
                  epsg_src: Optional[int]) -> np.ndarray:
    # Priorité 1 : EPSG fourni manuellement dans RASTERS (None = non fourni)
    # Priorité 2 : EPSG lu dans les métadonnées du fichier raster
    # Priorité 3 : on suppose identique à EPSG_OUT (avec avertissement)
    if epsg_src is not None:
        src_epsg = epsg_src
    elif ds.crs is not None:
        src_epsg = epsg_of(ds.crs)
        if src_epsg is None:
            log(f"EPSG non résolvable dans le fichier et non fourni — supposé identique à {EPSG_OUT}", "WARN")
            src_epsg = EPSG_OUT
    else:
        log(f"Aucun CRS disponible — supposé identique à {EPSG_OUT}", "WARN")
        src_epsg = EPSG_OUT

    if src_epsg != EPSG_OUT:
        # Reprojection via pyproj.Transformer (plus robuste que rio_transform)
        transformer = Transformer.from_crs(
            f"EPSG:{EPSG_OUT}", f"EPSG:{src_epsg}",
            always_xy=True
        )
        xs_src, ys_src = transformer.transform(xs_2154, ys_2154)
        xs_src = np.array(xs_src)
        ys_src = np.array(ys_src)
    else:
        xs_src, ys_src = xs_2154, ys_2154

    vals = np.full(len(xs_src), np.nan, dtype=np.float64)
    left, bottom, right, top = ds.bounds

    in_bounds = (
        (xs_src >= left) & (xs_src <= right) &
        (ys_src >= bottom) & (ys_src <= top)
    )
    if not np.any(in_bounds):
        return vals

    nodata = ds.nodata
    n_pts = int(np.sum(in_bounds))

    # Seuil adaptatif : ds.sample() est plus rapide pour peu de points,
    # ds.read() (1 seule lecture disque + indexation numpy) est plus rapide
    # quand les points sont nombreux par rapport à la taille du raster.
    # Heuristique : bascule si n_pts > 5 % des pixels du raster.
    raster_pixels = ds.width * ds.height
    use_read = n_pts > max(10_000, raster_pixels * 0.05)

    if use_read:
        # ── Lecture complète → indexation numpy ──────────────────────────
        band = ds.read(1).astype(np.float64)
        rows_f, cols_f = rasterio.transform.rowcol(
            ds.transform,
            xs_src[in_bounds],
            ys_src[in_bounds]
        )
        rows_f = np.array(rows_f, dtype=int)
        cols_f = np.array(cols_f, dtype=int)
        h, w = band.shape
        ok = (rows_f >= 0) & (rows_f < h) & (cols_f >= 0) & (cols_f < w)
        raw = np.full(n_pts, np.nan)
        raw[ok] = band[rows_f[ok], cols_f[ok]]
        log(f"  → mode read() ({n_pts} pts, raster {ds.width}×{ds.height})")
    else:
        # ── Lecture pixel par pixel → minimal I/O ────────────────────────
        coords = list(zip(xs_src[in_bounds].tolist(), ys_src[in_bounds].tolist()))
        try:
            raw = np.array([v[0] for v in ds.sample(coords, indexes=1)], dtype=np.float64)
        except Exception as e:
            log(f"  Erreur d'échantillonnage : {e}", "WARN")
            return vals
        log(f"  → mode sample() ({n_pts} pts, raster {ds.width}×{ds.height})")

    mask_valid = np.isfinite(raw)
    if nodata is not None:
        mask_valid &= ~np.isclose(raw, nodata, atol=1e-3)
    mask_valid &= raw > NODATA_FALLBACK
    mask_valid &= (raw >= Z_MIN) & (raw <= Z_MAX)

    vals[in_bounds] = np.where(mask_valid, raw, np.nan)
    return vals


def normalize_to_points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Garantit que toutes les géométries sont des Point simples.
    Gère : Point, MultiPoint (→ premier point), autres (→ centroïde).
    Supprime également les géométries nulles.
    """
    # Suppression des géométries nulles
    n_null = gdf.geometry.isna().sum()
    if n_null > 0:
        log(f"{n_null} géométrie(s) nulle(s) supprimée(s)", "WARN")
        gdf = gdf.dropna(subset=["geometry"]).reset_index(drop=True)

    geom_types = gdf.geometry.geom_type.unique()
    log(f"Types de géométrie détectés : {list(geom_types)}")

    if all(t == "Point" for t in geom_types):
        return gdf  # rien à faire

    log("Géométries non-Point détectées → normalisation en cours …", "WARN")

    def to_point(geom):
        if geom is None:
            return None
        gtype = geom.geom_type
        if gtype == "Point":
            return geom
        if gtype == "MultiPoint":
            pts = list(geom.geoms)
            if pts:
                return pts[0]
        # LineString, Polygon, GeometryCollection, etc.
        log(f"  Géométrie '{gtype}' → centroïde utilisé", "WARN")
        return geom.centroid

    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].apply(to_point)
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=gdf.crs)
    log(f"Normalisation terminée — {len(gdf)} nœuds de type Point", "OK")
    return gdf


def main():
    t0 = datetime.now()
    print("\n" + "═" * 62)
    print("  CALAGE BATHYMÉTRIQUE MULTI-RASTERS — TELEMAC MESH NODES")
    print("═" * 62)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tmp_dir = os.path.join(OUTPUT_DIR, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # ── 1. Chargement du SHP ─────────────────────────────────────────────
    log(f"Chargement du maillage : {Path(NODES_SHP).name}")
    if not os.path.exists(NODES_SHP):
        log(f"Fichier introuvable : {NODES_SHP}", "ERR")
        sys.exit(1)

    gdf = gpd.read_file(NODES_SHP)
    log(f"{len(gdf)} nœuds chargés (SCR source : {gdf.crs})", "OK")

    # ── 2. Reprojection CRS ──────────────────────────────────────────────
    if gdf.crs is None:
        log("Aucun CRS renseigné dans le SHP — EPSG:2154 supposé", "WARN")
        gdf = gdf.set_crs(epsg=EPSG_OUT)
    elif gdf.crs.to_epsg() != EPSG_OUT:
        gdf = gdf.to_crs(epsg=EPSG_OUT)
        log(f"Nœuds reprojetés en EPSG:{EPSG_OUT}", "OK")

    # ── 3. Normalisation des géométries → Point simples ──────────────────
    gdf = normalize_to_points(gdf)

    # ── 4. Extraction des coordonnées locales ────────────────────────────
    xs_local = gdf.geometry.x.values.copy()
    ys_local = gdf.geometry.y.values.copy()

    if OFFSET_X != 0.0 or OFFSET_Y != 0.0:
        log(f"Translation locale → L93 réel  (+{OFFSET_X/1e6:.1f}M X, +{OFFSET_Y/1e6:.1f}M Y)")
        xs = xs_local + OFFSET_X
        ys = ys_local + OFFSET_Y
        log(f"  Exemple nœud #0 : ({xs_local[0]:.1f}, {ys_local[0]:.1f}) "
            f"→ ({xs[0]:.1f}, {ys[0]:.1f})", "OK")
    else:
        xs = xs_local
        ys = ys_local

    n = len(gdf)
    z_final   = np.full(n, np.nan)
    src_final = np.full(n, "", dtype=object)

    print()
    print(f"  {'Priorité':<6} {'Source':<22} {'Nœuds couverts':>14} {'Cumul couvert':>14}")
    print("  " + "─" * 60)

    # ── 5. Boucle sur les rasters ────────────────────────────────────────
    for rank, (raster_path, label, epsg_src, z_offset) in enumerate(RASTERS):
        z_off_str = f"{z_offset:+.4f} m" if z_offset != 0.0 else "aucun"
        fsize_mb = os.path.getsize(raster_path) / 1e6 if os.path.exists(raster_path) else 0
        log(f"Raster #{rank+1} : {label}  ({Path(raster_path).name}, {fsize_mb:.0f} Mo)  offset Z : {z_off_str}")

        if not os.path.exists(raster_path):
            log("  Fichier introuvable, ignoré.", "WARN")
            continue

        t_raster = datetime.now()
        try:
            ds = open_raster(raster_path, epsg_src, tmp_dir)
        except Exception as e:
            log(f"  Impossible d'ouvrir le raster : {e}", "ERR")
            continue

        with ds:
            log(f"  Dimensions : {ds.width}×{ds.height} px, res={ds.res[0]:.2f} m, CRS={ds.crs}")
            z_this = sample_raster(ds, xs, ys, epsg_src)
            if z_offset != 0.0:
                finite_mask = np.isfinite(z_this)
                z_this[finite_mask] += z_offset

        dt = (datetime.now() - t_raster).total_seconds()
        manquants_avant = np.isnan(z_final)
        apport = manquants_avant & np.isfinite(z_this)

        z_final[apport]   = z_this[apport]
        src_final[apport] = label

        n_apport   = int(np.sum(apport))
        n_couverts = int(np.sum(np.isfinite(z_final)))
        pct = 100 * n_couverts / n

        print(f"  #{rank+1:<5} {label:<22} {n_apport:>14} {n_couverts:>12} ({pct:5.1f}%)  [{dt:.1f}s]")

    print()

    # ── 6. Résumé ────────────────────────────────────────────────────────
    n_ok      = int(np.sum(np.isfinite(z_final)))
    n_missing = n - n_ok

    log(f"Nœuds avec bathymétrie     : {n_ok} / {n}  ({100*n_ok/n:.1f}%)", "OK")
    if n_missing > 0:
        log(f"Nœuds SANS bathymétrie     : {n_missing}", "WARN")
    else:
        log("Tous les nœuds sont couverts !", "OK")

    # ── 7. Construction du GeoDataFrame résultat ─────────────────────────
    gdf_out = gdf.copy()
    gdf_out["Z_bathy"]   = z_final
    gdf_out["src_bathy"] = src_final
    gdf_out["has_bathy"] = np.isfinite(z_final)
    gdf_out["X_local"]   = xs_local
    gdf_out["Y_local"]   = ys_local

    out_shp = os.path.join(OUTPUT_DIR, OUTPUT_SHP_NAME + ".shp")
    gdf_out.to_file(out_shp)
    log(f"SHP exporté (repère local TELEMAC) : {out_shp}", "OK")

    # ── 7 bis. Export CSV bathy retenue (repère local) ───────────────────
    gdf_bathy_ok = gdf_out[gdf_out["has_bathy"]]
    df_bathy = pd.DataFrame({
        "X_loc":   gdf_bathy_ok["X_local"].values,
        "Y_loc":   gdf_bathy_ok["Y_local"].values,
        "Z_bathy": gdf_bathy_ok["Z_bathy"].values,
    })

    out_csv_bathy = os.path.join(OUTPUT_DIR, OUTPUT_CSV_BATHY_NAME + ".csv")
    df_bathy.to_csv(
        out_csv_bathy,
        sep=";", decimal=",",
        index=False, float_format="%.4f",
        encoding="utf-8-sig",
    )
    log(f"CSV bathy retenue (X_loc;Y_loc;Z_bathy) : {out_csv_bathy}  "
        f"[{len(df_bathy)} nœuds]", "OK")

    # ── 8. Tableau nœuds manquants ───────────────────────────────────────
    gdf_missing = gdf_out[~gdf_out["has_bathy"]].copy()
    gdf_missing["X_L93"] = gdf_missing.geometry.x + OFFSET_X
    gdf_missing["Y_L93"] = gdf_missing.geometry.y + OFFSET_Y

    cols_export = ["X_local", "Y_local", "X_L93", "Y_L93"]
    for col in gdf_missing.columns:
        if col.lower() in ("id", "node_id", "fid", "nodenumber", "num_node"):
            if col not in cols_export:
                cols_export = [col] + cols_export

    df_missing = gdf_missing[cols_export].reset_index(drop=True)
    df_missing.index += 1

    out_csv = os.path.join(OUTPUT_DIR, "noeuds_manquants.csv")
    df_missing.to_csv(out_csv, sep=";", decimal=",", encoding="utf-8-sig")
    log(f"CSV nœuds manquants : {out_csv}", "OK")

    if n_missing > 0:
        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │          NŒUDS SANS BATHYMÉTRIE (extrait)           │")
        print("  └─────────────────────────────────────────────────────┘")
        print(df_missing.head(20).to_string())
        if n_missing > 20:
            print(f"  ... et {n_missing - 20} nœuds supplémentaires (voir CSV)")

    # ── 9. Carte de visualisation ─────────────────────────────────────────
    log("Génération de la carte bathymétrique …")

    gdf_ok       = gdf_out[gdf_out["has_bathy"]]
    gdf_miss_geo = gdf_out[~gdf_out["has_bathy"]]

    fig, ax = plt.subplots(figsize=(14, 10), facecolor="#0d1117")
    ax.set_facecolor("#0d1117")

    z_vals = gdf_ok["Z_bathy"].values
    if len(z_vals) > 0:
        vmin, vmax = np.percentile(z_vals, 2), np.percentile(z_vals, 98)
    else:
        vmin, vmax = Z_MIN, Z_MAX
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cmap = matplotlib.colormaps[COLORMAP]

    # Sous-échantillonnage pour la carte si > 500k points (performance matplotlib)
    N_DISPLAY = 500_000
    if len(gdf_ok) > N_DISPLAY:
        log(f"Carte : sous-échantillonnage à {N_DISPLAY:,} points pour l'affichage", "WARN")
        gdf_display = gdf_ok.sample(N_DISPLAY, random_state=42)
    else:
        gdf_display = gdf_ok

    scatter = ax.scatter(
        gdf_display["X_local"], gdf_display["Y_local"],
        c=gdf_display["Z_bathy"], cmap=cmap, norm=norm,
        s=1.5, alpha=0.85, linewidths=0, rasterized=True,
        label="Nœuds calés"
    )

    if len(gdf_miss_geo) > 0:
        ax.scatter(
            gdf_miss_geo["X_local"], gdf_miss_geo["Y_local"],
            c="#ff4444", s=5, alpha=0.9, marker="x",
            linewidths=0.8, label=f"Nœuds manquants ({len(gdf_miss_geo)})"
        )

    cbar = fig.colorbar(scatter, ax=ax, fraction=0.025, pad=0.015, aspect=35)
    cbar.set_label("Altitude Z [m]", color="white", fontsize=10, labelpad=10)
    cbar.ax.yaxis.set_tick_params(color="white", labelcolor="white", labelsize=8)
    cbar.outline.set_edgecolor("#555")

    sources = [s for s in np.unique(src_final) if s != ""]
    colors_src = plt.cm.Set2(np.linspace(0, 1, max(len(sources), 1)))
    legend_patches = [Patch(color=c, label=s) for s, c in zip(sources, colors_src)]
    if len(gdf_miss_geo) > 0:
        legend_patches.append(Patch(color="#ff4444", label=f"Manquants ({len(gdf_miss_geo)})"))

    ax.legend(
        handles=legend_patches,
        loc="lower left", fontsize=8,
        facecolor="#1a1f2e", edgecolor="#444",
        labelcolor="white", title="Source bathymétrique",
        title_fontsize=8, framealpha=0.85
    )

    ax.set_title(CARTE_TITRE, color="white", fontsize=13, pad=16,
                 fontfamily="monospace", fontweight="bold")
    ax.tick_params(colors="white", labelsize=7)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333")
    ax.set_xlabel("X — repère local TELEMAC [m]", color="#aaa", fontsize=8)
    ax.set_ylabel("Y — repère local TELEMAC [m]", color="#aaa", fontsize=8)
    ax.xaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_major_locator(MaxNLocator(6))

    stats_label = (
        f"{n_ok}/{n} nœuds calés ({100*n_ok/n:.1f}%)\n"
        f"Z : {z_vals.min():.2f} m → {z_vals.max():.2f} m"
        if len(z_vals) > 0 else f"0/{n} nœuds calés"
    )
    ax.annotate(
        stats_label,
        xy=(0.01, 0.99), xycoords="axes fraction",
        va="top", ha="left", fontsize=8, color="#ccc",
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.4", fc="#1a1f2e", ec="#444", alpha=0.85)
    )
    ax.annotate(
        f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        xy=(0.99, 0.01), xycoords="axes fraction",
        va="bottom", ha="right", fontsize=7, color="#666",
        fontfamily="monospace"
    )

    plt.tight_layout()
    out_png = os.path.join(OUTPUT_DIR, "carte_bathymetrie.png")
    fig.savefig(out_png, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    log(f"Carte exportée : {out_png}", "OK")

    # ── 10. Nettoyage des temporaires ─────────────────────────────────────
    shutil.rmtree(tmp_dir, ignore_errors=True)

    elapsed = (datetime.now() - t0).total_seconds()
    print()
    print(f"  {'═'*58}")
    print(f"  ✓  Terminé en {elapsed:.1f} s")
    print(f"     Sorties dans : {OUTPUT_DIR}")
    print(f"  {'═'*58}\n")


if __name__ == "__main__":
    main()
