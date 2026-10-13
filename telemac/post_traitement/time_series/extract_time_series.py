"""
extract_time_series.py

Exporte en CSV (tabulation, virgule décimale) les variables d'un fichier
Selafin de time series TELEMAC (mot-clé TIME SERIES FILE) : une ligne par pas
de temps, une colonne par variable et par point.

Usage :
    python extract_time_series.py monfichier.slf export.csv --variables 1,3
    python extract_time_series.py monfichier.slf export.csv --variables all --telemac C:/TELEMAC/V8P5/scripts/python3
Les arguments non fournis sont demandés à l'utilisateur.
"""

import sys
import os
import csv
import argparse

parser = argparse.ArgumentParser(description="Export CSV d'un fichier de time series TELEMAC")
parser.add_argument("slf", nargs="?", help="Fichier Selafin de time series")
parser.add_argument("csv", nargs="?", help="Fichier CSV de sortie")
parser.add_argument("--variables", help="Numéros des variables (ex. 1,3) ou 'all'")
parser.add_argument("--telemac",
                    help="Dossier scripts/python3 de TELEMAC, si l'environnement TELEMAC n'est pas chargé "
                         "(défaut : variable d'environnement TELEMAC_PYTHON)")
args = parser.parse_args()

# --- Accès aux modules TELEMAC ---
chemin_telemac = args.telemac or os.environ.get("TELEMAC_PYTHON")
if chemin_telemac:
    sys.path.append(chemin_telemac)

from data_manip.extraction.telemac_file import TelemacFile

# --- Nom du fichier SLF ---
input_slf = args.slf or input("Entrez le nom du fichier SLF à lire : ").strip()

# --- Nom du fichier CSV ---
output_csv = args.csv or input("Entrez le nom du fichier CSV de sortie (ex: export.csv) : ").strip()

# --- Ouvrir le fichier SLF ---
slf = TelemacFile(input_slf)

vars_names = slf.varnames
times = slf.times
npoints = slf.npoin2

print("\nVariables disponibles :")
for i, var in enumerate(vars_names):
    print(f"{i+1}: {var}")

# --- Choix des variables ---
choice = args.variables or input("\nEntrez les numéros des variables à extraire (séparés par des virgules, ou 'all' pour tout) : ").strip()

if choice.lower() == "all":
    selected_vars = vars_names
else:
    try:
        selected_indices = [int(x)-1 for x in choice.split(",")]
        selected_vars = [vars_names[i] for i in selected_indices if 0 <= i < len(vars_names)]
    except ValueError:
        print("⚠ Entrée invalide, toutes les variables seront exportées.")
        selected_vars = vars_names

print(f"\nVariables sélectionnées : {selected_vars}")
print("Nombre de pas de temps :", len(times))
print("Nombre de points :", npoints)

# --- Construction de l'entête : une colonne par point_id et variable ---
header = ["time"]
for var in selected_vars:
    for ip in range(npoints):
        header.append(f"{var}_P{ip}")

# --- Export CSV ---
with open(output_csv, mode="w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f, delimiter="\t")  # tabulation comme séparateur
    writer.writerow(header)

    for it, t in enumerate(times):
        print(f"Lecture pas de temps {it+1}/{len(times)} (t={t})...")
        row = [t]

        # Récupère toutes les variables sélectionnées pour ce pas de temps
        for var in selected_vars:
            values = slf.get_data_value(var, it)  # tableau [npoints]
            # Remplace le point par une virgule pour les décimales
            row.extend([str(val).replace('.', ',') for val in values])

        writer.writerow(row)

slf.close()
print(f"\n✅ Export terminé : {output_csv}")
