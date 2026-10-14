# discretiser_pk — Points PK régulièrement espacés entre bornes

Discrétise une ligne (axe du lit mineur, ligne d'eau…) à partir des bornes PK,
dont l'espacement réel varie. Chaque intervalle entre deux bornes successives
est découpé en **n segments de même longueur, mesurée le long de la ligne**.

Exemple avec un pas nominal de 100 m :

| Intervalle | Longueur réelle le long de la ligne | n | Longueur d'un segment | Points |
|---|---|---|---|---|
| 125 → 126 | 1 100 m | 10 | 110 m | 125+000, 125+100, …, 125+900 |
| 126 → 127 | 950 m | 10 | 95 m | 126+000, 126+100, …, 126+900 |

Les points portent des **PK officiels** (125+100 = PK 125,1). La distance
réelle entre deux points est la longueur de l'intervalle divisée par n. Le
dernier point de chaque intervalle est exactement sur la borne suivante.

## Prérequis

```bash
pip install numpy pandas geopandas "shapely>=2"
```

Python 3.11 ou plus pour `--config` (sinon `pip install tomli`).

## Utilisation

```bash
python discretiser_pk.py --ligne axe_lit_mineur.gpkg --bornes bornes_pk.gpkg --pas 100 --csv points.csv --segments
# ou
cp config_exemple.toml config.toml   # puis adapter
python discretiser_pk.py --config config.toml
```

| Option | Clé TOML | Rôle | Défaut |
|---|---|---|---|
| `--ligne` | `[entrees] ligne` | Ligne à discrétiser — obligatoire | — |
| `--couche-ligne` | `[entrees] couche_ligne` | Couche si le fichier en contient plusieurs | première |
| `--bornes` | `[entrees] bornes` | Points des bornes PK — obligatoire | — |
| `--couche-bornes` | `[entrees] couche_bornes` | Couche si le fichier en contient plusieurs | première |
| `--champ-pk` | `[entrees] champ_pk` | Champ du PK des bornes | `pk`, `pk_km`, `borne`… détecté (majuscules indifférentes) |
| `--unite-bornes` | `[entrees] unite_bornes` | Unité de ce champ : `km` ou `m` | `km` |
| `--pas` | `[discretisation] pas` | Pas nominal en m de PK ; n = PK de l'intervalle / pas, arrondi | 100 |
| `--n-segments` | `[discretisation] n_segments` | Nombre fixe de segments par intervalle (remplace `--pas`) | — |
| `--prolonger` | `[discretisation] prolonger` | Discrétiser aussi avant la première et après la dernière borne | non |
| `--sortie` | `[sorties] gpkg` | GeoPackage de sortie | `points_pk.gpkg` |
| `--csv` | `[sorties] csv` | CSV des points | aucun |
| `--segments` | `[sorties] segments` | Écrire aussi les segments entre points | non |

Les options en ligne de commande l'emportent sur le fichier de configuration.

## Règles

- La ligne peut être en plusieurs tronçons s'ils se touchent ; sinon seul le
  plus long est gardé (avertissement).
- Les bornes n'ont pas besoin d'être exactement sur la ligne : elles y sont
  projetées perpendiculairement. Celles au-delà des extrémités de la ligne
  sont écartées, avec un avertissement qui les nomme.
- Les bornes doivent se succéder le long de la ligne dans l'ordre de leurs
  PK (croissants ou décroissants). Le sens de numérisation de la ligne
  n'a pas d'importance.
- **Avec `--prolonger`**, avant la première et après la dernière borne, faute
  de borne pour recaler, les points sont placés tous les `pas` mètres réels
  (1 m de PK par mètre de ligne).
- Si la ligne porte des altitudes (ligne 3D), le Z est interpolé sur chaque
  point.

## Sorties

**Couche `points_pk`** (GeoPackage) et CSV éventuel (séparateur `;`) :

| Champ | Contenu |
|---|---|
| `pk_m`, `pk_km` | PK officiel du point, en m et en km |
| `pk_libelle` | PK au format `125+300` |
| `type` | `borne`, `intermediaire` ou `prolongement` |
| `borne` | Nom de la borne, pour les points sur une borne |
| `intervalle` | Intervalle de bornes, ex. `125-126` |
| `abscisse_ligne` | Distance le long de la ligne depuis son début (m) |
| `longueur_segment` | Longueur réelle des segments de l'intervalle (m) |
| `x`, `y`, `z` | Coordonnées (`z` seulement pour une ligne 3D) |

**Couche `segments_pk`** (avec `--segments`) : les tronçons de ligne entre
deux points successifs, avec `pk_debut`, `pk_fin`, `libelle` et `longueur`.

Le programme affiche pour chaque intervalle sa longueur réelle, n, la longueur
des segments et l'écart au kilomètre officiel. Au-delà de 10 % d'écart, une
borne est probablement mal placée ou mal numérotée.
