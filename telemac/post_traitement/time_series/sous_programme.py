"""
sous_programme.py

Extrait une variable d'un fichier Selafin de time series TELEMAC vers un CSV
(tabulation), en ne gardant qu'un pas de temps sur `target_timestep` secondes.
Appelé par main_batch.py pour chaque fichier ; utilisable seul :

    python sous_programme.py <input_slf> <output_csv> [target_timestep] [tolerance] [var_index]

Sans var_index, les variables disponibles sont listées puis le script s'arrête.
Le fichier Selafin est lu directement avec numpy (TELEMAC n'est pas requis).
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

def lire_selafin(chemin):
    """Lit un fichier Selafin d'un seul bloc avec numpy.

    Renvoie (varnames, x, y, times, data) où data est un tableau structuré dont
    le champ f"v{i}" contient, pour chaque pas de temps, les valeurs de la
    variable i aux npoin points. Un dernier pas de temps tronqué (calcul en
    cours) est ignoré.
    """
    with open(chemin, "rb") as f:
        # Endianness : le premier marqueur de record (titre) vaut 80
        endian = ">" if np.frombuffer(f.read(4), ">i4")[0] == 80 else "<"
        f.seek(0)
        i4 = np.dtype(endian + "i4")

        def record():
            n = int(np.frombuffer(f.read(4), i4)[0])
            contenu = f.read(n)
            f.read(4)
            return contenu

        record()                                            # titre
        nvar = int(np.frombuffer(record(), i4)[0])
        varnames = [record()[:16].decode("latin-1") for _ in range(nvar)]
        iparam = np.frombuffer(record(), i4)
        if iparam[9] == 1:
            record()                                        # date
        nelem, npoin, ndp, _ = np.frombuffer(record(), i4)
        record()                                            # ikle
        record()                                            # ipobo
        brut_x = record()
        ff = np.dtype(endian + ("f8" if len(brut_x) == 8 * npoin else "f4"))
        x = np.frombuffer(brut_x, ff).astype(float)
        y = np.frombuffer(record(), ff).astype(float)
        debut = f.tell()

    champs = [("m0", i4), ("t", ff), ("m1", i4)]
    for i in range(nvar):
        champs += [(f"a{i}", i4), (f"v{i}", ff, (int(npoin),)), (f"b{i}", i4)]
    dtype = np.dtype(champs)
    ntimes = (os.path.getsize(chemin) - debut) // dtype.itemsize
    data = np.memmap(chemin, dtype=dtype, mode="r", offset=debut, shape=(ntimes,))
    times = data["t"].astype(float)
    return varnames, x, y, times, data

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

    # Temps le plus proche de chaque cible par recherche dichotomique (temps
    # croissants) ; à égalité, l'indice le plus petit est retenu comme argmin
    right = np.clip(np.searchsorted(times_array, wanted_times), 1, len(times_array) - 1)
    left = right - 1
    dist_left = np.abs(times_array[left] - wanted_times)
    dist_right = np.abs(times_array[right] - wanted_times)
    nearest = np.where(dist_left <= dist_right, left, right)
    distances = np.minimum(dist_left, dist_right)
    if len(times_array) == 1:
        nearest = np.zeros(len(wanted_times), dtype=int)
        distances = np.abs(times_array[0] - wanted_times)

    accepted = distances <= tolerance
    accepted_count = int(accepted.sum())
    rejected_count = len(wanted_times) - accepted_count
    selected_indices = set(nearest[accepted].tolist())

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
        
        print("Ouverture fichier SLF...")
        try:
            varnames, x_coords, y_coords, times, data = lire_selafin(input_slf)
            print("OK Fichier SLF ouvert")
        except Exception as e:
            print(f"ERREUR: Ouverture SLF: {e}")
            sys.exit(1)

        # ------------------------------------------------------------------ #
        #  LISTING ou SÉLECTION de la variable                                #
        # ------------------------------------------------------------------ #
        if var_index_str is None:
            print("\nVariables disponibles dans le fichier SLF:")
            for i, vn in enumerate(varnames):
                print(f"  [{i}] {vn.strip()}")
            print("\nRelancez en ajoutant l'index souhaité comme 5e argument, ex:")
            print(f"  python sous_programme.py \"{input_slf}\" \"{output_csv}\" "
                  f"{target_timestep_str} {tolerance_str} <index_variable>")
            sys.exit(0)

        try:
            var_index = int(var_index_str)
            if var_index < 0 or var_index >= len(varnames):
                print(f"ERREUR: Index {var_index} invalide — doit être compris entre 0 et {len(varnames)-1}")
                print("Variables disponibles:")
                for i, vn in enumerate(varnames):
                    print(f"  [{i}] {vn.strip()}")
                sys.exit(1)
            varname = varnames[var_index]
            print(f"OK Variable sélectionnée: [{var_index}] '{varname.strip()}'")
        except ValueError:
            print(f"ERREUR: Index variable invalide: '{var_index_str}' (doit être un entier)")
            sys.exit(1)
        # ------------------------------------------------------------------ #

        npoints = len(x_coords)
        print("OK Métadonnées OK:")
        print(f"  Points:       {npoints}")
        print(f"  Pas de temps: {len(times)}")
        print(f"  Variable:     {varname.strip()}")

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

                # Lecture groupée des pas de temps retenus, puis écriture en bloc
                values = data[f"v{var_index}"][selected_indices].astype(float)
                t_sel = times[selected_indices]
                writer.writerows(
                    [str(t)] + [str(v) for v in row]
                    for t, row in zip(t_sel.tolist(), values.tolist())
                )
                exported_count = len(selected_indices)

            print("SUCCES Export réussi:")
            print(f"  Fichier: {output_csv}")
            print(f"  Lignes:  {exported_count}")
            if os.path.exists(output_csv):
                print(f"  Taille:  {os.path.getsize(output_csv)} bytes")

        except Exception as e:
            print(f"ERREUR: Export: {e}")
            sys.exit(1)

        print("=== TRAITEMENT TERMINÉ AVEC SUCCÈS ===")
        
    except Exception as e:
        print(f"ERREUR GÉNÉRALE: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    print(f"Arguments sys.argv: {sys.argv}")
    main()
