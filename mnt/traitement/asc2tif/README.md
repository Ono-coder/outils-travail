# asc2tif — Conversion ESRI ASCII Grid → GeoTIFF

Convertit tous les fichiers `.asc` (ESRI ASCII Grid) d'un dossier en
GeoTIFF compressés, en parallèle, avec `gdal_translate`. Les fichiers déjà
convertis sont ignorés à la relance.

Typiquement utilisé sur les dalles RGE ALTI extraites par
[`extract_rgealti`](../../telechargement/rge_alti/).

## Prérequis

GDAL installé dans un environnement conda Windows : le script cherche
`gdal_translate.exe` dans `Scripts\` ou `Library\bin\` de l'environnement
conda actif, sinon de celui indiqué dans la configuration. Python 3.11 ou
plus pour l'option `--config`.

## Utilisation

En ligne de commande :

```bash
python asc2tif.py --input ./asc_data/ --output ./tif_data/ --epsg 2154
python asc2tif.py --input ./data/ --compress DEFLATE --workers 8
```

Ou avec un fichier de configuration :

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python asc2tif.py --config config.toml
```

Les options passées en ligne de commande sont prioritaires sur le fichier
de configuration.

| Option | Clé TOML | Rôle | Défaut |
|---|---|---|---|
| `--input`, `-i` | `[chemins] entree` | Dossier des fichiers ASC | `./asc_data/` |
| `--output`, `-o` | `[chemins] sortie` | Dossier des TIF produits | `./tif_data/` |
| `--compress`, `-c` | `[conversion] compression` | `LZW`, `DEFLATE` ou `NONE` | `LZW` |
| `--epsg`, `-e` | `[conversion] epsg` | Projection à affecter (ex. 2154) | aucune |
| `--workers`, `-w` | `[conversion] workers` | Processus parallèles | automatique |
| — | `[gdal] conda_prefix` | Environnement conda contenant GDAL, si aucun n'est activé | — |

## Sortie

Un GeoTIFF par fichier `.asc`, de même nom, dans le dossier de sortie.

Les fichiers `.asc` ne portent pas de projection : préciser `--epsg` pour
obtenir des GeoTIFF géoréférencés (2154 pour le RGE ALTI en Lambert 93).
