"""
main_batch.py

Extraction par lot des time series TELEMAC : lance sous_programme.py sur chaque
fichier Selafin d'un dossier (fichiers T2DHI1 récupérés du dossier temporaire
de calcul) et écrit un CSV par fichier dans le dossier de sortie.

Usage :
    python main_batch.py --dossier ./T2DHI1 --pas 2.0 --tolerance 0.05
    python main_batch.py --config config.toml
Les paramètres non fournis sont demandés à l'utilisateur. Les options en ligne
de commande restent prioritaires sur le fichier.
"""

import os
import sys
import glob
import argparse
import subprocess

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# sous_programme.py est cherché à côté de ce script, quel que soit le dossier courant
SOUS_PROGRAMME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sous_programme.py")

def get_var_index(input_folder, var_index=None):
    """Lance sous_programme.py sur le premier fichier SLF pour lister les variables,
    puis demande à l'utilisateur de choisir."""
    slf_files = [f for f in glob.glob(os.path.join(input_folder, "*")) if os.path.isfile(f)]
    if not slf_files:
        return None, []

    first_slf = slf_files[0]
    print(f"\n  Lecture des variables depuis : {os.path.basename(first_slf)}")

    # Appel sans var_index → le sous-programme liste et quitte (sys.exit(0))
    result = subprocess.run(
        [sys.executable, SOUS_PROGRAMME, str(first_slf), "_dummy.csv", "0", "0.05"],
        capture_output=True,
        text=True,
        timeout=60,
        encoding='utf-8',
        errors='replace',
        cwd=os.getcwd()
    )

    # Extraire les lignes de listing ([0] VAR, [1] VAR, ...)
    lines = (result.stdout or "").split('\n')
    var_lines = [l for l in lines if l.strip().startswith('[')]

    if not var_lines:
        print("  ❌ Impossible de lire les variables du fichier SLF.")
        print(result.stdout)
        return None, slf_files

    print("\n=== Variables disponibles ===")
    for l in var_lines:
        print(f"  {l.strip()}")

    if var_index is not None:
        if 0 <= var_index <= len(var_lines) - 1:
            return var_index, slf_files
        print(f"  ❌ Index {var_index} invalide, doit être entre 0 et {len(var_lines) - 1}")

    while True:
        choice = input("\nIndex de la variable à extraire : ").strip()
        try:
            var_index = int(choice)
            # Vérifier que l'index existe dans la liste affichée
            max_index = len(var_lines) - 1
            if 0 <= var_index <= max_index:
                return var_index, slf_files
            else:
                print(f"  ❌ Index invalide, doit être entre 0 et {max_index}")
        except ValueError:
            print("  ❌ Entrez un entier")

def lire_arguments():
    parser = argparse.ArgumentParser(description="Extraction par lot des time series TELEMAC")
    parser.add_argument("--config", help="Fichier de configuration TOML (voir config_exemple.toml)")
    parser.add_argument("--dossier", help="Dossier des fichiers Selafin (T2DHI1…)")
    parser.add_argument("--sortie", default="csv_intermediaires",
                        help="Dossier des CSV produits (défaut : csv_intermediaires)")
    parser.add_argument("--pas", type=float, help="Pas de temps d'extraction (s)")
    parser.add_argument("--tolerance", type=float, help="Tolérance sur le pas de temps (s, défaut 0.05)")
    parser.add_argument("--variable", type=int, help="Index de la variable à extraire")
    parser.add_argument("--telemac", help="Dossier scripts/python3 de TELEMAC")

    # Les valeurs du fichier de configuration remplacent les défauts,
    # les options passées en ligne de commande restent prioritaires
    args_config, _ = parser.parse_known_args()
    if args_config.config:
        with open(args_config.config, "rb") as f:
            cfg = tomllib.load(f)
        ext = cfg.get("extraction", {})
        defauts = {"dossier": cfg.get("chemins", {}).get("dossier_slf"),
                   "sortie": cfg.get("chemins", {}).get("dossier_csv"),
                   "pas": ext.get("pas_de_temps"), "tolerance": ext.get("tolerance"),
                   "variable": ext.get("index_variable"),
                   "telemac": cfg.get("telemac", {}).get("python")}
        parser.set_defaults(**{k: v for k, v in defauts.items() if v is not None})
    return parser.parse_args()


def main():
    args = lire_arguments()
    if args.telemac:
        os.environ["TELEMAC_PYTHON"] = args.telemac   # transmis à sous_programme.py

    input_folder = args.dossier or input("Dossier SLF : ").strip()

    output_folder = args.sortie

    # Pas de temps
    print("\n=== Configuration du pas de temps ===")
    target_timestep_str = str(args.pas) if args.pas is not None else \
        input("Pas de temps souhaité pour l'extraction (ex: 2.0) : ").strip()
    try:
        target_timestep = float(target_timestep_str)
        if target_timestep <= 0:
            print("❌ Le pas de temps doit être positif")
            return
    except ValueError:
        print("❌ Valeur invalide, utilisation de 2.0s")
        target_timestep = 2.0

    # Tolérance
    print(f"\n=== Configuration de la tolérance ===")
    tolerance_input = str(args.tolerance) if args.tolerance is not None else \
        input(f"Tolérance pour la sélection [défaut: 0.05] : ").strip()
    try:
        tolerance = float(tolerance_input) if tolerance_input else 0.05
        if tolerance <= 0:
            print("❌ La tolérance doit être positive")
            return
    except ValueError:
        print("❌ Tolérance invalide, utilisation de 0.05s")
        tolerance = 0.05

    # Sélection de la variable — une seule fois avant la boucle
    print("\n=== Sélection de la variable à extraire ===")
    var_index, slf_files = get_var_index(input_folder, args.variable)
    if var_index is None:
        return

    print(f"\n✅ Configuration:")
    print(f"  - Extraction tous les {target_timestep}s")
    print(f"  - Tolérance:          ±{tolerance}s")
    print(f"  - Variable index:     {var_index}")

    os.makedirs(output_folder, exist_ok=True)
    print(f"\nDossier {output_folder} prêt")
    print(f"{len(slf_files)} fichiers trouvés")

    success_count = 0
    error_files = []

    for idx, slf_file in enumerate(slf_files, 1):
        base_name = os.path.splitext(os.path.basename(slf_file))[0]
        output_csv = os.path.join(output_folder, f"{base_name}_{idx:04d}.csv")

        print(f"\n[{idx}/{len(slf_files)}] Traitement: {os.path.basename(slf_file)}")

        try:
            if not os.path.exists(SOUS_PROGRAMME):
                print(f"  ❌ sous_programme.py non trouvé : {SOUS_PROGRAMME}")
                error_files.append(slf_file)
                continue

            cmd_args = [
                sys.executable, SOUS_PROGRAMME,
                str(slf_file),
                str(output_csv),
                str(target_timestep),
                str(tolerance),
                str(var_index)   # 5e argument
            ]

            print(f"  🔄 Commande: python sous_programme.py [fichier] [sortie] "
                  f"{target_timestep} {tolerance} {var_index}")

            result = subprocess.run(
                cmd_args,
                capture_output=True,
                text=True,
                timeout=300,
                encoding='utf-8',
                errors='replace',
                cwd=os.getcwd()
            )

            if result.returncode == 0:
                if os.path.exists(output_csv) and os.path.getsize(output_csv) > 0:
                    print(f"  ✅ Succès: {os.path.basename(output_csv)} ({os.path.getsize(output_csv)} bytes)")
                    success_count += 1
                elif os.path.exists(output_csv):
                    print(f"  ⚠ Fichier vide créé")
                    error_files.append(slf_file)
                else:
                    print(f"  ❌ Fichier de sortie non créé")
                    error_files.append(slf_file)
            else:
                print(f"  ❌ Erreur (code {result.returncode}):")
                if result.stdout:
                    for line in result.stdout.split('\n')[:5]:
                        if line.strip():
                            print(f"      {line}")
                if result.stderr:
                    for line in result.stderr.split('\n')[:5]:
                        if line.strip():
                            print(f"      {line}")
                error_files.append(slf_file)

        except subprocess.TimeoutExpired:
            print(f"  ⏰ Timeout (>5min)")
            error_files.append(slf_file)
        except Exception as e:
            print(f"  ❌ Exception: {e}")
            error_files.append(slf_file)

    print(f"\n" + "="*50)
    print(f"📊 RÉSUMÉ FINAL")
    print(f"✅ Succès:           {success_count}/{len(slf_files)}")
    print(f"❌ Échecs:           {len(error_files)}")
    print(f"📈 Taux de réussite: {(success_count/len(slf_files)*100):.1f}%")
    print(f"⚙️  Configuration:    {target_timestep}s ±{tolerance}s — variable [{var_index}]")

if __name__ == "__main__":
    main()
