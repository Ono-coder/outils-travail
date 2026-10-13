import os
import zipfile
import shutil
from pathlib import Path

def extraire_xyz_des_zip(dossier_source, dossier_destination, nettoyer_destination=False):
    """
    Extrait tous les fichiers .xyz des archives ZIP (même dans les sous-dossiers)
    et les copie vers un dossier de destination commun.
    
    Args:
        dossier_source: Dossier contenant les fichiers .zip
        dossier_destination: Dossier où seront copiés tous les .xyz
        nettoyer_destination: Si True, vide le dossier de destination avant extraction
    """
    
    # Vérifications initiales
    if not os.path.isdir(dossier_source):
        print(f"Erreur : Le dossier source '{dossier_source}' n'existe pas.")
        return
    
    # Créer le dossier de destination s'il n'existe pas
    os.makedirs(dossier_destination, exist_ok=True)
    
    # Optionnel : nettoyer le dossier de destination
    if nettoyer_destination and os.path.exists(dossier_destination):
        print(f"Nettoyage du dossier de destination...")
        for fichier in os.listdir(dossier_destination):
            chemin_fichier = os.path.join(dossier_destination, fichier)
            try:
                if os.path.isfile(chemin_fichier):
                    os.unlink(chemin_fichier)
            except Exception as e:
                print(f"  Erreur lors de la suppression de {fichier}: {e}")
    
    print(f"--- Extraction des fichiers .xyz ---")
    print(f"Source      : {dossier_source}")
    print(f"Destination : {dossier_destination}\n")
    
    # Rechercher tous les fichiers .zip dans le dossier source
    fichiers_zip = [f for f in os.listdir(dossier_source) if f.lower().endswith('.zip')]
    
    if not fichiers_zip:
        print("Aucun fichier .zip trouvé dans le dossier source.")
        return
    
    print(f"Fichiers .zip trouvés : {len(fichiers_zip)}\n")
    
    compteur_xyz = 0
    fichiers_extraits = []
    
    # Parcourir chaque archive ZIP
    for nom_zip in fichiers_zip:
        chemin_zip = os.path.join(dossier_source, nom_zip)
        print(f"Traitement de : {nom_zip}")
        
        try:
            with zipfile.ZipFile(chemin_zip, 'r') as zip_ref:
                # Lister tous les fichiers dans l'archive
                liste_fichiers = zip_ref.namelist()
                
                # Filtrer uniquement les fichiers .xyz
                fichiers_xyz = [f for f in liste_fichiers if f.lower().endswith('.xyz') and not f.startswith('__MACOSX')]
                
                if not fichiers_xyz:
                    print(f"  -> Aucun fichier .xyz trouvé dans cette archive")
                    continue
                
                print(f"  -> {len(fichiers_xyz)} fichier(s) .xyz trouvé(s)")
                
                # Extraire chaque fichier .xyz
                for fichier_xyz in fichiers_xyz:
                    # Extraire juste le nom du fichier (sans le chemin des sous-dossiers)
                    nom_fichier = os.path.basename(fichier_xyz)
                    
                    # Vérifier si un fichier avec le même nom existe déjà
                    chemin_destination = os.path.join(dossier_destination, nom_fichier)
                    
                    if os.path.exists(chemin_destination):
                        # Ajouter un suffixe pour éviter les écrasements
                        base, ext = os.path.splitext(nom_fichier)
                        compteur = 1
                        while os.path.exists(chemin_destination):
                            nom_fichier = f"{base}_{compteur}{ext}"
                            chemin_destination = os.path.join(dossier_destination, nom_fichier)
                            compteur += 1
                        print(f"     ⚠ Renommé en : {nom_fichier} (doublon évité)")
                    
                    # Extraire le fichier
                    with zip_ref.open(fichier_xyz) as source, open(chemin_destination, 'wb') as dest:
                        shutil.copyfileobj(source, dest)
                    
                    print(f"     ✓ {nom_fichier}")
                    compteur_xyz += 1
                    fichiers_extraits.append(nom_fichier)
        
        except zipfile.BadZipFile:
            print(f"  -> Erreur : {nom_zip} n'est pas un fichier ZIP valide")
        except Exception as e:
            print(f"  -> Erreur : {e}")
        
        print()  # Ligne vide entre chaque archive
    
    # Résumé final
    print("=" * 60)
    print(f"RÉSUMÉ :")
    print(f"  Archives traitées : {len(fichiers_zip)}")
    print(f"  Fichiers .xyz extraits : {compteur_xyz}")
    print(f"  Destination : {dossier_destination}")
    print("=" * 60)
    
    if compteur_xyz > 0:
        print(f"\n✓ Extraction terminée avec succès !")
        print(f"\nVous pouvez maintenant lancer xyz2tif.py sur le dossier :")
        print(f"  {dossier_destination}")
    else:
        print("\n⚠ Aucun fichier .xyz n'a été extrait.")


# --- Exécution ---
if __name__ == "__main__":
    import argparse
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    parser = argparse.ArgumentParser(
        description="Extrait tous les fichiers .xyz des archives ZIP d'un dossier vers un dossier commun")
    parser.add_argument("--config", help="Fichier de configuration TOML (voir config_exemple.toml)")
    parser.add_argument("--source", help="Dossier contenant les fichiers .zip")
    parser.add_argument("--destination", help="Dossier où copier les .xyz extraits")
    parser.add_argument("--nettoyer", action="store_true",
                        help="Vider le dossier de destination avant l'extraction")
    args = parser.parse_args()

    cfg = {}
    if args.config:
        with open(args.config, "rb") as f:
            cfg = tomllib.load(f).get("extraction_zip", {})
    source = args.source or cfg.get("source")
    destination = args.destination or cfg.get("destination")
    nettoyer = args.nettoyer or cfg.get("nettoyer", False)
    if not source or not destination:
        parser.error("préciser --source et --destination (ou [extraction_zip] dans la configuration)")

    extraire_xyz_des_zip(source, destination, nettoyer)
