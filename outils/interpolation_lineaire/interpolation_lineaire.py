#!/usr/bin/env python3
"""
Script d'interpolation linéaire
Lit un fichier texte avec deux colonnes (x, y) et génère des points interpolés
avec un pas constant.
"""

import numpy as np
import argparse
import sys


def lire_donnees(fichier_entree):
    """
    Lit les données depuis un fichier texte.
    
    Args:
        fichier_entree: chemin du fichier d'entrée
        
    Returns:
        x, y: arrays numpy des abscisses et ordonnées
    """
    try:
        data = np.loadtxt(fichier_entree)
        if data.ndim != 2 or data.shape[1] != 2:
            raise ValueError("Le fichier doit contenir exactement 2 colonnes")
        
        x = data[:, 0]
        y = data[:, 1]
        
        # Vérifier que les x sont triés
        if not np.all(x[:-1] <= x[1:]):
            # Trier les données par x
            indices = np.argsort(x)
            x = x[indices]
            y = y[indices]
            print("Attention: les données ont été triées par abscisse croissante")
        
        return x, y
    
    except Exception as e:
        print(f"Erreur lors de la lecture du fichier: {e}")
        sys.exit(1)


def interpoler(x, y, pas, x_min=None, x_max=None):
    """
    Interpole linéairement les données avec un pas donné.
    
    Args:
        x, y: données originales
        pas: pas d'interpolation
        x_min: point initial d'interpolation (None = premier point des données)
        x_max: point final d'interpolation (None = dernier point des données)
        
    Returns:
        x_interp, y_interp: données interpolées
    """
    # Définir les bornes d'interpolation
    if x_min is None:
        x_min = x[0]
    if x_max is None:
        x_max = x[-1]
    
    # Vérifier que les bornes sont dans la plage des données
    if x_min < x[0] or x_max > x[-1]:
        print(f"Attention: les bornes d'interpolation [{x_min}, {x_max}] dépassent")
        print(f"           la plage des données [{x[0]}, {x[-1]}]")
        print(f"           L'interpolation sera une extrapolation pour ces zones.")
    
    if x_min >= x_max:
        raise ValueError("x_min doit être inférieur à x_max")
    
    # Créer les nouveaux points x avec le pas demandé
    x_interp = np.arange(x_min, x_max + pas, pas)
    
    # S'assurer que le dernier point est inclus
    if x_interp[-1] < x_max:
        x_interp = np.append(x_interp, x_max)
    
    # Interpolation linéaire (ou extrapolation si hors bornes)
    y_interp = np.interp(x_interp, x, y)
    
    return x_interp, y_interp


def ecrire_resultats(fichier_sortie, x, y):
    """
    Écrit les résultats dans un fichier texte.
    
    Args:
        fichier_sortie: chemin du fichier de sortie
        x, y: données à écrire
    """
    try:
        np.savetxt(fichier_sortie, np.column_stack((x, y)), 
                   fmt='%.6f', delimiter='\t')
        print(f"Résultats écrits dans: {fichier_sortie}")
        print(f"Nombre de points interpolés: {len(x)}")
    
    except Exception as e:
        print(f"Erreur lors de l'écriture du fichier: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Interpolation linéaire de données depuis un fichier texte'
    )
    parser.add_argument('fichier_entree', 
                        help='Fichier d\'entrée (2 colonnes: x y)')
    parser.add_argument('pas', type=float,
                        help='Pas d\'interpolation')
    parser.add_argument('-o', '--sortie', default='sortie_interpolee.txt',
                        help='Fichier de sortie (défaut: sortie_interpolee.txt)')
    parser.add_argument('--xmin', type=float, default=None,
                        help='Point initial d\'interpolation (défaut: premier point des données)')
    parser.add_argument('--xmax', type=float, default=None,
                        help='Point final d\'interpolation (défaut: dernier point des données)')
    
    args = parser.parse_args()
    
    # Lire les données
    print(f"Lecture de {args.fichier_entree}...")
    x, y = lire_donnees(args.fichier_entree)
    print(f"  {len(x)} points lus")
    print(f"  Plage x: [{x[0]:.3f}, {x[-1]:.3f}]")
    
    # Afficher les bornes d'interpolation
    x_min_interp = args.xmin if args.xmin is not None else x[0]
    x_max_interp = args.xmax if args.xmax is not None else x[-1]
    print(f"\nInterpolation:")
    print(f"  Pas = {args.pas}")
    print(f"  Bornes: [{x_min_interp:.3f}, {x_max_interp:.3f}]")
    
    # Interpoler
    x_interp, y_interp = interpoler(x, y, args.pas, args.xmin, args.xmax)
    
    # Écrire les résultats
    print(f"\nÉcriture des résultats...")
    ecrire_resultats(args.sortie, x_interp, y_interp)


if __name__ == "__main__":
    main()
