#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extraction des debits aux frontieres liquides depuis un listing TELEMAC-2D (.out).

Recupere, pour chaque pas de temps du bilan de masse, la valeur
"FLUX BOUNDARY <n>: <valeur> M3/S" et l'associe a l'iteration et au temps (s).

Usage :
    python extract_flux.py listing.out                  # frontiere 6 par defaut
    python extract_flux.py listing.out -b 6 -o flux6.csv
    python extract_flux.py listing.out -b 2 4 6 --plot
"""

import argparse
import csv
import re
import sys
from pathlib import Path

# " ITERATION   1500    TIME:  4 MN  60.0000 S   (      300.0000 S)"
RE_ITER = re.compile(
    r"^\s*ITERATION\s+(\d+)\s+TIME:\s*(?P<reste>.*)$"
)
# valeur totale entre parentheses, en secondes
RE_TOTAL = re.compile(r"\(\s*([-+0-9.E+]+)\s*S\s*\)", re.IGNORECASE)
# decomposition D / H / MIN|MN / S si pas de parenthese (ex. iteration 0)
RE_COMPOSANTES = re.compile(
    r"(?:([\d.]+)\s*D\b)?\s*(?:([\d.]+)\s*H\b)?\s*(?:([\d.]+)\s*(?:MIN|MN)\b)?"
    r"\s*(?:([\d.]+)\s*S\b)?"
)
# "     FLUX BOUNDARY    6:    -1357.401     M3/S"
RE_FLUX = re.compile(
    r"^\s*FLUX BOUNDARY\s+(\d+)\s*:\s*([-+0-9.EDed+]+)\s*M3/S", re.IGNORECASE
)


def temps_en_secondes(texte):
    """Convertit la partie droite d'une ligne TIME: en secondes."""
    m = RE_TOTAL.search(texte)
    if m:
        return float(m.group(1))
    m = RE_COMPOSANTES.match(texte.strip())
    jours, heures, minutes, secondes = (float(g) if g else 0.0 for g in m.groups())
    return jours * 86400 + heures * 3600 + minutes * 60 + secondes


def lire_listing(chemin, frontieres):
    """Retourne une liste de dicts : iteration, temps_s, flux par frontiere."""
    resultats = []
    courant = None

    with open(chemin, "r", encoding="utf-8", errors="replace") as f:
        for ligne in f:
            m = RE_ITER.match(ligne)
            if m:
                # on archive le pas de temps precedent s'il contient des flux
                if courant and any(courant.get(f"Q_{b}") is not None for b in frontieres):
                    resultats.append(courant)
                courant = {
                    "iteration": int(m.group(1)),
                    "temps_s": temps_en_secondes(m.group("reste")),
                }
                for b in frontieres:
                    courant[f"Q_{b}"] = None
                continue

            m = RE_FLUX.match(ligne)
            if m and courant is not None:
                num = int(m.group(1))
                if num in frontieres:
                    # Fortran ecrit parfois 1.0D+03 : on normalise en E
                    valeur = m.group(2).replace("D", "E").replace("d", "e")
                    courant[f"Q_{num}"] = float(valeur)

    if courant and any(courant.get(f"Q_{b}") is not None for b in frontieres):
        resultats.append(courant)

    return resultats


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("listing", help="fichier listing TELEMAC-2D (.out)")
    p.add_argument("-b", "--boundaries", type=int, nargs="+", default=[6],
                   help="numeros des frontieres a extraire (defaut : 6)")
    p.add_argument("-o", "--output", help="fichier CSV de sortie")
    p.add_argument("--plot", action="store_true",
                   help="trace le ou les hydrogrammes (necessite matplotlib)")
    args = p.parse_args()

    chemin = Path(args.listing)
    frontieres = sorted(set(args.boundaries))
    donnees = lire_listing(chemin, frontieres)

    if not donnees:
        sys.exit("Aucun bilan 'FLUX BOUNDARY' trouve pour ces frontieres.")

    colonnes = ["iteration", "temps_s", "temps_h"] + [f"Q_{b}" for b in frontieres]
    for d in donnees:
        d["temps_h"] = d["temps_s"] / 3600.0

    sortie = Path(args.output) if args.output else \
        chemin.with_name(chemin.stem + "_flux.csv")
    with open(sortie, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";")
        w.writeheader()
        w.writerows({k: d[k] for k in colonnes} for d in donnees)

    print(f"{len(donnees)} pas de temps extraits -> {sortie}")
    for b in frontieres:
        vals = [d[f"Q_{b}"] for d in donnees if d[f"Q_{b}"] is not None]
        if vals:
            print(f"  Frontiere {b} : min {min(vals):.3f} | max {max(vals):.3f} | "
                  f"moy {sum(vals)/len(vals):.3f} m3/s")

    if args.plot:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5))
        for b in frontieres:
            x = [d["temps_h"] for d in donnees if d[f"Q_{b}"] is not None]
            y = [d[f"Q_{b}"] for d in donnees if d[f"Q_{b}"] is not None]
            ax.plot(x, y, label=f"Frontiere {b}")
        ax.set_xlabel("Temps (h)")
        ax.set_ylabel("Debit (m3/s)")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(sortie.with_suffix(".png"), dpi=150)
        print(f"  Graphique -> {sortie.with_suffix('.png')}")


if __name__ == "__main__":
    main()
