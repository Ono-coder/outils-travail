# fusion_reechantillonnage — Mosaïque et rééchantillonnage de GeoTIFF

Fusionne tous les GeoTIFF d'un dossier en une mosaïque, puis la
rééchantillonne à une taille de maille choisie. Tout passe par rasterio :
contrairement à [`fusion_tif`](../fusion_tif/), aucun exécutable GDAL n'est
nécessaire, et la méthode de fusion dans les zones de recouvrement est
réglable.

## Prérequis

```bash
pip install rasterio
```

## Utilisation

```bash
python fusion_reechantillonnage.py -i ./dalles -o ./resultat -s 2
python fusion_reechantillonnage.py -i ./dalles -m max --skip-resampling
```

| Option | Rôle | Défaut |
|---|---|---|
| `-i`, `--input` | Dossier des GeoTIFF (`.tif`, `.tiff`) | dossier courant |
| `-o`, `--output` | Dossier de sortie | `<input>/outputs` |
| `-s`, `--cell-size` | Taille de maille du rééchantillonnage (unité du système, m en général) | 2 |
| `-m`, `--merge-method` | Valeur retenue dans les recouvrements : `first`, `last`, `min`, `max`, `mean` | `first` |
| `-r`, `--resampling` | Méthode : `nearest`, `bilinear`, `cubic`, `average`, `mode` | `bilinear` |
| `--skip-resampling` | Fusion seule, sans rééchantillonnage | non |

## Sorties

- `merged_image.tif` : la mosaïque à la résolution d'origine ;
- `merged_image_resampled_<taille>m.tif` : la mosaïque rééchantillonnée.

Les fichiers illisibles sont signalés et ignorés. Tous les fichiers doivent
être dans le même système de coordonnées.
