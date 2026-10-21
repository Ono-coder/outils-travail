import os
import sys
import time
import argparse
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from urllib.parse import urlencode
import geopandas as gpd
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# ============================================================================
# CONFIGURATION DU LOGGING
# ============================================================================

def setup_logging(log_file='lidar_mnt_download.log'):
    """Configure le logging"""
    logger = logging.getLogger('lidar_mnt_downloader')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        try:
            console_handler.stream.reconfigure(encoding='utf-8')
        except:
            pass
        logger.addHandler(console_handler)
    
    return logger

# ============================================================================
# RÉCUPÉRATION DES TUILES MNT
# ============================================================================

def get_mnt_tiles_from_bounds(minx, miny, maxx, maxy):
    """
    Génère la liste des tuiles MNT basée sur les coordonnées LAMB93
    Les tuiles font 1000m x 1000m
    """
    x_start = int(minx // 1000) * 1000
    y_start = int(miny // 1000) * 1000
    x_end = int(maxx // 1000 + 1) * 1000
    y_end = int(maxy // 1000 + 1) * 1000
    
    tiles = []
    x = x_start
    while x < x_end:
        y = y_start
        while y < y_end:
            tile_name = f"LHD_FXX_{int(x)}_{int(y)}_MNT_O_0M50_LAMB93_IGN69"
            tiles.append({
                'name': tile_name,
                'x': x,
                'y': y,
                'minx': x,
                'miny': y,
                'maxx': x + 1000,
                'maxy': y + 1000
            })
            y += 1000
        x += 1000
    
    return tiles

def get_tuiles_from_gpkg(gpkg_path):
    """Lit le GeoPackage et extrait les tuiles MNT correspondantes"""
    logger = logging.getLogger('lidar_mnt_downloader')
    
    try:
        gdf = gpd.read_file(gpkg_path)
        logger.info(f"📂 GeoPackage chargé: {len(gdf)} géométries")
        
        bounds = gdf.total_bounds
        logger.info(f"📍 Enveloppe globale (LAMB93):")
        logger.info(f"   X: {bounds[0]:.0f} → {bounds[2]:.0f}")
        logger.info(f"   Y: {bounds[1]:.0f} → {bounds[3]:.0f}")
        
        tiles = get_mnt_tiles_from_bounds(bounds[0], bounds[1], bounds[2], bounds[3])
        logger.info(f"🧩 {len(tiles)} tuiles MNT identifiées")
        
        return tiles
    
    except Exception as e:
        logger.error(f"❌ Erreur lecture GeoPackage: {e}")
        raise

# ============================================================================
# CONSTRUCTION ET TÉLÉCHARGEMENT WMS
# ============================================================================

def build_wms_url(tile_info, resolution=2000):
    """Construit l'URL WMS pour une tuile MNT"""
    base_url = "https://data.geopf.fr/wms-r"
    
    params = {
        'SERVICE': 'WMS',
        'VERSION': '1.3.0',
        'EXCEPTIONS': 'text/xml',
        'REQUEST': 'GetMap',
        'LAYERS': 'IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93',
        'FORMAT': 'image/geotiff',
        'STYLES': '',
        'CRS': 'EPSG:2154',
        'BBOX': f"{tile_info['minx']},{tile_info['miny']},{tile_info['maxx']},{tile_info['maxy']}",
        'WIDTH': resolution,
        'HEIGHT': resolution,
        'FILENAME': f"{tile_info['name']}.tif"
    }
    
    return f"{base_url}?{urlencode(params)}"

def download_mnt_tile(args):
    """Télécharge une tuile MNT unique (avec reprises en cas d'échec)"""
    tile_info, output_folder, timeout, resolution, tentatives = args
    logger = logging.getLogger('lidar_mnt_downloader')

    url = build_wms_url(tile_info, resolution)
    filename = f"{tile_info['name']}.tif"
    output_path = Path(output_folder) / filename
    # Écriture dans un .part renommé à la fin : un fichier .tif présent est forcément complet
    part_path = output_path.with_suffix('.tif.part')

    # Vérifier si déjà téléchargé
    if output_path.exists() and output_path.stat().st_size > 10000:
        return True, filename, f"Déjà téléchargé ({output_path.stat().st_size/(1024*1024):.1f}MB)"

    message = ""
    for tentative in range(1, tentatives + 1):
        try:
            start_time = time.time()
            response = requests.get(url, timeout=timeout, stream=True)

            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")

            # Vérifier que c'est un GeoTIFF valide
            content_type = response.headers.get('content-type', '')
            if 'xml' in content_type.lower():
                return False, filename, f"Erreur IGN: {response.text[:200]}"

            # Sauvegarder
            with open(part_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

            # Vérifier taille (complète et non vide)
            file_size = part_path.stat().st_size
            expected = response.headers.get('content-length')
            if expected and file_size != int(expected):
                raise RuntimeError(f"Fichier incomplet ({file_size}/{expected} bytes)")
            if file_size < 10000:
                raise RuntimeError(f"Fichier trop petit ({file_size} bytes)")

            part_path.replace(output_path)
            duration = time.time() - start_time
            size_mb = file_size / (1024*1024)
            return True, filename, f"{size_mb:.1f}MB en {duration:.1f}s"

        except Exception as e:
            # Les coupures en cours de flux remontent en ConnectionError, pas en Timeout
            message = "Timeout" if isinstance(e, requests.Timeout) else f"Erreur: {e}"
            if part_path.exists():
                part_path.unlink()
            if tentative < tentatives:
                logger.info(f"   ↻ {filename} - {message} - nouvelle tentative ({tentative + 1}/{tentatives})")
                time.sleep(10 * tentative)

    return False, filename, message

# ============================================================================
# FUSION DES MNT EN MOSAÏQUE (VERSION SIMPLE)
# ============================================================================

def create_mnt_mosaic(mnt_folder, output_mosaic, gdalbuildvrt_path, gdal_translate_path):
    """Fusionne les tuiles MNT - version simple et robuste"""
    logger = logging.getLogger('lidar_mnt_downloader')
    logger.info("=" * 70)
    logger.info("🔗 Fusion MNT")
    logger.info("=" * 70)
    
    import subprocess
    
    vrt_file = Path(mnt_folder) / 'temp_mosaique_mnt.vrt'
    file_list_path = Path(mnt_folder) / 'temp_mnt_list.txt'
    
    try:
        # Liste des fichiers TIF
        tif_files = sorted(Path(mnt_folder).glob('LHD_FXX_*.tif'))
        
        if len(tif_files) < 2:
            logger.warning(f"⚠️  Seulement {len(tif_files)} fichier(s) TIF - fusion non nécessaire")
            return False
        
        logger.info(f"📝 Fusion de {len(tif_files)} tuiles...")
        with open(file_list_path, 'w', encoding='utf-8') as f:
            for tif_file in tif_files:
                f.write(str(tif_file.resolve()) + '\n')
        
        # Créer VRT simple
        vrt_cmd = [
            str(gdalbuildvrt_path),
            '-input_file_list', str(file_list_path),
            str(vrt_file)
        ]
        
        logger.info("🏗️ Création du VRT...")
        vrt_result = subprocess.run(vrt_cmd, capture_output=True, text=True, timeout=300)
        if vrt_result.returncode != 0:
            logger.error(f"❌ Erreur VRT: {vrt_result.stderr}")
            return False
        
        logger.info("✅ VRT créé")
        
        # Convertir VRT → TIFF final
        logger.info("🔄 Conversion VRT → TIFF mosaïque...")
        translate_cmd = [
            str(gdal_translate_path),
            '-co', 'COMPRESS=LZW',
            '-co', 'TILED=YES',
            '-co', 'BIGTIFF=IF_SAFER',
            str(vrt_file),
            str(output_mosaic)
        ]
        
        translate_result = subprocess.run(translate_cmd, capture_output=True, text=True, timeout=1800)
        if translate_result.returncode != 0:
            logger.error(f"❌ Erreur conversion: {translate_result.stderr}")
            return False
        
        # Vérification finale
        if Path(output_mosaic).exists() and Path(output_mosaic).stat().st_size > 0:
            size_gb = Path(output_mosaic).stat().st_size / (1024**3)
            logger.info(f"🎉 Mosaïque créée: {Path(output_mosaic).name} ({size_gb:.2f}GB)")
            return True
        else:
            logger.error("❌ Fichier mosaïque non créé")
            return False
    
    except Exception as e:
        logger.error(f"❌ Erreur fusion: {e}")
        return False
    finally:
        # Nettoyage
        for temp_file in [vrt_file, file_list_path]:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except:
                pass

# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

def charger_config():
    """Lit le fichier TOML passé via --config"""
    parser = argparse.ArgumentParser(
        description="Téléchargement des MNT LiDAR HD IGN (WMS) sur l'emprise d'un GeoPackage")
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

def main():
    """Télécharge les MNT depuis l'IGN via WMS"""
    cfg = charger_config()
    chemins = cfg.get("chemins", {})
    telech = cfg.get("telechargement", {})

    logger = setup_logging(chemins.get("log", 'lidar_mnt_download.log'))
    logger.info("=== Téléchargement des MNT LiDAR HD IGN ===")
    
    # Configuration
    gpkg_file = chemins.get("emprise_gpkg", 'zone_rectangulaire.gpkg')
    output_folder = chemins.get("dossier_dalles", './lidar_output/mnt_data/')
    output_mosaic = chemins.get("mosaique", './lidar_output/mosaique_mnt.tif')
    
    # Chercher GDAL (environnement conda actif, sinon [gdal] conda_prefix)
    conda_prefix = os.environ.get('CONDA_PREFIX')
    if not conda_prefix:
        conda_prefix = cfg.get("gdal", {}).get("conda_prefix", "")
    
    gdalbuildvrt_path = Path(conda_prefix) / 'Scripts' / 'gdalbuildvrt.exe'
    gdal_translate_path = Path(conda_prefix) / 'Scripts' / 'gdal_translate.exe'
    
    if not gdalbuildvrt_path.exists():
        gdalbuildvrt_path = Path(conda_prefix) / 'Library' / 'bin' / 'gdalbuildvrt.exe'
    if not gdal_translate_path.exists():
        gdal_translate_path = Path(conda_prefix) / 'Library' / 'bin' / 'gdal_translate.exe'
    
    gdal_available = gdalbuildvrt_path.exists() and gdal_translate_path.exists()
    
    try:
        # Création dossier
        Path(output_folder).mkdir(parents=True, exist_ok=True)
        
        # Lire GeoPackage et identifier tuiles
        logger.info(f"📖 Lecture du GeoPackage: {gpkg_file}")
        tiles = get_tuiles_from_gpkg(gpkg_file)
        
        # ========== TÉLÉCHARGEMENT ==========
        logger.info("=" * 70)
        logger.info(f"🚀 Téléchargement de {len(tiles)} tuiles MNT")
        logger.info("=" * 70)
        
        max_workers = telech.get("max_workers", 6)
        successful = 0
        failed = 0
        skipped = 0
        
        timeout = telech.get("timeout_s", 120)
        resolution = telech.get("resolution_px", 2000)
        tentatives = telech.get("tentatives", 3)
        tasks = [(tile, output_folder, timeout, resolution, tentatives) for tile in tiles]
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(download_mnt_tile, task): task for task in tasks}
            
            for i, future in enumerate(as_completed(futures), 1):
                success, filename, message = future.result()
                
                if success:
                    if "Déjà téléchargé" in message:
                        skipped += 1
                        logger.info(f"[{i}/{len(tiles)}] ⏭️  {filename} - {message}")
                    else:
                        successful += 1
                        logger.info(f"[{i}/{len(tiles)}] ✅ {filename} - {message}")
                else:
                    failed += 1
                    logger.warning(f"[{i}/{len(tiles)}] ❌ {filename} - {message}")
                
                if i % 10 == 0:
                    elapsed = time.time() - start_time
                    rate = i / elapsed
                    eta_sec = (len(tiles) - i) / rate if rate > 0 else 0
                    logger.info(f"Progression: {i}/{len(tiles)} ({i/len(tiles)*100:.0f}%) - ETA: {eta_sec/60:.0f}min")
        
        # Résumé
        total_time = time.time() - start_time
        logger.info("=" * 70)
        logger.info(f"✅ Téléchargement terminé en {total_time/60:.1f}min")
        logger.info(f"   Succès: {successful}")
        logger.info(f"   Déjà présents: {skipped}")
        logger.info(f"   Erreurs: {failed}")
        logger.info("=" * 70)
        
        # ========== FUSION ==========
        if gdal_available and (successful + skipped) > 1:
            logger.info("")
            fusion_success = create_mnt_mosaic(
                output_folder,
                output_mosaic,
                str(gdalbuildvrt_path),
                str(gdal_translate_path)
            )
            if fusion_success:
                logger.info("🎉 === Téléchargement + Fusion réussis ===")
            else:
                logger.warning("⚠️  Téléchargement réussi mais fusion échouée")
        elif not gdal_available:
            logger.info("⚠️  GDAL non trouvé - fusion non disponible")
            logger.info(f"📁 Les fichiers MNT sont dans: {Path(output_folder).resolve()}")
        else:
            logger.info(f"📁 Fichier MNT unique dans: {Path(output_folder).resolve()}")
    
    except Exception as e:
        logger.error(f"💥 Erreur fatale: {e}")
        raise

if __name__ == "__main__":
    main()
