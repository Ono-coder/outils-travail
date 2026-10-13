#!/usr/bin/env python3
"""
Programme pour télécharger les données SwissALTI3D via l'API STAC de geo.admin.ch
Utilise le système de coordonnées suisse MN95/LV95 (EPSG:2056)
"""

import requests
import json
import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import time
from urllib.parse import urljoin
import math

class SwissALTI3DDownloader:
    def __init__(self):
        self.base_url = "https://data.geo.admin.ch/api/stac/v0.9/"
        self.collection_id = "ch.swisstopo.swissalti3d"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SwissALTI3D-Downloader/1.0',
            'Accept': 'application/json'
        })
    
    def lv95_to_wgs84(self, east: float, north: float) -> Tuple[float, float]:
        """
        Convertit les coordonnées LV95 (CH1903+) en WGS84
        
        Args:
            east: Coordonnée Est en mètres (LV95)
            north: Coordonnée Nord en mètres (LV95)
            
        Returns:
            Tuple (longitude, latitude) en degrés WGS84
        """
        # Conversion approximative LV95 vers WGS84
        # Formule simplifiée basée sur les paramètres officiels swisstopo
        
        # Décalage vers le système auxiliaire
        y_aux = (east - 2600000) / 1000000
        x_aux = (north - 1200000) / 1000000
        
        # Calcul de la longitude (lambda)
        longitude = (2.6779094 
                    + 4.728982 * y_aux 
                    + 0.791484 * y_aux * x_aux 
                    + 0.1306 * y_aux * x_aux**2 
                    - 0.0436 * y_aux**3)
        
        # Calcul de la latitude (phi)  
        latitude = (16.9023892 
                   + 3.238272 * x_aux 
                   - 0.270978 * y_aux**2 
                   - 0.002528 * x_aux**2 
                   - 0.0447 * y_aux**2 * x_aux 
                   - 0.0140 * x_aux**3)
        
        # Conversion en degrés
        longitude = longitude * 100 / 36
        latitude = latitude * 100 / 36
        
        return longitude, latitude
    
    def convert_bbox_lv95_to_wgs84(self, bbox_lv95: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        """
        Convertit une emprise LV95 en emprise WGS84
        
        Args:
            bbox_lv95: (xmin, ymin, xmax, ymax) en coordonnées LV95
            
        Returns:
            (lon_min, lat_min, lon_max, lat_max) en coordonnées WGS84
        """
        xmin, ymin, xmax, ymax = bbox_lv95
        
        # Convertir les 4 coins
        lon_min, lat_min = self.lv95_to_wgs84(xmin, ymin)
        lon_max, lat_max = self.lv95_to_wgs84(xmax, ymax)
        
        # S'assurer que min < max
        if lon_min > lon_max:
            lon_min, lon_max = lon_max, lon_min
        if lat_min > lat_max:
            lat_min, lat_max = lat_max, lat_min
            
        return lon_min, lat_min, lon_max, lat_max
    
    def get_collection_info(self) -> Dict:
        """Récupère les informations de la collection SwissALTI3D"""
        url = f"{self.base_url}collections/{self.collection_id}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Erreur lors de la récupération des informations de collection: {e}")
            return {}
    
    def search_items(self, bbox_lv95: Tuple[float, float, float, float], 
                     limit: int = 1000) -> List[Dict]:
        """
        Recherche les items SwissALTI3D dans une emprise donnée
        
        Args:
            bbox_lv95: Emprise (xmin, ymin, xmax, ymax) en coordonnées MN95/LV95
            limit: Nombre maximum d'items à retourner
            
        Returns:
            Liste des items trouvés
        """
        search_url = f"{self.base_url}search"
        
        # Convertir l'emprise LV95 vers WGS84
        bbox_wgs84 = self.convert_bbox_lv95_to_wgs84(bbox_lv95)
        
        print(f"Emprise LV95: {bbox_lv95}")
        print(f"Emprise WGS84 convertie: {bbox_wgs84}")
        
        # Paramètres de recherche
        search_params = {
            "collections": [self.collection_id],
            "bbox": bbox_wgs84,  # Utiliser les coordonnées WGS84
            "limit": limit
        }
        
        try:
            response = self.session.post(
                search_url,
                json=search_params,
                headers={'Content-Type': 'application/json'}
            )
            response.raise_for_status()
            data = response.json()
            
            items = data.get('features', [])
            print(f"Trouvé {len(items)} items dans l'emprise spécifiée")
            
            return items
            
        except requests.exceptions.RequestException as e:
            print(f"Erreur lors de la recherche: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    print(f"Détail de l'erreur: {error_detail}")
                except:
                    print(f"Contenu de la réponse: {e.response.text}")
            return []
    
    def download_item(self, item: Dict, download_dir: str = "./downloads") -> bool:
        """
        Télécharge un item SwissALTI3D
        
        Args:
            item: Item STAC à télécharger
            download_dir: Répertoire de téléchargement
            
        Returns:
            True si le téléchargement a réussi, False sinon
        """
        item_id = item.get('id', 'unknown')
        assets = item.get('assets', {})
        
        # Créer le répertoire de téléchargement
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        
        success = True
        
        for asset_key, asset_info in assets.items():
            asset_url = asset_info.get('href')
            if not asset_url:
                continue
                
            # Détecter l'extension du fichier
            filename = f"{item_id}_{asset_key}"
            if 'title' in asset_info:
                filename = asset_info['title']
            elif asset_url.endswith('.tif') or asset_url.endswith('.tiff'):
                filename = f"{item_id}_{asset_key}.tif"
            elif asset_url.endswith('.xyz'):
                filename = f"{item_id}_{asset_key}.xyz"
            
            filepath = os.path.join(download_dir, filename)
            
            # Vérifier si le fichier existe déjà
            if os.path.exists(filepath):
                print(f"Fichier déjà existant: {filename}")
                continue
            
            try:
                print(f"Téléchargement de {filename}...")
                
                response = self.session.get(asset_url, stream=True)
                response.raise_for_status()
                
                # Téléchargement avec barre de progression simple
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            
                            if total_size > 0:
                                percent = (downloaded / total_size) * 100
                                print(f"\r  Progression: {percent:.1f}%", end="", flush=True)
                
                print(f"\n  ✓ Téléchargé: {filename}")
                
                # Pause entre les téléchargements pour être poli avec le serveur
                time.sleep(0.5)
                
            except requests.exceptions.RequestException as e:
                print(f"\n  ✗ Erreur lors du téléchargement de {filename}: {e}")
                success = False
                
                # Nettoyer le fichier partiellement téléchargé
                if os.path.exists(filepath):
                    os.remove(filepath)
        
        return success
    
    def download_by_bbox(self, bbox_lv95: Tuple[float, float, float, float], 
                        download_dir: str = "./downloads", 
                        max_items: int = 1000) -> None:
        """
        Télécharge tous les items SwissALTI3D dans une emprise donnée
        
        Args:
            bbox_lv95: Emprise (xmin, ymin, xmax, ymax) en coordonnées MN95/LV95
            download_dir: Répertoire de téléchargement
            max_items: Nombre maximum d'items à télécharger
        """
        print(f"Recherche des données SwissALTI3D dans l'emprise: {bbox_lv95}")
        print("Système de coordonnées d'entrée: CH1903+ / LV95 (EPSG:2056)")
        
        # Rechercher les items
        items = self.search_items(bbox_lv95, limit=max_items)
        
        if not items:
            print("Aucune donnée trouvée dans cette emprise.")
            return
        
        print(f"\nDébut du téléchargement de {len(items)} items...")
        
        downloaded_count = 0
        failed_count = 0
        
        for i, item in enumerate(items, 1):
            item_id = item.get('id', f'item_{i}')
            print(f"\n[{i}/{len(items)}] Traitement de l'item: {item_id}")
            
            if self.download_item(item, download_dir):
                downloaded_count += 1
            else:
                failed_count += 1
        
        print(f"\n{'='*50}")
        print(f"Téléchargement terminé:")
        print(f"  ✓ Succès: {downloaded_count}")
        print(f"  ✗ Échecs: {failed_count}")
        print(f"  📁 Répertoire: {download_dir}")

def main():
    """Télécharge les dalles swissALTI3D d'une emprise LV95 (ligne de commande ou --config)"""
    import argparse
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    parser = argparse.ArgumentParser(
        description="Téléchargement des dalles swissALTI3D (STAC swisstopo) sur une emprise MN95/LV95")
    parser.add_argument("--config", help="Fichier de configuration TOML (voir config_exemple.toml)")
    parser.add_argument("--emprise", type=float, nargs=4, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                        help="Emprise en MN95/LV95 (EPSG:2056)")
    parser.add_argument("--sortie", default="./swissalti3d_downloads",
                        help="Dossier de téléchargement (défaut : ./swissalti3d_downloads)")
    parser.add_argument("--max-items", type=int, default=1000,
                        help="Nombre maximal de dalles demandées (défaut : 1000)")

    # Les valeurs du fichier de configuration remplacent les défauts,
    # les options passées en ligne de commande restent prioritaires
    args_config, _ = parser.parse_known_args()
    if args_config.config:
        with open(args_config.config, "rb") as f:
            cfg = tomllib.load(f)
        defauts = {"emprise": cfg.get("emprise", {}).get("lv95"),
                   "sortie": cfg.get("sorties", {}).get("dossier"),
                   "max_items": cfg.get("telechargement", {}).get("max_items")}
        parser.set_defaults(**{k: v for k, v in defauts.items() if v is not None})
    args = parser.parse_args()
    if not args.emprise or len(args.emprise) != 4:
        parser.error("préciser l'emprise : --emprise XMIN YMIN XMAX YMAX "
                     "(ou [emprise] lv95 dans la configuration)")

    downloader = SwissALTI3DDownloader()
    downloader.download_by_bbox(
        bbox_lv95=tuple(args.emprise),
        download_dir=args.sortie,
        max_items=args.max_items,
    )

if __name__ == "__main__":
    main()
