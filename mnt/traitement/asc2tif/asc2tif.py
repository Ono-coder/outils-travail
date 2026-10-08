"""
asc2tif.py

Script générique pour convertir tous les fichiers ASC d'un dossier
en fichiers GeoTIFF (.tif) avec traitement parallèle.

Format supporté: ESRI ASCII Grid (.asc)

Usage:
    python asc2tif.py
    
    Ou personnalisé:
    python asc2tif.py --input ./mes_asc/ --output ./mes_tif/

    Ou avec un fichier de configuration (les options en ligne de commande
    restent prioritaires sur le fichier) :
    python asc2tif.py --config config.toml
"""

import os
import sys
import argparse
import tomllib
import logging
from pathlib import Path
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count
import time

# ============================================================================
# CONFIGURATION DU LOGGING
# ============================================================================

def setup_logging():
    """Configure le logging"""
    logger = logging.getLogger('asc_to_tif')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
        file_handler = logging.FileHandler('conversion_asc_to_tif.log', encoding='utf-8')
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
# RECHERCHE DE GDAL_TRANSLATE
# ============================================================================

def find_gdal_translate(conda_prefix_secours=None):
    """Trouve gdal_translate dans l'environnement"""
    logger = logging.getLogger('asc_to_tif')
    
    # Essayer conda d'abord
    conda_prefix = os.environ.get('CONDA_PREFIX')
    if not conda_prefix:
        # Chemin de secours si pas dans conda ([gdal] conda_prefix du fichier de config)
        conda_prefix = conda_prefix_secours or ''
    
    # Chemins possibles
    possible_paths = [
        Path(conda_prefix) / 'Scripts' / 'gdal_translate.exe',
        Path(conda_prefix) / 'Library' / 'bin' / 'gdal_translate.exe'
    ]
    
    for path in possible_paths:
        if path.exists():
            logger.info(f"✓ gdal_translate: {path}")
            return str(path)
    
    logger.error("❌ gdal_translate introuvable")
    logger.error("   Installez GDAL ou activez l'environnement conda approprié")
    raise RuntimeError("gdal_translate non trouvé")

# ============================================================================
# CONVERSION ASC → TIF
# ============================================================================

def convert_single_asc(args):
    """
    Convertit un seul fichier ASC en TIF
    
    Args:
        args: tuple (asc_file, output_folder, gdal_translate_path, compression, epsg)
    
    Returns:
        tuple: (success, filename, message, processing_time)
    """
    asc_file, output_folder, gdal_translate_path, compression, epsg = args
    
    try:
        start_time = time.time()
        
        # Chemins
        input_path = Path(asc_file)
        output_filename = input_path.stem + '.tif'
        output_path = Path(output_folder) / output_filename
        
        # Vérifier si déjà converti
        if output_path.exists() and output_path.stat().st_size > 1000:
            size_mb = output_path.stat().st_size / (1024*1024)
            return True, output_filename, f"Déjà converti ({size_mb:.1f}MB)", 0
        
        # Construire la commande gdal_translate
        cmd = [
            gdal_translate_path,
            '-of', 'GTiff',
            '-co', f'COMPRESS={compression}',
            '-co', 'TILED=YES',
            '-co', 'BLOCKXSIZE=512',
            '-co', 'BLOCKYSIZE=512'
        ]
        
        # Ajouter projection si spécifiée
        if epsg:
            cmd.extend(['-a_srs', f'EPSG:{epsg}'])
        
        cmd.extend([
            str(input_path),
            str(output_path)
        ])
        
        # Exécuter la conversion
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes max par fichier
        )
        
        processing_time = time.time() - start_time
        
        # Vérifier le résultat
        if result.returncode != 0:
            return False, output_filename, f"Erreur GDAL: {result.stderr[:100]}", processing_time
        
        # Vérifier le fichier de sortie
        if not output_path.exists() or output_path.stat().st_size < 1000:
            return False, output_filename, "Fichier de sortie invalide", processing_time
        
        input_size = input_path.stat().st_size / (1024*1024)
        output_size = output_path.stat().st_size / (1024*1024)
        
        return True, output_filename, f"{input_size:.1f}MB→{output_size:.1f}MB en {processing_time:.1f}s", processing_time
    
    except subprocess.TimeoutExpired:
        return False, output_filename, "Timeout (>5min)", 300
    except Exception as e:
        return False, output_filename, f"Erreur: {str(e)}", 0

# ============================================================================
# CONVERSION BATCH
# ============================================================================

def convert_asc_folder(input_folder, output_folder, gdal_translate_path, 
                       compression='LZW', epsg=None, max_workers=None):
    """
    Convertit tous les fichiers ASC d'un dossier en TIF
    
    Args:
        input_folder: Dossier source contenant les .asc
        output_folder: Dossier de destination pour les .tif
        gdal_translate_path: Chemin vers gdal_translate
        compression: Type de compression (LZW, DEFLATE, NONE)
        epsg: Code EPSG optionnel (ex: 2154 pour Lambert 93)
        max_workers: Nombre de processus parallèles
    """
    logger = logging.getLogger('asc_to_tif')
    logger.info("=" * 70)
    logger.info("🔄 CONVERSION ASC → GEOTIFF")
    logger.info("=" * 70)
    
    # Rechercher les fichiers ASC
    input_path = Path(input_folder)
    if not input_path.exists():
        logger.error(f"❌ Dossier introuvable: {input_path}")
        return False
    
    asc_files = sorted(input_path.glob('*.asc'))
    
    if len(asc_files) == 0:
        logger.error(f"❌ Aucun fichier .asc trouvé dans {input_path}")
        return False
    
    # Créer le dossier de sortie
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    
    # Configuration
    if max_workers is None:
        max_workers = min(cpu_count() - 1, 6)
    
    logger.info(f"📂 Dossier source: {input_path}")
    logger.info(f"📂 Dossier destination: {Path(output_folder).resolve()}")
    logger.info(f"📊 Fichiers ASC trouvés: {len(asc_files)}")
    logger.info(f"🔧 Compression: {compression}")
    if epsg:
        logger.info(f"🌍 Projection: EPSG:{epsg}")
    logger.info(f"⚙️  Workers parallèles: {max_workers}")
    logger.info("")
    
    # Préparer les tâches
    tasks = []
    already_converted = 0
    
    for asc_file in asc_files:
        output_filename = asc_file.stem + '.tif'
        output_path = Path(output_folder) / output_filename
        
        if output_path.exists() and output_path.stat().st_size > 1000:
            already_converted += 1
            continue
        
        tasks.append((str(asc_file), output_folder, gdal_translate_path, compression, epsg))
    
    logger.info(f"📋 {already_converted} fichiers déjà convertis")
    logger.info(f"🔄 {len(tasks)} fichiers à traiter")
    
    if len(tasks) == 0:
        logger.info("✅ Tous les fichiers sont déjà convertis")
        return True
    
    logger.info("")
    logger.info("🚀 Démarrage de la conversion parallèle...")
    logger.info("")
    
    # Conversion parallèle
    successful = 0
    failed = 0
    skipped = already_converted
    total_processing_time = 0
    start_time = time.time()
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(convert_single_asc, task): task for task in tasks}
        
        for i, future in enumerate(as_completed(futures), 1):
            success, filename, message, proc_time = future.result()
            total_processing_time += proc_time
            
            if success:
                if "Déjà converti" in message:
                    skipped += 1
                    logger.info(f"[{i}/{len(tasks)}] ⏭️  {filename} - {message}")
                else:
                    successful += 1
                    logger.info(f"[{i}/{len(tasks)}] ✅ {filename} - {message}")
            else:
                failed += 1
                logger.error(f"[{i}/{len(tasks)}] ❌ {filename} - {message}")
            
            # Progression tous les 10 fichiers
            if i % 10 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed * 60
                eta_min = (len(tasks) - i) / rate if rate > 0 else 0
                logger.info(f"   Progression: {i}/{len(tasks)} ({i/len(tasks)*100:.0f}%) - "
                          f"{rate:.1f} fichiers/min - ETA: {eta_min:.0f}min")
    
    # Résumé final
    total_time = time.time() - start_time
    logger.info("")
    logger.info("=" * 70)
    logger.info("📊 RÉSUMÉ DE LA CONVERSION")
    logger.info("=" * 70)
    logger.info(f"✅ Succès: {successful} fichiers")
    logger.info(f"⏭️  Déjà présents: {skipped} fichiers")
    logger.info(f"❌ Échecs: {failed} fichiers")
    logger.info(f"⏱️  Temps total: {total_time/60:.1f}min")
    logger.info(f"⏱️  Temps traitement: {total_processing_time/60:.1f}min")
    if len(tasks) > 0:
        logger.info(f"📈 Débit moyen: {len(tasks)/(total_time/60):.1f} fichiers/min")
    logger.info("=" * 70)
    
    return failed == 0

# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(
        description='Convertit tous les fichiers ASC d\'un dossier en GeoTIFF',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  # Conversion basique
  python asc2tif.py
  
  # Personnalisé avec compression DEFLATE
  python asc2tif.py --input ./data/ --output ./tifs/ --compress DEFLATE
  
  # Avec projection Lambert 93
  python asc2tif.py --input ./data/ --epsg 2154
  
  # Avec plus de workers
  python asc2tif.py --workers 8
        """
    )
    
    parser.add_argument(
        '--config',
        help='Fichier de configuration TOML (voir config_exemple.toml) ; '
             'les options ci-dessous restent prioritaires'
    )
    
    parser.add_argument(
        '--input', '-i',
        default='./asc_data/',
        help='Dossier contenant les fichiers ASC (défaut: ./asc_data/)'
    )
    
    parser.add_argument(
        '--output', '-o',
        default='./tif_data/',
        help='Dossier de destination pour les TIF (défaut: ./tif_data/)'
    )
    
    parser.add_argument(
        '--compress', '-c',
        choices=['LZW', 'DEFLATE', 'NONE'],
        default='LZW',
        help='Type de compression (défaut: LZW)'
    )
    
    parser.add_argument(
        '--epsg', '-e',
        type=int,
        default=None,
        help='Code EPSG pour la projection (ex: 2154 pour Lambert 93, optionnel)'
    )
    
    parser.add_argument(
        '--workers', '-w',
        type=int,
        default=None,
        help='Nombre de processus parallèles (défaut: auto)'
    )
    
    # Les valeurs du fichier de configuration remplacent les défauts,
    # les options passées en ligne de commande restent prioritaires
    args_config, _ = parser.parse_known_args()
    cfg = {}
    if args_config.config:
        with open(args_config.config, 'rb') as f:
            cfg = tomllib.load(f)
        chemins = cfg.get('chemins', {})
        conversion = cfg.get('conversion', {})
        defauts = {'input': chemins.get('entree'),
                   'output': chemins.get('sortie'),
                   'compress': conversion.get('compression'),
                   'epsg': conversion.get('epsg'),
                   'workers': conversion.get('workers')}
        parser.set_defaults(**{k: v for k, v in defauts.items() if v is not None})
    args = parser.parse_args()
    if args.compress not in ('LZW', 'DEFLATE', 'NONE'):
        parser.error(f"compression invalide : {args.compress} (LZW, DEFLATE ou NONE)")
    
    # Setup logging
    logger = setup_logging()
    logger.info("=== CONVERSION ASC → GEOTIFF ===")
    logger.info("")
    
    try:
        # Trouver gdal_translate
        gdal_translate_path = find_gdal_translate(cfg.get('gdal', {}).get('conda_prefix'))
        logger.info("")
        
        # Lancer la conversion
        success = convert_asc_folder(
            args.input,
            args.output,
            gdal_translate_path,
            args.compress,
            args.epsg,
            args.workers
        )
        
        if success:
            logger.info("")
            logger.info("🎉 === CONVERSION TERMINÉE AVEC SUCCÈS ===")
            sys.exit(0)
        else:
            logger.error("")
            logger.error("❌ === CONVERSION TERMINÉE AVEC ERREURS ===")
            sys.exit(1)
    
    except Exception as e:
        logger.error(f"💥 Erreur fatale: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
