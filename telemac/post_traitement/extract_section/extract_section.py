def write_control_sections_results(control_sections_res,
                                   control_sections_file=None,
                                   control_sections_excel_res="control_section.xlsx",
                                   number_sections=0,
                                   export=True):
    """
    Traite un fichier de résultats de sections de contrôle et l'exporte en Excel.
    
    Args:
        control_sections_res: Chemin vers le fichier de résultats
        control_sections_file: Chemin vers le fichier de configuration (optionnel)
        control_sections_excel_res: Nom du fichier Excel de sortie
        number_sections: Nombre de sections (si connu)
        export: Si True, exporte vers Excel, sinon retourne le DataFrame
    
    Returns:
        DataFrame pandas ou chemin du fichier Excel selon le paramètre export
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
                    
                    control_sections = pd.DataFrame(columns=columns)
                
                # Traitement des données
                if len(columns) == 0:
                    continue
                
                rows_per_timestep = m.ceil(len(columns) / 4) if len(columns) > 4 else 1
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
                    
                    control_sections.loc[len(control_sections)] = time_res
        
        # Conversion en float (avec gestion des erreurs)
        for col in control_sections.columns:
            control_sections[col] = pd.to_numeric(control_sections[col], errors='coerce')
        
        print(f"DataFrame créé avec {len(control_sections)} lignes et {len(control_sections.columns)} colonnes")
        
        if export:
            # Créer le répertoire si nécessaire
            os.makedirs(os.path.dirname(control_sections_excel_res) if os.path.dirname(control_sections_excel_res) else '.', exist_ok=True)
            control_sections.to_excel(control_sections_excel_res, index=False)
            print(f"Fichier Excel créé: {control_sections_excel_res}")
            return control_sections_excel_res
        else:
            return control_sections
            
    except Exception as e:
        print(f"Erreur lors du traitement: {e}")
        # Retourner un DataFrame vide en cas d'erreur
        return pd.DataFrame() if not export else None

if __name__ == "__main__":
    import argparse
    import tomllib

    parser = argparse.ArgumentParser(
        description="Export Excel des résultats de sections de contrôle TELEMAC")
    parser.add_argument("--config", required=True,
                        help="Fichier de configuration TOML (voir config_exemple.toml)")
    args = parser.parse_args()
    with open(args.config, "rb") as f:
        fichiers = tomllib.load(f).get("fichiers", {})

    control_sections_result_file = fichiers.get("resultat", "T2DSEO")                  # chemin du fichier résultat
    control_sections_txt = fichiers.get("sections", "control_sections.txt")            # fichier de configuration
    excel_save_filename = fichiers.get("sortie_excel", "control_sections.xlsx")        # fichier Excel de sortie
    
    try:
        result = write_control_sections_results(
            control_sections_result_file,
            control_sections_file=control_sections_txt,
            control_sections_excel_res=excel_save_filename,
            export=True
        )
        print(f"Succès! Résultat: {result}")
    except Exception as e:
        print(f"Erreur lors de l'exécution: {e}")