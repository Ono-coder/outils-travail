# meteo_download — Données climatologiques quotidiennes Météo-France

Télécharge les données quotidiennes de Météo-France (dataset
« Données climatologiques de base - quotidiennes » de meteo.data.gouv.fr)
et produit des fichiers prêts à l'analyse.

Trois modes, enchaînables :

| Mode | Rôle | Sorties |
|---|---|---|
| `find` | Cherche les N stations les plus proches d'un point | `stations_proches.html` (carte OpenStreetMap) |
| `download` | Télécharge les données des stations listées | `donnees_quotidiennes_consolidees.csv`, `chronique_journaliere.html` |
| `gaps` | Analyse les lacunes du CSV consolidé | `lacunes.html` (heatmap % manquant par station et par an + plages continues) |

`both` enchaîne `find` + `download`, `all` enchaîne les trois.

## Prérequis

```bash
pip install requests pandas plotly
```

Python 3.11 ou plus (lecture du TOML avec `tomllib`). Accès internet à
data.gouv.fr.

## Utilisation

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python meteo_download.py --config config.toml
```

Démarche habituelle :
1. `mode = "find"` avec le point d'étude (`[recherche] lat`, `lon`) pour
   repérer les stations sur la carte ;
2. reporter les identifiants retenus dans `[telechargement] stations` ;
3. `mode = "download"`, puis `mode = "gaps"` pour vérifier la complétude.

## Configuration (`config.toml`)

| Section / clé | Rôle | Défaut |
|---|---|---|
| `mode` | `find`, `download`, `gaps`, `both` ou `all` | `both` |
| `[recherche] lat`, `lon` | Point de référence (WGS84) — obligatoire en mode `find` | — |
| `[recherche] n_stations` | Nombre de stations proches à afficher | 25 |
| `[recherche] rayon_max_km` | Rayon maximal (omettre = pas de limite) | pas de limite |
| `[telechargement] stations` | Identifiants NUM_POSTE, **entre guillemets** (zéros initiaux) — obligatoire en mode `download` | — |
| `[telechargement] variables` | Variables à conserver, ex. `["RR", "TX"]` (omettre = toutes) | toutes |
| `[telechargement] inclure_colonnes_qualite` | Garder les colonnes QRR, QTN… | `false` |
| `[telechargement] force_refresh_latest` | Re-télécharger les fichiers « latest » déjà en cache | `true` |
| `[telechargement] variable_graphique` | Variable tracée dans la chronique | `RR` |
| `[lacunes] variables` | Variables analysées (omettre = toutes) | toutes |
| `[sorties] dossier` | Dossier des sorties et du cache | `meteo_output` |

Variables disponibles : RR (pluie) ; TN, TX, TM… (températures) ; FFM, FXY…
(vent) ; INST, GLOT, UN, UX, UM, PMER… ; NEIGETOT… La liste complète est
dans `config_exemple.toml`.

Les fichiers téléchargés sont mis en cache dans `<dossier>/cache` : une
relance ne re-télécharge que les fichiers « latest » (si
`force_refresh_latest = true`).

## Notebook `Affichage_stations/`

Notebook Jupyter (auteur : L. Duffar) qui cartographie l'ensemble des postes
météorologiques Météo-France, ouverts et fermés, et exporte la liste en
Excel / GeoJSON / HTML. Avant de l'exécuter, adapter la variable
`folder_out` (dossier de sortie) dans la première cellule de code.
