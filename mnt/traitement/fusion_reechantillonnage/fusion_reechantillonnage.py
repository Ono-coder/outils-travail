import rasterio
import os
import argparse
import glob
from pathlib import Path
from rasterio.merge import merge
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.errors import RasterioIOError
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def find_tiff_files(input_folder, pattern="*.tif"):
    """
    Trouve tous les fichiers TIFF dans le dossier d'entrée avec support de patterns multiples
    """
    input_path = Path(input_folder)
    tiff_extensions = ['*.tif', '*.tiff', '*.TIF', '*.TIFF']
    
    tiff_files = []
    for ext in tiff_extensions:
        files = list(input_path.glob(ext))
        tiff_files.extend(files)
    
    # Convertit en strings et supprime les doublons
    tiff_files = [str(f) for f in set(tiff_files)]
    tiff_files.sort()
    
    logger.info(f"Trouvé {len(tiff_files)} fichiers TIFF dans {input_folder}")
    return tiff_files

def validate_tiff_files(tiff_files):
    """
    Valide que tous les fichiers TIFF peuvent être ouverts et ont des propriétés compatibles
    """
    valid_files = []
    crs_list = []
    
    for fp in tiff_files:
        try:
            with rasterio.open(fp) as src:
                logger.info(f"Validation: {Path(fp).name} - {src.width}x{src.height}, {src.count} bandes, CRS: {src.crs}")
                crs_list.append(src.crs)
                valid_files.append(fp)
        except (RasterioIOError, Exception) as e:
            logger.warning(f"Fichier invalide ignoré {fp}: {e}")
    
    # Vérification de la cohérence des CRS
    unique_crs = list(set(crs_list))
    if len(unique_crs) > 1:
        logger.warning(f"Attention: Multiples CRS détectés: {unique_crs}")
        logger.info("Tous les fichiers seront reprojetés vers le premier CRS")
    
    return valid_files

def merge_tiff(tiff_files, output_file, method='first'):
    """
    Fusionne les fichiers TIFF avec gestion d'erreurs améliorée
    
    Args:
        tiff_files: Liste des chemins vers les fichiers TIFF
        output_file: Chemin du fichier de sortie
        method: Méthode de fusion ('first', 'last', 'min', 'max', 'mean')
    """
    logger.info("Début de la fusion des fichiers TIFF...")
    
    if not tiff_files:
        raise ValueError("Aucun fichier TIFF valide à fusionner")
    
    try:
        # Ouvre tous les fichiers source
        src_files_to_mosaic = []
        for fp in tiff_files:
            src = rasterio.open(fp)
            src_files_to_mosaic.append(src)
        
        logger.info(f"Ouverture de {len(src_files_to_mosaic)} fichiers réussie")
        
        # Définit la méthode de fusion
        method_map = {
            'first': 'first',
            'last': 'last', 
            'min': 'min',
            'max': 'max',
            'mean': 'mean'
        }
        
        # Fusion avec la méthode spécifiée
        mosaic, out_trans = merge(
            src_files_to_mosaic, 
            method=method_map.get(method, 'first'),
            dtype=src_files_to_mosaic[0].dtypes[0]
        )
        
        # Métadonnées du fichier de sortie
        out_meta = src_files_to_mosaic[0].meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": out_trans,
            "compress": "lzw",  # Compression pour réduire la taille
            "tiled": True,      # Optimisation pour les gros fichiers
            "blockxsize": 2000,
            "blockysize": 2000
        })
        
        # Sauvegarde du TIFF fusionné
        with rasterio.open(output_file, 'w', **out_meta) as dest:
            dest.write(mosaic)
        
        # Ferme tous les fichiers source
        for src in src_files_to_mosaic:
            src.close()
            
        logger.info(f"Fichier fusionné sauvegardé: {output_file}")
        logger.info(f"Dimensions finales: {mosaic.shape[2]}x{mosaic.shape[1]}")
        
        return output_file
        
    except Exception as e:
        logger.error(f"Erreur lors de la fusion: {e}")
        # Assure la fermeture des fichiers en cas d'erreur
        for src in src_files_to_mosaic:
            try:
                src.close()
            except:
                pass
        raise

def resample_image(input_raster_path, output_raster_path, cell_size=2, resampling_method='bilinear'):
    """
    Rééchantillonne l'image avec méthodes multiples
    
    Args:
        input_raster_path: Chemin du raster d'entrée
        output_raster_path: Chemin du raster de sortie
        cell_size: Taille de cellule en mètres
        resampling_method: Méthode de rééchantillonnage
    """
    logger.info(f"Début du rééchantillonnage (taille cellule: {cell_size}m)...")
    
    resampling_methods = {
        'nearest': Resampling.nearest,
        'bilinear': Resampling.bilinear,
        'cubic': Resampling.cubic,
        'average': Resampling.average,
        'mode': Resampling.mode
    }
    
    try:
        with rasterio.open(input_raster_path) as src:
            logger.info(f"Dimensions originales: {src.width}x{src.height}")
            
            # Calcule la nouvelle transformation et dimensions
            transform, width, height = calculate_default_transform(
                src.crs, src.crs, src.width, src.height, *src.bounds, 
                resolution=cell_size
            )
            
            logger.info(f"Nouvelles dimensions: {width}x{height}")
            
            # Définit les nouvelles métadonnées
            new_meta = src.meta.copy()
            new_meta.update({
                'transform': transform,
                'width': width,
                'height': height,
                'crs': src.crs,
                'compress': 'lzw',
                'tiled': True,
                'blockxsize': 2000,
                'blockysize': 2000
            })
            
            # Rééchantillonne le raster
            with rasterio.open(output_raster_path, 'w', **new_meta) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=src.crs,
                        resampling=resampling_methods.get(resampling_method, Resampling.bilinear)
                    )
                    
            logger.info(f"Fichier rééchantillonné sauvegardé: {output_raster_path}")
            
    except Exception as e:
        logger.error(f"Erreur lors du rééchantillonnage: {e}")
        raise

def process_geotiffs(input_folder, output_folder=None, cell_size=2, merge_method='first', 
                    resampling_method='bilinear', skip_resampling=False):
    """
    Traite tous les GeoTIFF d'un dossier avec options configurables
    """
    logger.info("Début du traitement des GeoTIFF...")
    
    # Trouve tous les fichiers TIFF
    tiff_files = find_tiff_files(input_folder)
    if not tiff_files:
        logger.error("Aucun fichier TIFF trouvé dans le dossier d'entrée.")
        return None, None
    
    # Valide les fichiers
    valid_files = validate_tiff_files(tiff_files)
    if not valid_files:
        logger.error("Aucun fichier TIFF valide trouvé.")
        return None, None
    
    # Crée le dossier de sortie
    if output_folder is None:
        output_folder = os.path.join(input_folder, 'outputs')
    
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    logger.info(f"Dossier de sortie: {output_folder}")
    
    # Noms des fichiers de sortie
    merged_output_file = os.path.join(output_folder, 'merged_image.tif')
    resampled_output_file = os.path.join(output_folder, f'merged_image_resampled_{cell_size}m.tif')
    
    try:
        # Fusion
        merge_tiff(valid_files, merged_output_file, method=merge_method)
        
        # Rééchantillonnage (optionnel)
        if not skip_resampling:
            resample_image(merged_output_file, resampled_output_file, 
                         cell_size=cell_size, resampling_method=resampling_method)
            return merged_output_file, resampled_output_file
        else:
            logger.info("Rééchantillonnage ignoré")
            return merged_output_file, None
            
    except Exception as e:
        logger.error(f"Erreur lors du traitement: {e}")
        return None, None

def main():
    """Fonction principale avec interface en ligne de commande"""
    parser = argparse.ArgumentParser(description="Fusionner et rééchantillonner des fichiers TIFF")
    parser.add_argument('-i', '--input', default='.', 
                       help='Dossier contenant les fichiers TIFF (défaut: dossier courant)')
    parser.add_argument('-o', '--output', 
                       help='Dossier de sortie (défaut: input/outputs)')
    parser.add_argument('-s', '--cell-size', type=float, default=2,
                       help='Taille de cellule pour le rééchantillonnage (défaut: 2)')
    parser.add_argument('-m', '--merge-method', choices=['first', 'last', 'min', 'max', 'mean'],
                       default='first', help='Méthode de fusion (défaut: first)')
    parser.add_argument('-r', '--resampling', choices=['nearest', 'bilinear', 'cubic', 'average', 'mode'],
                       default='bilinear', help='Méthode de rééchantillonnage (défaut: bilinear)')
    parser.add_argument('--skip-resampling', action='store_true',
                       help='Ignorer le rééchantillonnage')
    
    args = parser.parse_args()
    
    logger.info("=== Début du script de fusion TIFF ===")
    
    merged_file, resampled_file = process_geotiffs(
        input_folder=args.input,
        output_folder=args.output,
        cell_size=args.cell_size,
        merge_method=args.merge_method,
        resampling_method=args.resampling,
        skip_resampling=args.skip_resampling
    )
    
    if merged_file:
        logger.info("=== Script terminé avec succès ===")
        logger.info(f"Fichier fusionné: {merged_file}")
        if resampled_file:
            logger.info(f"Fichier rééchantillonné: {resampled_file}")
    else:
        logger.error("=== Échec du script ===")

if __name__ == "__main__":
    main()