import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import numpy as np

# Paliers d'échantillonnage autour du pic de crue :
# (largeur de la fenêtre centrée sur le pic en s, pas d'extraction en s ;
# 0 = tous les pas de temps du calcul). Hors de toutes les fenêtres, un instant
# est gardé toutes les PAS_HORS_PIC secondes.
PALIERS = [(600, 0), (1200, 1), (1800, 5), (3600, 10)]
PAS_HORS_PIC = 60


def indices_plus_proches(times, cibles):
    """Indice du temps le plus proche de chaque cible (temps croissants)."""
    droite = np.clip(np.searchsorted(times, cibles), 0, len(times) - 1)
    gauche = np.clip(droite - 1, 0, len(times) - 1)
    return np.where(np.abs(times[gauche] - cibles) <= np.abs(times[droite] - cibles), gauche, droite)


def instants_autour_du_pic(times, serie, paliers=PALIERS, pas_hors_pic=PAS_HORS_PIC):
    """Indices des pas de temps à conserver pour une section, et instant du pic.

    Le pic est l'instant du maximum de |valeur|. Autour du pic, les paliers
    fixent le pas d'extraction (le plus fin s'applique là où les fenêtres se
    recouvrent) ; ailleurs, un instant toutes les pas_hors_pic secondes.
    Premier et dernier instants toujours inclus.
    """
    times = np.asarray(times, dtype=float)
    debut, fin = times[0], times[-1]
    garder = set(indices_plus_proches(times, np.arange(debut, fin + pas_hors_pic, pas_hors_pic)).tolist())
    garder.update((0, len(times) - 1))

    serie = np.abs(np.asarray(serie, dtype=float))
    if np.all(np.isnan(serie)):
        return np.array(sorted(garder)), None
    pic = times[np.nanargmax(serie)]
    for largeur, pas in paliers:
        t0, t1 = max(pic - largeur / 2, debut), min(pic + largeur / 2, fin)
        if pas <= 0:
            garder.update(np.nonzero((times >= t0) & (times <= t1))[0].tolist())
        else:
            # Cibles calées sur les multiples du pas
            cibles = np.arange(np.ceil(t0 / pas) * pas, t1 + pas / 2, pas)
            garder.update(indices_plus_proches(times, cibles).tolist())

    return np.array(sorted(garder)), pic


def nom_feuille(nom):
    """Nom de feuille Excel valide (31 caractères, sans []:*?/\\)."""
    for c in '[]:*?/\\':
        nom = nom.replace(c, "_")
    return nom[:31]


def write_control_sections_results(control_sections_res,
                                   control_sections_file=None,
                                   control_sections_excel_res="control_section.xlsx",
                                   number_sections=0,
                                   export=True,
                                   paliers=PALIERS,
                                   pas_hors_pic=PAS_HORS_PIC):
    """
    Traite un fichier de résultats de sections de contrôle et l'exporte en Excel.

    Args:
        control_sections_res: Chemin vers le fichier de résultats
        control_sections_file: Chemin vers le fichier de configuration (optionnel)
        control_sections_excel_res: Nom du fichier Excel de sortie
        number_sections: Nombre de sections (si connu)
        export: Si True, exporte vers Excel, sinon retourne le DataFrame
        paliers: Paliers d'échantillonnage autour du pic (None = tous les pas de temps)
        pas_hors_pic: Pas d'extraction hors des fenêtres autour du pic (s)

    Returns:
        Chemin du fichier Excel si export ; sinon DataFrame pandas, ou, avec
        échantillonnage, dictionnaire {section: DataFrame (TIME, section)}
    """
    import pandas as pd
    import math as m
    import os

    if control_sections_file is not None and os.path.exists(control_sections_file):
        try:
            with open(control_sections_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and line[0] != "#":
                        number_sections = int(line.split()[0])
                        print(f"Nombre de sections depuis le fichier: {number_sections}")
                        break
        except Exception as e:
            print(f"Erreur lors de la lecture du fichier de configuration: {e}")

    if number_sections == 0:
        print("Warning: Nombre de sections non déterminé, tentative d'auto-détection...")

    # Vérifier que le fichier de résultats existe
    if not os.path.exists(control_sections_res):
        raise FileNotFoundError(f"Le fichier de résultats {control_sections_res} n'existe pas.")

    # Traitement du fichier de résultats
    try:
        with open(control_sections_res, 'r') as res_control_sec:
            lines = res_control_sec.readlines()

        if len(lines) < 2:
            raise ValueError("Le fichier de résultats est trop court.")

        # Parser l'en-tête
        header = True
        header_number_lines = 0
        columns = []
        rows = []

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:  # Ignorer les lignes vides
                continue

            if line.startswith("TITLE"):
                header_number_lines += 1
                continue
            elif line.startswith("VARIABLES"):
                header_number_lines += 1
                # Extraire les variables après "VARIABLES ="
                variables_part = line.split("=", 1)[1].strip() if "=" in line else line.replace("VARIABLES", "").strip()
                columns.extend(variables_part.split())
                continue
            elif header and not line[0].isdigit() and not line.startswith("-"):
                # Ligne d'en-tête supplémentaire (variables qui continuent)
                header_number_lines += 1
                columns.extend(line.split())
                continue
            else:
                # Première ligne de données - fin de l'en-tête
                if header:
                    header = False
                    # Auto-détection du nombre de sections
                    if number_sections == 0 and len(columns) > 1:
                        number_sections = len(columns) - 1  # -1 car TIME est la première colonne
                        print(f"Auto-détection: {number_sections} sections trouvées")
                        print(f"Colonnes détectées: {columns}")
                    rows_per_timestep = m.ceil(len(columns) / 4) if len(columns) > 4 else 1

                # Traitement des données
                if len(columns) == 0:
                    continue

                data_line_index = i - header_number_lines

                if data_line_index % rows_per_timestep == 0:
                    time_res = []

                time_res.extend(line.split())

                if data_line_index % rows_per_timestep == rows_per_timestep - 1:
                    # Ajuster la longueur des données aux colonnes
                    if len(time_res) > len(columns):
                        time_res = time_res[:len(columns)]
                    elif len(time_res) < len(columns):
                        time_res.extend([None] * (len(columns) - len(time_res)))

                    rows.append(time_res)

        # Construction en une fois (l'ajout ligne à ligne d'un DataFrame est très lent)
        control_sections = pd.DataFrame(rows, columns=columns)

        # Conversion en float (avec gestion des erreurs)
        for col in control_sections.columns:
            control_sections[col] = pd.to_numeric(control_sections[col], errors='coerce')

        print(f"DataFrame créé avec {len(control_sections)} lignes et {len(control_sections.columns)} colonnes")

        if paliers and len(control_sections) > 1:
            # Chaque section a son propre pic, donc ses propres instants :
            # un tableau (TIME, section) par section
            print("Échantillonnage autour du pic de crue de chaque section...")
            col_temps = control_sections.columns[0]
            times = control_sections[col_temps].to_numpy(dtype=float)
            resultats = {}
            for col in control_sections.columns[1:]:
                garder, pic = instants_autour_du_pic(times, control_sections[col].to_numpy(dtype=float),
                                                     paliers, pas_hors_pic)
                resultats[col] = control_sections[[col_temps, col]].iloc[garder].reset_index(drop=True)
                pic_txt = f"{pic:.2f} s" if pic is not None else "non trouvé"
                print(f"  {col}: pic à {pic_txt}, {len(garder)} pas de temps conservés")
        else:
            resultats = None

        if export:
            # Créer le répertoire si nécessaire
            os.makedirs(os.path.dirname(control_sections_excel_res) if os.path.dirname(control_sections_excel_res) else '.', exist_ok=True)
            if resultats is None:
                control_sections.to_excel(control_sections_excel_res, index=False)
            else:
                with pd.ExcelWriter(control_sections_excel_res) as writer:
                    for col, df in resultats.items():
                        df.to_excel(writer, sheet_name=nom_feuille(col), index=False)
            print(f"Fichier Excel créé: {control_sections_excel_res}")
            return control_sections_excel_res
        else:
            return control_sections if resultats is None else resultats

    except Exception as e:
        print(f"Erreur lors du traitement: {e}")
        # Retourner un DataFrame vide en cas d'erreur
        return pd.DataFrame() if not export else None

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Export Excel des résultats de sections de contrôle TELEMAC")
    parser.add_argument("--config", required=True,
                        help="Fichier de configuration TOML (voir config_exemple.toml)")
    args = parser.parse_args()
    with open(args.config, "rb") as f:
        config = tomllib.load(f)
    fichiers = config.get("fichiers", {})
    echantillonnage = config.get("echantillonnage", {})

    control_sections_result_file = fichiers.get("resultat", "T2DSEO")                  # chemin du fichier résultat
    control_sections_txt = fichiers.get("sections", "control_sections.txt")            # fichier de configuration
    excel_save_filename = fichiers.get("sortie_excel", "control_sections.xlsx")        # fichier Excel de sortie
    paliers = [tuple(p) for p in echantillonnage.get("paliers", PALIERS)] \
        if echantillonnage.get("actif", True) else None

    try:
        result = write_control_sections_results(
            control_sections_result_file,
            control_sections_file=control_sections_txt,
            control_sections_excel_res=excel_save_filename,
            export=True,
            paliers=paliers,
            pas_hors_pic=echantillonnage.get("pas_hors_pic", PAS_HORS_PIC)
        )
        print(f"Succès! Résultat: {result}")
    except Exception as e:
        print(f"Erreur lors de l'exécution: {e}")
