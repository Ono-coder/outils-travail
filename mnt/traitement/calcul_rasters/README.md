# calcul_rasters — Calcul entre deux rasters partiellement superposés

Applique une opération pixel à pixel entre deux GeoTIFF A et B (différence
de MNT, moyenne, min/max, expression libre…) qui ne se recouvrent que par
endroits, en laissant choisir le résultat **en dehors de la zone de
recouvrement**.

Pour chaque pixel de la grille de sortie :

| Situation | Résultat |
|---|---|
| A et B valides | opération choisie |
| seul A valide | selon `--seul-a` (ou `--hors-recouvrement`) |
| seul B valide | selon `--seul-b` (ou `--hors-recouvrement`) |
| aucun valide | NoData |

Les pixels NoData à l'intérieur d'un raster sont traités comme « absents ».

## Prérequis

GDAL avec ses liaisons Python et numpy (par exemple l'environnement conda
`pdal_env`). Python 3.11 ou le paquet `tomli` pour l'option `--config`.

## Utilisation

```bash
python calcul_rasters.py --a mnt_2025.tif --b mnt_2020.tif --output diff.tif
python calcul_rasters.py --a a.tif --b b.tif -o moy.tif --operation moyenne --hors-recouvrement conserver
python calcul_rasters.py --a a.tif --b b.tif -o diff.tif --seul-a conserver --seul-b nodata
python calcul_rasters.py --a a.tif --b b.tif -o r.tif --expression "np.abs(a - b)"
```

Ou avec un fichier de configuration :

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python calcul_rasters.py --config config.toml
```

Les options passées en ligne de commande sont prioritaires sur le fichier
de configuration.

| Option | Clé TOML | Rôle | Défaut |
|---|---|---|---|
| `--a` | `[chemins] raster_a` | Raster A (grille de référence) | — |
| `--b` | `[chemins] raster_b` | Raster B | — |
| `--output`, `-o` | `[chemins] sortie` | Raster de sortie | `./resultat.tif` |
| `--operation` | `[calcul] operation` | Opération dans le recouvrement (voir ci-dessous) | `difference` |
| `--expression` | `[calcul] expression` | Expression numpy en `a` et `b`, remplace `--operation` | — |
| `--hors-recouvrement` | `[hors_recouvrement] mode` | Comportement là où un seul raster est valide | `nodata` |
| `--seul-a` | `[hors_recouvrement] seul_a` | Idem, uniquement là où seul A est valide | `--hors-recouvrement` |
| `--seul-b` | `[hors_recouvrement] seul_b` | Idem, uniquement là où seul B est valide | `--hors-recouvrement` |
| `--valeur` | `[hors_recouvrement] valeur` | Valeur des modes `constante` et `remplacer` | `0` |
| `--emprise` | `[calcul] emprise` | Emprise de sortie : `union`, `intersection`, `a`, `b` | `union` |
| `--resampling` | `[calcul] resampling` | Rééchantillonnage de B : `near`, `bilinear`, `cubic`, `average` | `bilinear` |
| `--nodata-a`, `--nodata-b` | `[calcul] nodata_a`, `nodata_b` | Forcer la valeur NoData d'une entrée | celle du fichier |
| `--compress`, `-c` | — | Compression : `DEFLATE`, `LZW`, `NONE` | `DEFLATE` |
| `--taille-bloc` | — | Taille des blocs de calcul (px) | `2048` |

### Opérations

| `--operation` | Calcul |
|---|---|
| `difference` | A − B |
| `addition` | A + B |
| `multiplication` | A × B |
| `division` | A / B (NoData si B = 0) |
| `moyenne` | (A + B) / 2 |
| `minimum`, `maximum` | min(A, B), max(A, B) |
| `a`, `b` | A ou B seul (utile avec `--hors-recouvrement conserver` pour une mosaïque prioritaire) |

### Hors recouvrement

| Mode | Résultat là où un seul raster est valide |
|---|---|
| `nodata` | NoData |
| `conserver` | valeur du raster présent |
| `constante` | valeur fixe `--valeur` |
| `remplacer` | le raster absent prend la valeur `--valeur`, puis l'opération est appliquée (ex. différence avec `--valeur 0` : A − 0 = A là où seul A existe, 0 − B = −B là où seul B existe) |

## Sortie

GeoTIFF float32 tuilé et compressé, NoData = −9999, dans la projection et
sur la grille (résolution, alignement des pixels) de A. B est rééchantillonné
et reprojeté si besoin. Le calcul se fait par blocs, donc les gros rasters
ne saturent pas la mémoire.

Le journal (`calcul_rasters.log`) indique la surface de chaque zone
(recouvrement, A seul, B seul) et les statistiques du résultat.
