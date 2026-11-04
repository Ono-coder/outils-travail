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
| `[echantillonnage] actif` | Échantillonnage autour du pic de crue de chaque section | `true` |
| `[echantillonnage] paliers` | `[largeur de fenêtre centrée sur le pic (s), pas (s)]`, pas 0 = tous les pas de temps | `[[600, 0], [1200, 1], [1800, 5], [3600, 10]]` |
| `[echantillonnage] pas_hors_pic` | Pas d'extraction hors des fenêtres (s) | `60` |

Le nombre de sections est lu sur la première ligne non commentée du fichier
de définition. Si ce fichier est absent, il est déduit des colonnes du
fichier résultat.

## Sortie

Avec l'échantillonnage (défaut) : une feuille par section, avec les colonnes
`TIME` et la section. Chaque section a ses propres instants : le pic est
l'instant du maximum de |débit| de la section ; autour de lui, le pas
d'extraction est celui du palier le plus fin dont la fenêtre contient
l'instant, et vaut `pas_hors_pic` ailleurs. Premier et dernier instants
toujours inclus.

Sans échantillonnage (`actif = false`) : une seule feuille, colonne du temps
puis une colonne par section, à tous les pas de temps.

Les noms de sections sont lus dans l'en-tête `VARIABLES` du fichier résultat.

La fonction `write_control_sections_results` peut aussi être importée depuis
un autre script ; avec `export=False`, elle renvoie un DataFrame pandas (ou,
avec échantillonnage, un dictionnaire `{section: DataFrame}`) au lieu
d'écrire le fichier Excel.
