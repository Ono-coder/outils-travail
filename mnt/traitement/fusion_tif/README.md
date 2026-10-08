# fusion — Mosaïque de fichiers GeoTIFF

Fusionne tous les GeoTIFF d'un dossier (dalles de MNT par exemple) en une
seule mosaïque GeoTIFF compressée, via un raster virtuel GDAL
(`gdalbuildvrt` puis `gdal_translate`).

## Prérequis

GDAL installé dans un environnement conda Windows : le script cherche
`gdalbuildvrt.exe` et `gdal_translate.exe` dans `Scripts\` ou
`Library\bin\` de l'environnement conda actif, sinon de celui indiqué dans
la configuration. Python 3.11 ou plus pour l'option `--config`.

## Utilisation

En ligne de commande :

```bash
python fusion.py --input ./lidar_output/mnt_data/ --output mosaique.tif
python fusion.py --input ./data/ --pattern "*_MNT_*.tif"
```

Ou avec un fichier de configuration :

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python fusion.py --config config.toml
```

Les options passées en ligne de commande sont prioritaires sur le fichier
de configuration.

| Option | Clé TOML | Rôle | Défaut |
|---|---|---|---|
| `--input`, `-i` | `[chemins] entree` | Dossier des TIF à fusionner | `./lidar_output/mnt_data/` |
| `--output`, `-o` | `[chemins] sortie` | Mosaïque de sortie | `./lidar_output/mosaique_finale.tif` |
| `--pattern`, `-p` | `[fusion] pattern` | Motif de sélection des fichiers | `*.tif` |
| — | `[gdal] conda_prefix` | Environnement conda contenant GDAL, si aucun n'est activé | — |

## Sortie

Mosaïque GeoTIFF compressée (LZW, tuilée). Les fichiers temporaires (VRT,
liste des fichiers) sont créés à côté de la mosaïque puis supprimés.

Tous les fichiers doivent être dans le même système de coordonnées et avoir
la même résolution.
