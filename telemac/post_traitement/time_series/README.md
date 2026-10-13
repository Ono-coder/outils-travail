# time_series — Exploitation des time series TELEMAC-2D

Depuis la version V8P5, TELEMAC-2D peut enregistrer les variables calculées
en quelques points du modèle, à une fréquence pouvant aller jusqu'à chaque
pas de temps de calcul (« time series »). L'échantillonnage est bien plus fin
que celui des sorties graphiques, pour un fichier qui reste léger tant que le
nombre de points est raisonnable.

Ce dossier contient de quoi exporter ces résultats en CSV, y compris lorsque
TELEMAC n'a pas pu fusionner les fichiers partiels d'un calcul parallèle.

| Script | Rôle |
|---|---|
| `extract_time_series.py` | Exporte le fichier Selafin de time series en CSV |
| `main_batch.py` + `sous_programme.py` | Extraction par lot des fichiers partiels `T2DHI1`, un CSV par fichier |
| `fusion_csv.py` | Fusionne les CSV produits par `main_batch.py` |

## Prérequis

Les modules Python de TELEMAC (`data_manip`) : lancer les scripts depuis
l'environnement TELEMAC, ou indiquer le dossier `scripts/python3` de TELEMAC
(`--telemac`, clé `[telemac] python` ou variable d'environnement
`TELEMAC_PYTHON`). `numpy` pour `sous_programme.py`. Python 3.11 ou plus pour
`--config` (sinon `pip install tomli`).

## Préparer le calcul TELEMAC

### Fichier de time series

- **Première ligne** : le nombre d'intervalles de temps d'extraction, puis le
  nombre de points.
- **Lignes suivantes**, une par intervalle : instant de début, instant de fin,
  et période d'enregistrement.
- **Lignes suivantes**, une par point : X, Y, un identifiant unique et un nom
  parlant, **sans espace** (`PK127_RD` est correct, `PK 127 RD` ne l'est pas).

Recommandations :
- faire au plus simple : un seul intervalle, du début à la fin du calcul ;
- mettre la période d'enregistrement à **1**. Contrairement à ce qu'indique
  la documentation, cette valeur n'est pas en secondes : c'est un nombre de
  pas de temps de calcul (avec un pas de 2 s, une période de 10 enregistre
  toutes les 20 s). À 1, une valeur est extraite à chaque pas de temps ; le
  fichier est plus lourd mais reste raisonnable ;
- les points n'ont pas besoin de coïncider avec des nœuds : TELEMAC
  interpole.

### Fichier cas

```
TIME SERIES COORDINATES FILE 1 = 'monfichier_time_series.txt'
TIME SERIES FILE 1             = 'monfichier.slf'
```

Deux jeux de time series peuvent être enregistrés (`… FILE 2`). La sortie est
au format Selafin par défaut ; le format MED est aussi possible.

## Cas normal : un seul fichier de time series

```bash
python extract_time_series.py monfichier.slf export.csv --variables 1,3
python extract_time_series.py monfichier.slf export.csv --variables all --telemac C:/TELEMAC/V8P5/scripts/python3
```

Sans arguments, le script demande le fichier Selafin, le CSV de sortie et les
variables, après avoir affiché la liste des variables disponibles.

Sortie : CSV séparé par des tabulations, virgule décimale, une ligne par pas
de temps, une colonne `<variable>_P<n>` par variable et par point.

## Cas d'un calcul dont la fusion a échoué

Si le dossier temporaire du calcul est resté sur le cluster, soit le calcul
est encore en cours, soit il s'est arrêté avant la fin. Si l'erreur vient de
la fusion des fichiers `T2DHI1`, l'outil `grehis` de TELEMAC (équivalent de
`gretel` pour les time series) devrait faire ce travail, mais il n'est pas
toujours compilé. Les scripts suivants le remplacent.

1. Récupérer tous les fichiers `T2DHI1` du dossier temporaire de calcul.
   Les fichiers de taille nulle sont inutiles.
2. Lancer l'extraction par lot :

   ```bash
   python main_batch.py --dossier ./T2DHI1 --pas 2.0 --tolerance 0.05 --telemac C:/TELEMAC/V8P5/scripts/python3
   # ou
   cp config_exemple.toml config.toml   # puis adapter
   python main_batch.py --config config.toml
   ```

   | Option | Clé TOML | Rôle | Défaut |
   |---|---|---|---|
   | `--dossier` | `[chemins] dossier_slf` | Dossier des fichiers `T2DHI1` | demandé |
   | `--sortie` | `[chemins] dossier_csv` | Dossier des CSV produits | `csv_intermediaires` |
   | `--pas` | `[extraction] pas_de_temps` | Pas de temps d'extraction (s) | demandé |
   | `--tolerance` | `[extraction] tolerance` | Écart toléré autour de chaque instant visé (s) | 0,05 |
   | `--variable` | `[extraction] index_variable` | Index de la variable | liste affichée, choix demandé |
   | `--telemac` | `[telemac] python` | Dossier `scripts/python3` de TELEMAC | environnement chargé |

   Les scripts ont été mis au point pour un calcul au pas de temps de
   0,05 s : adapter `--pas` et `--tolerance` au modèle. Le premier et le
   dernier pas de temps sont toujours conservés.

3. Fusionner les CSV en un seul fichier :

   ```bash
   python fusion_csv.py --entree csv_intermediaires --sortie fusion.csv
   ```

Dans le fichier fusionné, les colonnes sont nommées `X<x>_Y<y>` et
**triées par ordre alphabétique** de ces noms. Pour retrouver les noms des
points, faire la correspondance avec les coordonnées du fichier de time
series donné à TELEMAC (par exemple en le triant dans Excel).
