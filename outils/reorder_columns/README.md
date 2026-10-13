# reorder_columns — Réordonner et calculer des colonnes

Réordonne (ou sélectionne) les colonnes d'un fichier texte délimité et ajoute
des colonnes calculées par des expressions arithmétiques. Le délimiteur est
détecté automatiquement : virgule, point-virgule, tabulation, pipe ou espace.

Pratique pour remettre un export dans l'ordre attendu par un logiciel
(`X Y Z`), ou calculer un écart entre deux colonnes (`DZ = Z_ref - Z_lev`).

## Prérequis

Python 3 uniquement, aucune bibliothèque externe.

## Utilisation

```bash
python reorder_columns.py data.csv --preview                       # liste les colonnes et quitte
python reorder_columns.py data.csv out.csv --order X Y Z
python reorder_columns.py data.csv out.csv --order X Y Z --calc "DZ=Z_ref-Z_lev"
python reorder_columns.py data.csv out.csv --order 0 1 2 --calc "DZ=c2-c5" "S=X+Y"
python reorder_columns.py data.txt out.txt --delimiter TAB --order 2 0 1 --calc "diff=A-B"
```

| Option | Rôle | Défaut |
|---|---|---|
| `input` | Fichier d'entrée (avec ligne d'en-tête) | — |
| `output` | Fichier de sortie (inutile avec `--preview`) | — |
| `--order` | Colonnes à garder, dans l'ordre : indices (à partir de 0), noms, ou un mélange | toutes, ordre d'origine |
| `--calc "NOM=EXPR"` | Colonnes calculées (ajoutées, ou remplacées si le nom existe) | aucune |
| `--delimiter` | Délimiteur d'entrée (`TAB` pour tabulation) | détecté |
| `--out_delimiter` | Délimiteur de sortie | celui d'entrée |
| `--preview` | Affiche les colonnes disponibles et quitte | non |

## Expressions

Dans une expression, les colonnes s'utilisent par leur nom, ou par leur
indice sous la forme `c0`, `c1`, `c2`… Opérateurs : `+ - * / ** % //`, et les
fonctions `abs()`, `round()`, `sqrt()`, `log()`, etc. Exemples :

| Expression | Résultat |
|---|---|
| `"DZ=Z_ref-Z_lev"` | différence de deux colonnes |
| `"H=round(Z*100)/100"` | arrondi au centimètre |
| `"R=sqrt(X**2+Y**2)"` | distance à l'origine |
| `"PCT=100*(A-B)/A"` | écart relatif en % |

Les expressions sont évaluées avec `eval`, privé des fonctions intégrées de
Python et limité aux fonctions mathématiques. Ce n'est pas une protection de
sécurité : n'utiliser que des expressions que l'on a écrites soi-même.
