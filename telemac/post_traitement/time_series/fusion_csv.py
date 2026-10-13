"""
fusion_csv.py

Fusionne les CSV (tabulation) produits par main_batch.py en un seul fichier :
une ligne par temps, les colonnes de tous les fichiers côte à côte.

Usage :
    python fusion_csv.py --entree csv_intermediaires --sortie fusion.csv
"""
import csv
import glob
import os
from collections import defaultdict, OrderedDict

def fusion_native_python(input_folder="csv_intermediaires", output_csv="fusion_native.csv"):
    """Version pure Python sans pandas - souvent plus rapide"""
    
    csv_files = sorted(glob.glob(os.path.join(input_folder, "*.csv")))
    print(f"📁 {len(csv_files)} fichiers trouvés")
    
    # Dictionnaire global : {temps: {colonne: valeur}}
    all_data = defaultdict(dict)
    all_columns = set(['temps'])
    
    for i, file_path in enumerate(csv_files, 1):
        print(f"📖 [{i}/{len(csv_files)}] {os.path.basename(file_path)}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                
                for row in reader:
                    temps = row['temps']
                    
                    # Ajouter toutes les colonnes (sauf temps)
                    for col, val in row.items():
                        if col != 'temps' and val.strip():  # Ignorer les valeurs vides
                            all_data[temps][col] = val
                            all_columns.add(col)
        
        except Exception as e:
            print(f"  ⚠ Erreur: {e}")
            continue
    
    print(f"📊 {len(all_data)} pas de temps, {len(all_columns)-1} colonnes")
    
    # Trier les colonnes (temps en premier)
    sorted_columns = ['temps'] + sorted([c for c in all_columns if c != 'temps'])
    
    # Export
    print("💾 Export...")
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t')
        
        # En-tête
        writer.writerow(sorted_columns)
        
        # Données triées par temps
        sorted_times = sorted(all_data.keys(), key=lambda x: float(x.replace(',', '.')))
        
        for temps in sorted_times:
            row = [temps]
            for col in sorted_columns[1:]:  # Skip 'temps'
                row.append(all_data[temps].get(col, ''))
            writer.writerow(row)
    
    print(f"✅ Terminé: {output_csv}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Fusionne les CSV produits par main_batch.py en un seul fichier trié par temps")
    parser.add_argument("--entree", default="csv_intermediaires",
                        help="Dossier des CSV à fusionner (défaut : csv_intermediaires)")
    parser.add_argument("--sortie", default="fusion_native.csv",
                        help="CSV fusionné (défaut : fusion_native.csv)")
    args = parser.parse_args()
    fusion_native_python(args.entree, args.sortie)
