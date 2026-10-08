"""
fusion.py

Script générique pour fusionner tous les fichiers TIF d'un dossier
en une seule mosaïque GeoTIFF.

Usage:
    python fusion.py
    
    Ou personnalisé:
    python fusion.py --input ./mes_tifs/ --output mosaique.tif

    Ou avec un fichier de configuration (les options en ligne de commande
    restent prioritaires sur le fichier) :
    python fusion.py --config config.toml
"""

import os
import sys
import argparse
import tomllib
import logging
from pathlib import Path
import subprocess

# ============================================================================
# CONFIGURATION DU LOGGING
# ============================================================================

def setup_logging():
    """Configure le logging"""
    logger = logging.getLogger('fusion_tif')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
        file_handler = logging.FileHandler('fusion_tif.log', encoding='utf-8')
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
# RECHERCHE DES OUTILS GDAL
# ============================================================================

def find_gdal_tools(conda_prefix_secours=None):
    """Trouve gdalbuildvrt et gdal_translate dans l'environnement"""
    logger = logging.getLogger('fusion_tif')
    
    # Essayer conda d'abord
    conda_prefix = os.environ.get('CONDA_PREFIX')
    if not conda_prefix:
        # Chemin de secours si pas dans conda ([gdal] conda_prefix du fichier de config)
        conda_prefix = conda_prefix_secours or ''
    
    # Chemins possibles
    possible_paths = [
        (Path(conda_prefix) / 'Scripts', Path(conda_prefix) / 'Scripts'),
        (Path(conda_prefix) / 'Library' / 'bin', Path(conda_prefix) / 'Library' / 'bin')
    ]
    
    gdalbuildvrt_path = None
    gdal_translate_path = None
    
    for scripts_dir, bin_dir in possible_paths:
        vrt_candidate = scripts_dir / 'gdalbuildvrt.exe'
        if not vrt_candidate.exists():
            vrt_candidate = bin_dir / 'gdalbuildvrt.exe'
        
        translate_candidate = scripts_dir / 'gdal_translate.exe'
        if not translate_candidate.exists():
            translate_candidate = bin_dir / 'gdal_translate.exe'
        
        if vrt_candidate.exists() and translate_candidate.exists():
            gdalbuildvrt_path = vrt_candidate
            gdal_translate_path = translate_candidate
            break
    
    if not gdalbuildvrt_path or not gdal_translate_path:
        logger.error("❌ GDAL tools introuvables")
        logger.error("   Installez GDAL ou activez l'environnement conda approprié")
        raise RuntimeError("GDAL tools non trouvés")
    
    logger.info(f"✓ gdalbuildvrt: {gdalbuildvrt_path}")
    logger.info(f"✓ gdal_translate: {gdal_translate_path}")
    
    return str(gdalbuildvrt_path), str(gdal_translate_path)

# ============================================================================
# FUSION DES TIF
# ============================================================================

def fusion_tif(input_folder, output_mosaic, gdalbuildvrt_path, gdal_translate_path, pattern='*.tif'):
    """
    Fusionne tous les TIF d'un dossier en une mosaïque
    
    Args:
        input_folder: Dossier contenant les fichiers TIF
        output_mosaic: Chemin du fichier mosaïque de sortie
        gdalbuildvrt_path: Chemin vers gdalbuildvrt
        gdal_translate_path: Chemin vers gdal_translate
        pattern: Pattern de recherche des fichiers (défaut: *.tif)
    """
    logger = logging.getLogger('fusion_tif')
    logger.info("=" * 70)
    logger.info("🔗 FUSION DES FICHIERS TIF")
    logger.info("=" * 70)
    
    vrt_file = Path(output_mosaic).parent / 'temp_mosaic.vrt'
    file_list_path = Path(output_mosaic).parent / 'temp_file_list.txt'
    
    try:
        # Liste des fichiers TIF
        input_path = Path(input_folder)
        if not input_path.exists():
            logger.error(f"❌ Dossier introuvable: {input_path}")
            return False
        
        tif_files = sorted(input_path.glob(pattern))
        
        if len(tif_files) == 0:
            logger.error(f"❌ Aucun fichier {pattern} trouvé dans {input_path}")
            return False
        
        if len(tif_files) == 1:
            logger.warning(f"⚠️  Un seul fichier trouvé, copie directe")
            import shutil
            shutil.copy2(tif_files[0], output_mosaic)
            logger.info(f"✅ Fichier copié: {output_mosaic}")
            return True
        
        logger.info(f"📂 Dossier source: {input_path}")
        logger.info(f"📊 Fichiers trouvés: {len(tif_files)}")
        logger.info(f"📄 Mosaïque cible: {output_mosaic}")
        
        # Créer la liste des fichiers
        with open(file_list_path, 'w', encoding='utf-8') as f:
            for tif_file in tif_files:
                f.write(str(tif_file.resolve()) + '\n')
                logger.info(f"   • {tif_file.name}")
        
        # Étape 1: Créer le VRT
        logger.info("")
        logger.info("🏗️ Étape 1/2: Création du VRT...")
        vrt_cmd = [
            gdalbuildvrt_path,
            '-input_file_list', str(file_list_path),
            str(vrt_file)
        ]
        
        vrt_result = subprocess.run(vrt_cmd, capture_output=True, text=True, timeout=600)
        if vrt_result.returncode != 0:
            logger.error(f"❌ Erreur gdalbuildvrt:")
            logger.error(vrt_result.stderr)
            return False
        
        logger.info("✅ VRT créé avec succès")
        
        # Étape 2: Convertir VRT → GeoTIFF
        logger.info("")
        logger.info("🔄 Étape 2/2: Conversion VRT → GeoTIFF...")
        
        # Créer le dossier de sortie si nécessaire
        Path(output_mosaic).parent.mkdir(parents=True, exist_ok=True)
        
        translate_cmd = [
            gdal_translate_path,
            '-co', 'COMPRESS=LZW',
            '-co', 'TILED=YES',
            '-co', 'BIGTIFF=IF_SAFER',
            '-co', 'BLOCKXSIZE=512',
            '-co', 'BLOCKYSIZE=512',
            str(vrt_file),
            str(output_mosaic)
        ]
        
        logger.info("⏳ Conversion en cours (peut prendre du temps)...")
        translate_result = subprocess.run(translate_cmd, capture_output=True, text=True, timeout=3600)
        
        if translate_result.returncode != 0:
            logger.error(f"❌ Erreur gdal_translate:")
            logger.error(translate_result.stderr)
            return False
        
        # Vérification finale
        output_path = Path(output_mosaic)
        if output_path.exists() and output_path.stat().st_size > 0:
            size_mb = output_path.stat().st_size / (1024**2)
            size_gb = output_path.stat().st_size / (1024**3)
            
            logger.info("")
            logger.info("=" * 70)
            logger.info("🎉 FUSION RÉUSSIE !")
            logger.info("=" * 70)
            logger.info(f"📄 Fichier: {output_path.name}")
            logger.info(f"📁 Emplacement: {output_path.parent.resolve()}")
            logger.info(f"💾 Taille: {size_gb:.2f} GB ({size_mb:.0f} MB)")
            logger.info(f"📊 Tuiles fusionnées: {len(tif_files)}")
            logger.info("=" * 70)
            return True
        else:
            logger.error("❌ Fichier mosaïque non créé ou vide")
            return False
    
    except subprocess.TimeoutExpired:
        logger.error("❌ Timeout lors de la fusion")
        return False
    except Exception as e:
        logger.error(f"❌ Erreur lors de la fusion: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Nettoyage des fichiers temporaires
        for temp_file in [vrt_file, file_list_path]:
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    logger.info(f"🧹 Nettoyé: {temp_file.name}")
            except:
                pass

# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(
        description='Fusionne tous les fichiers TIF d\'un dossier en une mosaïque',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  # Fusion basique (config par défaut)
  python fusion.py
  
  # Personnalisé
  python fusion.py --input ./mes_donnees/tifs/ --output resultat.tif
  
  # Avec pattern spécifique
  python fusion.py --input ./data/ --pattern "*_MNT_*.tif"

  # Avec un fichier de configuration
  python fusion.py --config config.toml
        """
    )
    
    parser.add_argument(
        '--config',
        help='Fichier de configuration TOML (voir config_exemple.toml) ; '
             'les options ci-dessous restent prioritaires'
    )
    
    parser.add_argument(
        '--input', '-i',
        default='./lidar_output/mnt_data/',
        help='Dossier contenant les fichiers TIF à fusionner (défaut: ./lidar_output/mnt_data/)'
    )
    
    parser.add_argument(
        '--output', '-o',
        default='./lidar_output/mosaique_finale.tif',
        help='Chemin du fichier mosaïque de sortie (défaut: ./lidar_output/mosaique_finale.tif)'
    )
    
    parser.add_argument(
        '--pattern', '-p',
        default='*.tif',
        help='Pattern de recherche des fichiers (défaut: *.tif)'
    )
    
    # Les valeurs du fichier de configuration remplacent les défauts,
    # les options passées en ligne de commande restent prioritaires
    args_config, _ = parser.parse_known_args()
    cfg = {}
    if args_config.config:
        with open(args_config.config, 'rb') as f:
            cfg = tomllib.load(f)
        chemins = cfg.get('chemins', {})
        defauts = {'input': chemins.get('entree'),
                   'output': chemins.get('sortie'),
                   'pattern': cfg.get('fusion', {}).get('pattern')}
        parser.set_defaults(**{k: v for k, v in defauts.items() if v is not None})
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging()
    logger.info("=== FUSION TIF UNIVERSELLE ===")
    logger.info("")
    
    try:
        # Trouver les outils GDAL
        gdalbuildvrt_path, gdal_translate_path = find_gdal_tools(
            cfg.get('gdal', {}).get('conda_prefix'))
        
        logger.info("")
        
        # Lancer la fusion
        success = fusion_tif(
            args.input,
            args.output,
            gdalbuildvrt_path,
            gdal_translate_path,
            args.pattern
        )
        
        if success:
            sys.exit(0)
        else:
            logger.error("❌ Échec de la fusion")
            sys.exit(1)
    
    except Exception as e:
        logger.error(f"💥 Erreur fatale: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
