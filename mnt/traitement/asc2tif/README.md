# asc2tif — Conversion de fichiers `.asc` → GeoTIFF

Convertit tous les fichiers `.asc` d'un dossier en GeoTIFF compressés, en
parallèle. Le format de chaque fichier est détecté automatiquement :

| Format | Reconnu par | Conversion |
|---|---|---|
| ESRI ASCII Grid | en-tête `ncols` / `nrows` | `gdal_translate` |
| Points XYZ (`X Y Z`), non triés, avec trous | absence d'en-tête | rastérisation numpy : résolution déduite de l'espacement des points, moyenne si plusieurs points par maille, NoData = -9999 |

Les fichiers déjà convertis sont ignorés à la relance. Pour de très gros
nuages XYZ ou des fichiers corrompus, préférer [`xyz2tif`](../xyz2tif/).

Typiquement utilisé sur les dalles RGE ALTI extraites par
[`extract_rgealti`](../../telechargement/rge_alti/).

## Prérequis

```bash
pip install numpy pandas      # + GDAL avec ses liaisons Python (osgeo), via conda
```

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
