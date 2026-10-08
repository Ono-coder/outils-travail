# telechargement_mnt_ign — MNT LiDAR HD de l'IGN

Télécharge les dalles du MNT LiDAR HD de l'IGN (0,5 m, Lambert 93 / IGN69)
couvrant l'emprise d'un GeoPackage, via le service WMS de la Géoplateforme
(`data.geopf.fr`), puis les assemble en une mosaïque GeoTIFF.

Les dalles font 1 km × 1 km ; les dalles déjà téléchargées sont ignorées à
la relance.

## Prérequis

```bash
pip install requests geopandas
```

Python 3.11 ou plus. Pour la mosaïque : `gdalbuildvrt` et `gdal_translate`
d'un environnement conda Windows (recherchés dans `Scripts\` ou
`Library\bin\` de l'environnement). Sans GDAL, les dalles sont téléchargées
mais pas assemblées.

## Utilisation

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python telechargement_mnt_ign.py --config config.toml
```

L'emprise est l'enveloppe de toutes les géométries du GeoPackage, qui doit
être en **Lambert 93** (EPSG:2154). `zone_rectangulaire.gpkg` est fourni à
titre d'exemple.

## Configuration (`config.toml`)

| Section / clé | Rôle | Défaut |
|---|---|---|
| `[chemins] emprise_gpkg` | GeoPackage d'emprise (Lambert 93) | `zone_rectangulaire.gpkg` |
| `[chemins] dossier_dalles` | Dossier des dalles téléchargées | `./lidar_output/mnt_data/` |
| `[chemins] mosaique` | Mosaïque GeoTIFF finale | `./lidar_output/mosaique_mnt.tif` |
| `[chemins] log` | Journal d'exécution | `lidar_mnt_download.log` |
| `[telechargement] max_workers` | Téléchargements parallèles | 6 |
| `[telechargement] timeout_s` | Timeout par requête (s) | 120 |
| `[telechargement] resolution_px` | Pixels par côté de dalle (2000 → 0,5 m) | 2000 |
| `[gdal] conda_prefix` | Environnement conda contenant GDAL, utilisé si aucun environnement n'est activé | — |

## Sorties

- `LHD_FXX_<x>_<y>_MNT_O_0M50_LAMB93_IGN69.tif` : une dalle par km²
- la mosaïque compressée (LZW, tuilée)
- le journal d'exécution

Une grande emprise représente beaucoup de dalles : vérifier le nombre de
dalles annoncé au démarrage avant de laisser tourner le téléchargement.
