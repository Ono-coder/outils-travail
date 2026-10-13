# -*- coding: utf-8 -*-
"""
elements_surcontraints.py

Repère les triangles surcontraints d'un maillage TELEMAC : triangles ayant
plus d'un côté sur la frontière du domaine (tous leurs nœuds sont des nœuds de
bord). Ces éléments sont à éliminer avant calcul.

Usage :
    python elements_surcontraints.py maillage.slf
    python elements_surcontraints.py maillage.slf --telemac C:/TELEMAC/V8P5/scripts/python3 --graphique carte.png
"""

import sys
import argparse
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.tri as mtri


def main():
    parser = argparse.ArgumentParser(description="Repérage des triangles surcontraints d'un maillage TELEMAC")
    parser.add_argument("maillage", help="Fichier de maillage Selafin (.slf)")
    parser.add_argument("-o", "--sortie",
                        help="Fichier XYZ des nœuds concernés (défaut : over_constrained_triangles.xyz "
                             "à côté du maillage)")
    parser.add_argument("--telemac",
                        help="Dossier scripts/python3 de TELEMAC, si l'environnement TELEMAC n'est pas chargé")
    parser.add_argument("--graphique", help="Enregistre la carte des triangles repérés (PNG)")
    args = parser.parse_args()

    if args.telemac:
        sys.path.append(args.telemac)
    from data_manip.extraction.telemac_file import TelemacFile

    maillage = Path(args.maillage)
    sortie = Path(args.sortie) if args.sortie else maillage.with_name("over_constrained_triangles.xyz")

    res = TelemacFile(str(maillage))
    telemac_mesh = {}
    telemac_mesh["mesh"] = np.vstack((res.meshx , res.meshy)).T
    telemac_mesh["ikle"] = res.ikle2

    triang = mtri.Triangulation( telemac_mesh["mesh"][:,0] , telemac_mesh["mesh"][:,1] , triangles = telemac_mesh["ikle"] )

    # Un côté sans voisin est un côté de frontière ; un triangle cité plusieurs
    # fois a plus d'un côté sur la frontière
    ind0 = np.where( triang.neighbors[:,0] == -1 )
    ind1 = np.where( triang.neighbors[:,1] == -1 )
    ind2 = np.where( triang.neighbors[:,2] == -1 )

    temp = np.hstack((ind0,ind1,ind2))

    ind3 , counts = np.unique(temp, return_counts=True )

    ind4 = ind3[counts!=1]
    the_triangles = triang.triangles[ind4]
    aa = the_triangles.flatten()

    xy_coord =  np.vstack( (telemac_mesh["mesh"][aa,0] , telemac_mesh["mesh"][aa,1] )).T

    np.savetxt( sortie , xy_coord )
    print(f"{len(ind4)} triangle(s) surcontraint(s) -> {sortie}")

    if args.graphique:
        plt.triplot(triang, 'b-', lw=0.1)
        plt.plot( telemac_mesh["mesh"][aa,0] , telemac_mesh["mesh"][aa,1] , 'r.',ms=10)
        plt.gca().set_aspect("equal")
        plt.savefig(args.graphique, dpi=200)
        print(f"Carte -> {args.graphique}")


if __name__ == "__main__":
    main()
