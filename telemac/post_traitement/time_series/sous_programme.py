"""
sous_programme.py

Extrait une variable d'un fichier Selafin de time series TELEMAC vers un CSV
(tabulation), en ne gardant qu'un pas de temps sur `target_timestep` secondes.
Appelé par main_batch.py pour chaque fichier ; utilisable seul :

    python sous_programme.py <input_slf> <output_csv> [target_timestep] [tolerance] [var_index]

Sans var_index, les variables disponibles sont listées puis le script s'arrête.
Le dossier scripts/python3 de TELEMAC est lu dans la variable d'environnement
TELEMAC_PYTHON si l'environnement TELEMAC n'est pas déjà chargé.
"""
import sys
import os
import csv
import traceback
import numpy as np

def safe_get_arg(index, default=None):
    try:
        if len(sys.argv) > index:
            arg = sys.argv[index]
            if isinstance(arg, list):
                arg = arg[0] if arg else default
            return str(arg) if arg is not None else default
        return default
    except Exception as e:
        print(f"Erreur argument {index}: {e}")
        return default

def select_nearest_times(times, target_timestep, tolerance):
    """Version robuste avec tolérance personnalisable"""
    if len(times) == 0:
        return []
    
    print(f"  Paramètres: pas={target_timestep}s, tolérance={tolerance}s")
    
    try:
        if hasattr(times, 'flatten'):
            times_array = times.flatten().astype(float)
        else:
            times_array = np.array(times, dtype=float)
        
        if times_array.ndim > 1:
            times_array = times_array.flatten()
            
    except Exception as e:
        print(f"  Conversion automatique échouée: {e}")
        times_list = []
        for i, t in enumerate(times):
            try:
                if isinstance(t, (list, tuple, np.ndarray)):
                    times_list.append(float(t[0]))
                else:
                    times_list.append(float(t))
            except Exception as te:
                print(f"  Erreur élément {i}: {te}")
                if i > 0:
                    times_list.append(times_list[-1])
                else:
                    times_list.append(0.0)
        
        times_array = np.array(times_list)
    
    min_time = float(times_array[0])
    max_time = float(times_array[-1])
    
    print(f"  Plage: {min_time:.2f}s à {max_time:.2f}s ({len(times_array)} points)")
    
    # Générer les temps cibles
    wanted_times = np.arange(min_time, max_time + target_timestep, target_timestep)
    
    selected_indices = set()
    accepted_count = 0
    rejected_count = 0
    
    for tgt in wanted_times:
        distances = np.abs(times_array - tgt)
        idx = np.argmin(distances)
        
        if distances[idx] <= tolerance:
            selected_indices.add(int(idx))
            accepted_count += 1
        else:
            rejected_count += 1
    
    # Toujours inclure premier et dernier
    selected_indices.add(0)
    selected_indices.add(len(times_array) - 1)
    
    result = sorted(selected_indices)
    reduction_factor = len(times_array) / len(result)
    
    print(f"  Résultat: {len(result)} points sélectionnés (réduction x{reduction_factor:.1f})")
    print(f"  Détails: {accepted_count} acceptés, {rejected_count} rejetés (tolérance)")
    
    return result

def main():
    try:
        print("=== DEBUT TRAITEMENT ===")
        
        input_slf        = safe_get_arg(1)
        output_csv       = safe_get_arg(2)
        target_timestep_str = safe_get_arg(3, "0")
        tolerance_str    = safe_get_arg(4, "0.05")
        var_index_str    = safe_get_arg(5, None)   # optionnel : index de la variable à extraire
        
        if not input_slf or not output_csv:
            print("Usage: python sous_programme.py <input_slf> <output_csv> [target_timestep] [tolerance] [var_index]")
            print("  Si var_index est omis, les variables disponibles sont listées puis le script quitte.")
            print(f"Arguments reçus: {sys.argv}")
            sys.exit(1)
        
        try:
            target_timestep = float(target_timestep_str)
        except ValueError:
            target_timestep = 0
        
        try:
            tolerance = float(tolerance_str)
        except ValueError:
            tolerance = 0.05
        
        print("OK Arguments OK")
        print(f"  Input:      {input_slf}")
        print(f"  Output:     {output_csv}")
        print(f"  Pas de temps: {target_timestep}s")
        print(f"  Tolérance:  {tolerance}s")
        print(f"  Var index:  {var_index_str if var_index_str is not None else '(non fourni — listing)'}")
        
        if not os.path.exists(input_slf):
            print(f"ERREUR: Fichier non trouvé: {input_slf}")
            sys.exit(1)
        print("OK Fichier d'entrée existe")
        
        output_dir = os.path.dirname(output_csv)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(f"OK Dossier créé: {output_dir}")
        
        print("Configuration TELEMAC...")
        # Dossier scripts/python3 de TELEMAC : variable d'environnement TELEMAC_PYTHON
        # (renseignée par main_batch.py), sinon environnement TELEMAC déjà chargé
        if os.environ.get("TELEMAC_PYTHON"):
            sys.path.append(os.environ["TELEMAC_PYTHON"])
        
        try:
            from data_manip.extraction.telemac_file import TelemacFile
            print("OK Import TelemacFile OK")
        except ImportError as e:
            print(f"ERREUR: Import TelemacFile: {e}")
            sys.exit(1)
        
        print("Ouverture fichier SLF...")
        try:
            slf = TelemacFile(input_slf)
            print("OK Fichier SLF ouvert")
        except Exception as e:
            print(f"ERREUR: Ouverture SLF: {e}")
            sys.exit(1)
        
        # ------------------------------------------------------------------ #
        #  LISTING ou SÉLECTION de la variable                                #
        # ------------------------------------------------------------------ #
        if var_index_str is None:
            print("\nVariables disponibles dans le fichier SLF:")
            for i, vn in enumerate(slf.varnames):
                print(f"  [{i}] {vn.strip()}")
            print("\nRelancez en ajoutant l'index souhaité comme 5e argument, ex:")
            print(f"  python sous_programme.py \"{input_slf}\" \"{output_csv}\" "
                  f"{target_timestep_str} {tolerance_str} <index_variable>")
            slf.close()
            sys.exit(0)
        
        try:
            var_index = int(var_index_str)
            if var_index < 0 or var_index >= len(slf.varnames):
                print(f"ERREUR: Index {var_index} invalide — doit être compris entre 0 et {len(slf.varnames)-1}")
                print("Variables disponibles:")
                for i, vn in enumerate(slf.varnames):
                    print(f"  [{i}] {vn.strip()}")
                slf.close()
                sys.exit(1)
            varname = slf.varnames[var_index]
            print(f"OK Variable sélectionnée: [{var_index}] '{varname.strip()}'")
        except ValueError:
            print(f"ERREUR: Index variable invalide: '{var_index_str}' (doit être un entier)")
            slf.close()
            sys.exit(1)
        # ------------------------------------------------------------------ #
        
        if len(slf.varnames) == 0:
            print("ERREUR: Aucune variable dans le fichier SLF")
            slf.close()
            sys.exit(1)
        
        print("Lecture métadonnées...")
        try:
            x_coords = slf.meshx
            y_coords = slf.meshy
            npoints  = slf.npoin2
            times    = slf.times
            
            print("OK Métadonnées OK:")
            print(f"  Points:       {npoints}")
            print(f"  Pas de temps: {len(times)}")
            print(f"  Variable:     {varname.strip()}")
            
        except Exception as e:
            print(f"ERREUR: Lecture métadonnées: {e}")
            slf.close()
            sys.exit(1)
        
        print("Filtrage des pas de temps...")
        try:
            if target_timestep > 0:
                selected_indices = select_nearest_times(times, target_timestep, tolerance)
            else:
                selected_indices = list(range(len(times)))
                print(f"OK Tous les {len(selected_indices)} pas de temps conservés")
        except Exception as e:
            print(f"ERREUR: Filtrage pas de temps: {e}")
            traceback.print_exc()
            slf.close()
            sys.exit(1)
        
        print("Construction en-tête...")
        header = ["temps"]
        for ip in range(npoints):
            x = round(x_coords[ip], 2)
            y = round(y_coords[ip], 2)
            header.append(f"X{x}_Y{y}")
        print(f"OK En-tête: {len(header)} colonnes")
        
        print("Export des données...")
        try:
            with open(output_csv, mode="w", newline='', encoding="utf-8") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerow(header)
                
                exported_count  = 0
                total_to_export = len(selected_indices)
                
                for i, it in enumerate(selected_indices):
                    if i % 50 == 0 or i == total_to_export - 1:
                        print(f"  Progress: {i+1}/{total_to_export}")
                    
                    try:
                        t = times[it]
                        if hasattr(t, '__len__') and not isinstance(t, str):
                            t = t[0]
                        
                        values = slf.get_data_value(varname, it)
                        row = [str(t)]
                        for ip in range(npoints):
                            row.append(str(values[ip]))
                        writer.writerow(row)
                        exported_count += 1
                    except Exception as e:
                        print(f"  Erreur pas de temps {it}: {e}")
                        continue
            
            print("SUCCES Export réussi:")
            print(f"  Fichier: {output_csv}")
            print(f"  Lignes:  {exported_count}")
            if os.path.exists(output_csv):
                print(f"  Taille:  {os.path.getsize(output_csv)} bytes")
            
        except Exception as e:
            print(f"ERREUR: Export: {e}")
            slf.close()
            sys.exit(1)
        
        try:
            slf.close()
            print("OK Fichier SLF fermé")
        except:
            pass
        
        print("=== TRAITEMENT TERMINÉ AVEC SUCCÈS ===")
        
    except Exception as e:
        print(f"ERREUR GÉNÉRALE: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    print(f"Arguments sys.argv: {sys.argv}")
    main()
