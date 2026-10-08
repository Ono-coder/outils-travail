import os
import sys
import time
import argparse
import tomllib
import logging
import zipfile
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import geopandas as gpd

# ============================================================================
# CONFIGURATION
# ============================================================================

# Taille des paquets portail : 2x2 km (4 kacheln de 1x1 km chacune)
TILE_SIZE_KM = 2   # ne pas modifier — convention du portail LGL-BW

# URL de base du portail
BASE_URL = "https://opengeodata.lgl-bw.de/data/dgm"

# Valeurs par défaut, remplacées par la section [telechargement] du fichier
# de configuration (--config)
MAX_WORKERS = 3     # threads parallèles (modéré pour ne pas surcharger le portail)
TIMEOUT_S = 120     # timeout par requête (ZIP ~4-8 MB par paquet, prévoir large)
PRODUCT = "dgm1"    # dgm1 (1m) — dgm025 pour la version 0.25m si disponible


def charger_config():
    """Lit le fichier TOML passé via --config."""
    parser = argparse.ArgumentParser(
        description="Téléchargement DGM1 LGL-BW (portail OpenGeoData) sur l'emprise d'un GeoPackage")
    parser.add_argument("--config", required=True,
                        help="Fichier de configuration TOML (voir config_exemple.toml)")
    args = parser.parse_args()
    try:
        with open(args.config, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        sys.exit(f"Fichier de configuration introuvable : {args.config}")
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"Fichier de configuration invalide ({args.config}) : {e}")

# ============================================================================
# LOGGING
# ============================================================================

def setup_logging(log_file='dgm1_portail_download.log'):
    logger = logging.getLogger('dgm1_portail')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        try:
            ch.stream.reconfigure(encoding='utf-8')
        except Exception:
            pass
        logger.addHandler(ch)
    return logger

# ============================================================================
# REPROJECTION GPKG → EPSG:25832
# ============================================================================

def get_bounds_utm32(gpkg_path):
    """Lit le GeoPackage (tout CRS) et retourne l'enveloppe en EPSG:25832."""
    logger = logging.getLogger('dgm1_portail')
    gdf = gpd.read_file(gpkg_path)
    logger.info(f"📂 GeoPackage : {len(gdf)} géométrie(s), CRS = {gdf.crs}")
    if gdf.crs is None:
        raise ValueError("GeoPackage sans CRS défini.")
    gdf_utm = gdf.to_crs(epsg=25832)
    bounds = gdf_utm.total_bounds  # [minx, miny, maxx, maxy] en mètres
    logger.info(f"📍 Emprise EPSG:25832 :")
    logger.info(f"   X : {bounds[0]:.0f} → {bounds[2]:.0f}  (Δ={bounds[2]-bounds[0]:.0f} m)")
    logger.info(f"   Y : {bounds[1]:.0f} → {bounds[3]:.0f}  (Δ={bounds[3]-bounds[1]:.0f} m)")
    return bounds

# ============================================================================
# GÉNÉRATION DES PAQUETS (grille 2x2 km)
# ============================================================================

def get_portail_tiles(minx, miny, maxx, maxy):
    """
    Génère la grille de paquets 2x2 km en coordonnées km entiers UTM32N.

    La grille du portail LGL-BW est alignée sur une origine impaire :
    X = 401, 403, 405, ... (pas de 2 km, début impair)
    Y = 5340, 5342, ... (pas de 2 km)

    Algorithme : pour chaque borne, trouver le multiple de 2 km INFÉRIEUR
    le plus proche qui soit de même parité que l'origine (401 → impair en X).
    En pratique : arrondir à 2 km vers le bas, puis ajuster la parité.
    """
    tile_m = TILE_SIZE_KM * 1000  # 2000 m

    def snap(val_m, origine_km):
        """Trouve le coin de tuile le plus proche ≤ val_m, aligné sur la grille."""
        val_km = val_m / 1000
        # Arrondir vers le bas au multiple de 2 km
        snapped = int(val_km // 2) * 2
        # Ajuster si la parité ne correspond pas à l'origine
        if (snapped % 2) != (origine_km % 2):
            snapped -= 1
        return snapped

    # Origines de la grille déduites des exemples du portail
    ORIGINE_X_KM = 401   # impair
    ORIGINE_Y_KM = 5340  # pair

    x_start_km = snap(minx, ORIGINE_X_KM)
    y_start_km = snap(miny, ORIGINE_Y_KM)
    x_end_km   = snap(maxx, ORIGINE_X_KM) + 2
    y_end_km   = snap(maxy, ORIGINE_Y_KM) + 2

    tiles = []
    x_km = x_start_km
    while x_km <= x_end_km:
        y_km = y_start_km
        while y_km <= y_end_km:
            tiles.append({
                'x_km': x_km,
                'y_km': y_km,
                'zip_name': f"{PRODUCT}_32_{x_km}_{y_km}_2_bw.zip",
                'minx': x_km * 1000,
                'miny': y_km * 1000,
                'maxx': (x_km + 2) * 1000,
                'maxy': (y_km + 2) * 1000,
            })
            y_km += 2
        x_km += 2
    return tiles

# ============================================================================
# TÉLÉCHARGEMENT + EXTRACTION D'UN PAQUET
# ============================================================================

def download_and_extract(args):
    """
    Télécharge un ZIP du portail LGL-BW et extrait les 4 kacheln GeoTIFF.
    Retourne (succès, zip_name, message).
    """
    tile_info, output_folder, timeout = args
    zip_name = tile_info['zip_name']
    url = f"{BASE_URL}/{zip_name}"
    out_dir = Path(output_folder)

    try:
        # Les 4 kacheln 1x1 km dans le ZIP sont nommées avec les coins SW en km
        # Ex : paquet 413_5378 → kacheln 413_5378, 414_5378, 413_5379, 414_5379
        x_km = tile_info['x_km']
        y_km = tile_info['y_km']
        expected = [
            f"{PRODUCT}_32_{x_km}_{y_km}_1_bw.tif",
            f"{PRODUCT}_32_{x_km+1}_{y_km}_1_bw.tif",
            f"{PRODUCT}_32_{x_km}_{y_km+1}_1_bw.tif",
            f"{PRODUCT}_32_{x_km+1}_{y_km+1}_1_bw.tif",
        ]
        already = [f for f in expected if (out_dir / f).exists()
                   and (out_dir / f).stat().st_size > 100_000]
        if len(already) == 4:
            total_mb = sum((out_dir / f).stat().st_size for f in expected) / 1024**2
            return True, zip_name, f"Déjà extrait ({total_mb:.1f} MB)"

        # Télécharger le ZIP en mémoire
        t0 = time.time()
        r = requests.get(url, timeout=timeout, stream=True)

        if r.status_code == 404:
            return False, zip_name, "HTTP 404 — kachel hors emprise BW ou inexistante"
        if r.status_code != 200:
            return False, zip_name, f"HTTP {r.status_code}"

        content = r.content
        zip_size_mb = len(content) / 1024**2

        # Extraire le ZIP directement en mémoire → dossier de sortie
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            extracted = []
            for member in zf.namelist():
                # On ne garde que les .tif / .xyz (ignorer les dossiers)
                if member.endswith(('.tif', '.tiff', '.xyz', '.asc')):
                    # Extraire à plat dans output_folder (sans sous-dossier)
                    data = zf.read(member)
                    dest = out_dir / Path(member).name
                    with open(dest, 'wb') as f:
                        f.write(data)
                    extracted.append(dest.name)

        if not extracted:
            return False, zip_name, "ZIP vide ou format inattendu"

        duration = time.time() - t0
        total_mb = sum((out_dir / f).stat().st_size
                       for f in extracted if (out_dir / f).exists()) / 1024**2
        return True, zip_name, (f"{len(extracted)} fichier(s) extrait(s), "
                                f"{total_mb:.1f} MB en {duration:.1f}s")

    except requests.Timeout:
        return False, zip_name, "Timeout"
    except zipfile.BadZipFile:
        return False, zip_name, "ZIP corrompu"
    except Exception as e:
        return False, zip_name, f"Erreur : {e}"

# ============================================================================
# FUSION EN MOSAÏQUE
# ============================================================================

def create_mosaic(output_folder, output_mosaic, gdalbuildvrt_path, gdal_translate_path):
    """Fusionne toutes les kacheln extraites en un GeoTIFF mosaïque."""
    import subprocess
    logger = logging.getLogger('dgm1_portail')
    logger.info("=" * 70)
    logger.info("🔗 Fusion → mosaïque")
    logger.info("=" * 70)

    vrt_file       = Path(output_folder) / '_temp_mosaic.vrt'
    file_list_path = Path(output_folder) / '_temp_list.txt'

    try:
        # Chercher tous les TIF extraits (1x1 km natifs)
        tif_files = sorted(Path(output_folder).glob(f'{PRODUCT}_32_*.tif'))
        if len(tif_files) < 2:
            logger.warning(f"⚠️  {len(tif_files)} fichier(s) — fusion non nécessaire.")
            return False

        logger.info(f"📝 {len(tif_files)} kacheln à assembler...")
        with open(file_list_path, 'w', encoding='utf-8') as f:
            for p in tif_files:
                f.write(str(p.resolve()) + '\n')

        # VRT
        r1 = subprocess.run(
            [str(gdalbuildvrt_path), '-input_file_list', str(file_list_path), str(vrt_file)],
            capture_output=True, text=True, timeout=300
        )
        if r1.returncode != 0:
            logger.error(f"❌ gdalbuildvrt : {r1.stderr}")
            return False
        logger.info("✅ VRT créé")

        # GeoTIFF final compressé
        r2 = subprocess.run(
            [
                str(gdal_translate_path),
                '-co', 'COMPRESS=LZW',
                '-co', 'TILED=YES',
                '-co', 'BIGTIFF=IF_SAFER',
                '-co', 'PREDICTOR=2',
                str(vrt_file),
                str(output_mosaic),
            ],
            capture_output=True, text=True, timeout=1800
        )
        if r2.returncode != 0:
            logger.error(f"❌ gdal_translate : {r2.stderr}")
            return False

        if Path(output_mosaic).exists() and Path(output_mosaic).stat().st_size > 0:
            size_mb = Path(output_mosaic).stat().st_size / 1024**2
            logger.info(f"🎉 Mosaïque : {Path(output_mosaic).name} ({size_mb:.1f} MB)")
            return True

        logger.error("❌ Fichier mosaïque absent.")
        return False

    except Exception as e:
        logger.error(f"❌ Erreur fusion : {e}")
        return False
    finally:
        for tmp in [vrt_file, file_list_path]:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

# ============================================================================
# MAIN
# ============================================================================

def main():
    global MAX_WORKERS, TIMEOUT_S, PRODUCT
    cfg = charger_config()
    chemins = cfg.get("chemins", {})
    telech = cfg.get("telechargement", {})
    MAX_WORKERS = telech.get("max_workers", MAX_WORKERS)
    TIMEOUT_S = telech.get("timeout_s", TIMEOUT_S)
    PRODUCT = telech.get("produit", PRODUCT)

    logger = setup_logging(chemins.get("log", 'dgm1_portail_download.log'))
    logger.info("=" * 70)
    logger.info("=== Téléchargement DGM1 LGL-BW — Portail OpenGeoData ===")
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # PARAMÈTRES
    # ------------------------------------------------------------------
    gpkg_file     = chemins.get("emprise_gpkg", 'zone_rectangulaire.gpkg')
    output_folder = chemins.get("dossier_dalles", './dgm1_output/kacheln/')
    output_mosaic = chemins.get("mosaique", './dgm1_output/mosaique_dgm1_bw.tif')

    # Chercher GDAL (environnement conda actif, sinon [gdal] conda_prefix)
    conda_prefix = os.environ.get('CONDA_PREFIX') or \
                   cfg.get("gdal", {}).get("conda_prefix", "")

    def find_gdal(name):
        for sub in ('Scripts', Path('Library') / 'bin'):
            p = Path(conda_prefix) / sub / name
            if p.exists():
                return p
        return None

    gdalbuildvrt_path   = find_gdal('gdalbuildvrt.exe')
    gdal_translate_path = find_gdal('gdal_translate.exe')
    gdal_available = bool(gdalbuildvrt_path and gdal_translate_path)

    if gdal_available:
        logger.info(f"🔧 GDAL : {gdalbuildvrt_path.parent}")
    else:
        logger.warning("⚠️  GDAL non trouvé — fusion désactivée.")

    # ------------------------------------------------------------------
    # LECTURE GPKG + TUILES
    # ------------------------------------------------------------------
    logger.info(f"📖 Lecture GeoPackage : {gpkg_file}")
    bounds = get_bounds_utm32(gpkg_file)

    tiles = get_portail_tiles(*bounds)
    logger.info(f"🧩 {len(tiles)} paquet(s) 2×2 km identifié(s) "
                f"→ jusqu'à {len(tiles)*4} kacheln 1×1 km")

    # Estimation : ~4-8 MB par ZIP, ~4 fichiers TIF de ~1 MB chacun
    logger.info(f"💾 Volume estimé ZIP : ~{len(tiles)*6:.0f} MB — "
                f"TIF extraits : ~{len(tiles)*4:.0f} MB")

    # ------------------------------------------------------------------
    # TÉLÉCHARGEMENT PARALLÈLE
    # ------------------------------------------------------------------
    Path(output_folder).mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info(f"🚀 Démarrage — {MAX_WORKERS} threads")
    logger.info("=" * 70)

    successful, failed, skipped = 0, 0, 0
    tasks = [(tile, output_folder, TIMEOUT_S) for tile in tiles]
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(download_and_extract, task): task for task in tasks}

        for i, future in enumerate(as_completed(futures), 1):
            success, zip_name, message = future.result()

            if success:
                if "Déjà extrait" in message:
                    skipped += 1
                    logger.info(f"[{i}/{len(tiles)}] ⏭️  {zip_name} — {message}")
                else:
                    successful += 1
                    logger.info(f"[{i}/{len(tiles)}] ✅ {zip_name} — {message}")
            else:
                failed += 1
                logger.warning(f"[{i}/{len(tiles)}] ❌ {zip_name} — {message}")

    total_time = time.time() - t_start
    logger.info("=" * 70)
    logger.info(f"✅ Terminé en {total_time/60:.1f} min")
    logger.info(f"   Succès  : {successful}  |  Déjà là : {skipped}  |  Erreurs : {failed}")
    logger.info("=" * 70)

    # ------------------------------------------------------------------
    # FUSION
    # ------------------------------------------------------------------
    if gdal_available and (successful + skipped) > 0:
        create_mosaic(output_folder, output_mosaic,
                      str(gdalbuildvrt_path), str(gdal_translate_path))
    elif not gdal_available:
        logger.info(f"📁 Kacheln dans : {Path(output_folder).resolve()}")


if __name__ == "__main__":
    main()