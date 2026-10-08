# dgm1 — MNT DGM1 du Bade-Wurtemberg (LGL-BW)

Télécharge le modèle numérique de terrain DGM1 (1 m) du Land de
Bade-Wurtemberg depuis le portail OpenGeoData du LGL-BW, sur l'emprise d'un
GeoPackage, puis assemble les dalles en une mosaïque GeoTIFF.

Utile pour les études transfrontalières sur le Rhin : le MNT allemand
complète le LiDAR HD français côté rive droite.

Le portail livre des paquets ZIP de 2 km × 2 km (4 dalles de 1 km²) en
ETRS89 / UTM 32N (EPSG:25832). Les paquets déjà extraits sont ignorés à la
relance.

## Prérequis

```bash
pip install requests geopandas
```

Python 3.11 ou plus. Pour la mosaïque : `gdalbuildvrt` et `gdal_translate`
d'un environnement conda Windows. Sans GDAL, les dalles sont extraites mais
pas assemblées.

## Utilisation

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python dgm1.py --config config.toml
```

Le GeoPackage d'emprise peut être dans n'importe quel système : il est
reprojeté en EPSG:25832. `zone_rectangulaire.gpkg` est fourni à titre
d'exemple.

## Configuration (`config.toml`)

| Section / clé | Rôle | Défaut |
|---|---|---|
| `[chemins] emprise_gpkg` | GeoPackage d'emprise (tout SCR) | `zone_rectangulaire.gpkg` |
| `[chemins] dossier_dalles` | Dossier des dalles extraites | `./dgm1_output/kacheln/` |
| `[chemins] mosaique` | Mosaïque GeoTIFF finale | `./dgm1_output/mosaique_dgm1_bw.tif` |
| `[chemins] log` | Journal d'exécution | `dgm1_portail_download.log` |
| `[telechargement] produit` | `dgm1` (1 m), ou `dgm025` (0,25 m) si disponible | `dgm1` |
| `[telechargement] max_workers` | Téléchargements parallèles (rester modéré) | 3 |
| `[telechargement] timeout_s` | Timeout par requête (s) | 120 |
| `[gdal] conda_prefix` | Environnement conda contenant GDAL, utilisé si aucun environnement n'est activé | — |

## Sorties

- `dgm1_32_<x>_<y>_1_bw.tif` : dalles de 1 km² en EPSG:25832
- la mosaïque compressée (LZW, tuilée)
- le journal d'exécution

La mosaïque reste en EPSG:25832 : la reprojeter en Lambert 93 si besoin
avant de la combiner à des données françaises.
