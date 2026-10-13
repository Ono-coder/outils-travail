"""
reorder_columns.py
==================
Réordonne les colonnes d'un fichier texte délimité et permet de créer
des colonnes calculées via des expressions arithmétiques.

Détecte automatiquement le délimiteur (virgule, point-virgule, tabulation, pipe, espace).

─── ORDRE DES COLONNES ────────────────────────────────────────────────────────
  --order 2 0 1               par indices (0-based)
  --order Z X Y               par noms de colonnes
  --order 0 2 1               mix indices/noms

─── COLONNES CALCULÉES ────────────────────────────────────────────────────────
  --calc "NOM=EXPR"           ajoute ou remplace une colonne
  Les noms de colonnes sont utilisables directement dans l'expression.
  Les indices sont accessibles via c0, c1, c2...

  Opérateurs disponibles : + - * / ** % // abs() round() sqrt() log() etc.

  Exemples d'expressions :
    "DZ=Z_ref-Z_lev"          soustraction de deux colonnes
    "DZ=c2-c5"                par indices
    "S=X*X+Y*Y"               combinaison
    "H=round(Z*100)/100"      arrondi
    "R=sqrt(X**2+Y**2)"       distance 2D
    "PCT=100*(A-B)/A"         pourcentage d'écart

─── EXEMPLES COMPLETS ─────────────────────────────────────────────────────────
  python reorder_columns.py data.csv --preview
  python reorder_columns.py data.csv out.csv --order X Y Z
  python reorder_columns.py data.csv out.csv --order X Y Z --calc "DZ=Z_ref-Z_lev"
  python reorder_columns.py data.csv out.csv --order 0 1 2 --calc "DZ=c2-c5" "S=X+Y"
  python reorder_columns.py data.txt out.txt --delimiter TAB --order 2 0 1 --calc "diff=A-B"
"""

import argparse
import csv
import math
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# DÉTECTION DU DÉLIMITEUR
# ─────────────────────────────────────────────────────────────────────────────

def detect_delimiter(filepath: str, n_lines: int = 5) -> str:
    candidates = [";", ",", "\t", "|", " "]
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        sample = "".join(f.readline() for _ in range(n_lines))
    scores = {d: sample.count(d) for d in candidates}
    best = max(scores, key=scores.get)
    names = {";": "point-virgule", ",": "virgule", "\t": "tabulation",
             "|": "pipe", " ": "espace"}
    print(f"  Délimiteur détecté : '{best}' ({names.get(best, best)})")
    return best


# ─────────────────────────────────────────────────────────────────────────────
# LECTURE EN-TÊTE
# ─────────────────────────────────────────────────────────────────────────────

def read_header(filepath: str, delimiter: str) -> list:
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f, delimiter=delimiter)
        header = next(reader)
    return [h.strip() for h in header]


def preview(filepath: str, delimiter: str):
    header = read_header(filepath, delimiter)
    print(f"\n  {len(header)} colonnes détectées :\n")
    for i, name in enumerate(header):
        print(f"    [{i:2d}]  {name}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# RÉSOLUTION DE L'ORDRE
# ─────────────────────────────────────────────────────────────────────────────

def resolve_order(order_arg: list, header: list) -> list:
    """Accepte indices entiers ou noms de colonnes, retourne liste d'indices."""
    indices = []
    for token in order_arg:
        token = token.strip()
        if token.lstrip("-").isdigit():
            idx = int(token)
            if not (0 <= idx < len(header)):
                raise ValueError(f"Indice {idx} hors limites (0–{len(header)-1})")
            indices.append(idx)
        else:
            matches = [i for i, h in enumerate(header) if h.lower() == token.lower()]
            if not matches:
                raise ValueError(f"Colonne '{token}' introuvable. Colonnes disponibles : {header}")
            indices.append(matches[0])
    return indices


# ─────────────────────────────────────────────────────────────────────────────
# COLONNES CALCULÉES
# ─────────────────────────────────────────────────────────────────────────────

# Fonctions math disponibles dans les expressions
SAFE_MATH = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e,
}


def parse_calc(calc_specs: list, header: list) -> list:
    """
    Parse les specs --calc "NOM=EXPR".
    Retourne liste de (col_name, expr_string).
    """
    calcs = []
    for spec in calc_specs:
        if "=" not in spec:
            raise ValueError(f"Syntaxe invalide '{spec}'. Format attendu : NOM=EXPRESSION")
        name, expr = spec.split("=", 1)
        name = name.strip()
        expr = expr.strip()
        if not name:
            raise ValueError(f"Nom de colonne vide dans '{spec}'")
        calcs.append((name, expr))
    return calcs


def build_row_context(row: list, header: list) -> dict:
    """
    Construit le contexte d'évaluation pour une ligne :
      - valeurs par nom de colonne : {"X": 1.0, "Y": 2.0, ...}
      - valeurs par index          : {"c0": 1.0, "c1": 2.0, ...}
    Les valeurs non numériques sont laissées comme chaînes.
    """
    ctx = dict(SAFE_MATH)
    for i, (col, val) in enumerate(zip(header, row)):
        try:
            v = float(val.replace(",", "."))  # support virgule décimale
        except (ValueError, AttributeError):
            v = val
        ctx[col]          = v
        ctx[f"c{i}"]      = v
    return ctx


def evaluate_calc(expr: str, ctx: dict, line_num: int) -> str:
    """Évalue une expression dans le contexte de la ligne. Retourne str."""
    try:
        result = eval(expr, {"__builtins__": {}}, ctx)  # sandbox minimal
        # Formatage : entier si pas de décimale significative
        if isinstance(result, float):
            return f"{result:.6g}"
        return str(result)
    except ZeroDivisionError:
        return "NaN"
    except Exception as exc:
        print(f"  [!] Ligne {line_num} : erreur dans '{expr}' → {exc}", file=sys.stderr)
        return "ERR"


# ─────────────────────────────────────────────────────────────────────────────
# TRAITEMENT PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def process(
    input_path: str,
    output_path: str,
    order: list,          # liste d'indices de colonnes à conserver/réordonner
    calcs: list,          # liste de (nom, expr)
    delimiter: str,
    out_delimiter: str,
):
    header = read_header(input_path, delimiter)

    # Noms des colonnes de sortie (réordonnées)
    out_header = [header[i] for i in order]

    # Colonnes calculées : ajout ou remplacement
    # Si le nom existe dans out_header → remplacement (même position)
    # Sinon → ajout en fin
    calc_positions = {}  # nom → position dans out_header (-1 = nouvelle)
    for calc_name, _ in calcs:
        matches = [i for i, h in enumerate(out_header) if h.lower() == calc_name.lower()]
        if matches:
            calc_positions[calc_name] = matches[0]
        else:
            calc_positions[calc_name] = len(out_header)
            out_header.append(calc_name)

    total = 0
    errors = 0

    with open(input_path,  "r", encoding="utf-8-sig", errors="replace", newline="") as fin, \
         open(output_path, "w", encoding="utf-8",     newline="") as fout:

        reader = csv.reader(fin,  delimiter=delimiter)
        writer = csv.writer(fout, delimiter=out_delimiter)

        for line_num, row in enumerate(reader):
            if line_num == 0:
                # En-tête
                writer.writerow(out_header)
                continue

            # Réordonnancement
            new_row = [row[j].strip() if j < len(row) else "" for j in order]

            # Colonnes calculées
            if calcs:
                ctx = build_row_context(row, header)
                # Étendre new_row si nécessaire
                while len(new_row) < len(out_header):
                    new_row.append("")

                for calc_name, expr in calcs:
                    val = evaluate_calc(expr, ctx, line_num)
                    if val == "ERR":
                        errors += 1
                    pos = calc_positions[calc_name]
                    if pos < len(new_row):
                        new_row[pos] = val
                    else:
                        new_row.append(val)

            writer.writerow(new_row)
            total += 1

    print(f"  Lignes traitées : {total:,}")
    if errors:
        print(f"  [!] Erreurs d'évaluation : {errors} (voir messages ci-dessus)")
    print(f"  Fichier de sortie : {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Réordonne les colonnes et calcule des colonnes dérivées.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Aperçu des colonnes disponibles
  python reorder_columns.py data.csv --preview

  # Réordonnancement simple
  python reorder_columns.py data.csv out.csv --order X Y Z

  # Soustraction de deux colonnes → nouvelle colonne DZ
  python reorder_columns.py data.csv out.csv --order X Y Z_ref Z_lev --calc "DZ=Z_ref-Z_lev"

  # Plusieurs colonnes calculées
  python reorder_columns.py data.csv out.csv --order X Y Z --calc "DZ=A-B" "PCT=100*(A-B)/A"

  # Utilisation par indices
  python reorder_columns.py data.txt out.txt --order 0 1 2 3 --calc "diff=c2-c5"

  # Distance 2D
  python reorder_columns.py pts.csv out.csv --order X Y Z --calc "R=sqrt(X**2+Y**2)"

  # Délimiteur tabulation
  python reorder_columns.py data.txt out.txt --delimiter TAB --order 2 0 1 --calc "DZ=c3-c4"
        """
    )
    parser.add_argument("input",  help="Fichier d'entrée")
    parser.add_argument("output", nargs="?", default=None,
                        help="Fichier de sortie (omis si --preview)")
    parser.add_argument("--order", nargs="+", default=None,
                        help="Colonnes à conserver et leur ordre (indices ou noms)")
    parser.add_argument("--calc", nargs="+", default=None, metavar="NOM=EXPR",
                        help='Colonnes calculées. Ex: --calc "DZ=Z_ref-Z_lev" "R=sqrt(X**2+Y**2)"')
    parser.add_argument("--delimiter", default=None,
                        help="Délimiteur entrée (auto si omis). TAB pour tabulation.")
    parser.add_argument("--out_delimiter", default=None,
                        help="Délimiteur sortie (identique à l'entrée si omis)")
    parser.add_argument("--preview", action="store_true",
                        help="Affiche les colonnes disponibles et quitte")

    args = parser.parse_args()

    # Délimiteur
    if args.delimiter is None:
        delim = detect_delimiter(args.input)
    elif args.delimiter.upper() == "TAB":
        delim = "\t"
    else:
        delim = args.delimiter

    out_delim = delim if args.out_delimiter is None else (
        "\t" if args.out_delimiter.upper() == "TAB" else args.out_delimiter
    )

    # Aperçu
    if args.preview:
        preview(args.input, delim)
        return

    if args.order is None and args.calc is None:
        parser.error("--order ou --calc requis (ou --preview pour lister les colonnes)")
    if args.output is None:
        parser.error("fichier de sortie requis sauf avec --preview")

    header = read_header(args.input, delim)

    # Ordre : si omis, on garde toutes les colonnes dans l'ordre original
    if args.order is None:
        order_indices = list(range(len(header)))
    else:
        order_tokens = []
        for token in args.order:
            order_tokens.extend(token.split(","))
        try:
            order_indices = resolve_order(order_tokens, header)
        except ValueError as e:
            print(f"\n  [ERREUR] {e}", file=sys.stderr)
            sys.exit(1)

    # Colonnes calculées
    calcs = []
    if args.calc:
        try:
            calcs = parse_calc(args.calc, header)
        except ValueError as e:
            print(f"\n  [ERREUR] {e}", file=sys.stderr)
            sys.exit(1)

    # Résumé
    print(f"\n  Colonnes de sortie :")
    for new_pos, old_idx in enumerate(order_indices):
        print(f"    [{new_pos}] {header[old_idx]}  (source: col {old_idx})")
    for calc_name, expr in calcs:
        print(f"    [+] {calc_name} = {expr}")

    process(args.input, args.output, order_indices, calcs, delim, out_delim)
    print("  ✓ Terminé")


if __name__ == "__main__":
    main()