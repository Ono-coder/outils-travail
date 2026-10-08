# extract_section — Résultats des sections de contrôle TELEMAC vers Excel

Convertit le fichier de résultats des sections de contrôle d'un calcul
TELEMAC-2D (format Tecplot, ex. `T2DSEO`) en un tableau Excel : une ligne
par pas de temps, une colonne par section.

## Prérequis

```bash
pip install pandas openpyxl
```

Python 3.11 ou plus.

## Utilisation

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python extract_section.py --config config.toml
```

## Configuration (`config.toml`)

| Section / clé | Rôle | Défaut |
|---|---|---|
| `[fichiers] resultat` | Fichier résultat des sections de contrôle | `T2DSEO` |
| `[fichiers] sections` | Fichier de définition des sections (fichier `SECTIONS INPUT FILE` de TELEMAC) | `control_sections.txt` |
| `[fichiers] sortie_excel` | Classeur Excel produit | `control_sections.xlsx` |

Le nombre de sections est lu sur la première ligne non commentée du fichier
de définition. Si ce fichier est absent, il est déduit des colonnes du
fichier résultat.

## Sortie

Classeur Excel : colonne du temps puis une colonne par section de
contrôle, avec les noms de variables lus dans l'en-tête `VARIABLES` du
fichier résultat.

La fonction `write_control_sections_results` peut aussi être importée depuis
un autre script ; avec `export=False`, elle renvoie un DataFrame pandas au
lieu d'écrire le fichier Excel.
