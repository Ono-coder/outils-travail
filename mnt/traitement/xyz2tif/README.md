# xyz2tif — Nuages de points XYZ → GeoTIFF

Convertit des fichiers de points `X Y Z` (levés bathymétriques, MNT livrés en
XYZ…) en GeoTIFF, en affectant à chaque maille la **moyenne des Z** des
points qu'elle contient. Conçu pour les gros fichiers (plusieurs Go) et les
fichiers abîmés :

- lecture en parallèle par portions, avec suivi de l'avancement (débit,
  temps restant) ;
- lignes illisibles (texte, nombres collés, guillemets, virgule décimale…)
  ignorées une à une, avec un échantillon écrit dans `<nom>_rejets.txt` ;
- points aberrants mais lisibles (ex. X tronqué qui tombe à des km du levé)
  écartés par groupes de tuiles isolées et clairsemées ;
- petits trous comblés par interpolation (`distance_remplissage`).

Le dossier contient aussi `extraire_xyz_des_zip.py`, qui rassemble les `.xyz`
contenus dans des archives ZIP (livraisons par dalles) avant conversion.

## Prérequis

```bash
pip install numpy pandas rasterio
```

Python 3.11 ou plus pour `--config` (sinon `pip install tomli`).

## Utilisation

```bash
# 1. (optionnel) extraire les .xyz des archives ZIP
python extraire_xyz_des_zip.py --source ./zip --destination ./xyz

# 2. convertir tous les .xyz du dossier
python xyz2tif.py --dossier ./xyz --resolution 1 --epsg 2154 --workers 4
```

Ou avec un fichier de configuration (commun aux deux scripts) :

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python extraire_xyz_des_zip.py --config config.toml
python xyz2tif.py --config config.toml
```

Les options passées en ligne de commande sont prioritaires sur le fichier.

### `xyz2tif.py`

| Option | Clé TOML | Rôle | Défaut |
|---|---|---|---|
| `--dossier` | `[chemins] dossier` | Dossier des `.xyz` — obligatoire | — |
| `--resolution` | `[raster] resolution` | Taille de maille (m) | 1,0 |
| `--epsg` | `[raster] epsg` | Système de coordonnées | 2154 |
| — | `[raster] nodata` | Valeur NoData | -9999 |
| — | `[raster] distance_remplissage` | Rayon de comblement des trous (mailles, 0 = aucun) | 3 |
| `--workers` | `[lecture] workers` | Processus de lecture en parallèle | 4 |
| `--conserver` | `[lecture] ecraser_existants = false` | Ne pas remplacer les `.tif` existants | remplacés |
| — | `[lecture] emprise` | Ne garder que `[xmin, xmax, ymin, ymax]` | tout |
| — | `[lecture] plage_z` | Écarter les Z hors de `[zmin, zmax]` | aucun filtre |
| — | `[filtrage] …` | Réglages du filtrage des points aberrants (voir l'exemple) | adaptés à la plupart des levés |

Séparateurs acceptés : espaces, tabulations, virgules, points-virgules ;
virgule décimale détectée ligne par ligne.

### `extraire_xyz_des_zip.py`

| Option | Clé TOML | Rôle |
|---|---|---|
| `--source` | `[extraction_zip] source` | Dossier des archives `.zip` |
| `--destination` | `[extraction_zip] destination` | Dossier où copier les `.xyz` |
| `--nettoyer` | `[extraction_zip] nettoyer` | Vider la destination avant l'extraction |

Les `.xyz` sont cherchés dans toute l'arborescence de chaque archive ; un
fichier de même nom est renommé (`_1`, `_2`…) plutôt qu'écrasé.

## Sorties

Pour chaque `nom.xyz` : `nom.tif` (GeoTIFF float32 à côté du fichier source)
et, s'il y a des lignes rejetées, `nom_rejets.txt`.

Pour des fichiers `.asc` mélangeant grilles ESRI et points XYZ,
[`asc2tif`](../asc2tif/) détecte aussi le format, avec une rastérisation plus
simple.
