# interpolation_lacunes — Comblement des NoData d'un raster

Interpole les pixels NoData d'un raster GeoTIFF (MNT, bathymétrie…) en le
traitant par tuiles avec recouvrement, ce qui permet de traiter des rasters
très volumineux (plus de 10 Go) sans saturer la mémoire.

Produit le raster comblé et, en option, une carte PNG des modifications.

## Prérequis

```bash
pip install numpy rasterio scipy opencv-python pillow
```

`opencv-python` n'est nécessaire que pour les méthodes `inpaint_*`, `scipy`
que pour `nearest` / `bilinear` / `cubic`, `pillow` que pour la carte PNG.

## Utilisation

```bash
python interpolation_lacunes.py entree.tif sortie.tif --method inpaint_telea --map_png modifications.png
python interpolation_lacunes.py bathy.tif bathy_fill.tif --method cubic --tile_size 4096 --overlap 128
```

| Argument | Rôle | Défaut |
|---|---|---|
| `input` | Raster d'entrée | — |
| `output` | Raster comblé | — |
| `--method` | Méthode d'interpolation (voir ci-dessous) | `inpaint_telea` |
| `--band` | Bande à traiter | 1 |
| `--tile_size` | Taille des tuiles (px) | 2048 |
| `--overlap` | Recouvrement entre tuiles (px) | 64 |
| `--map_png` | Carte PNG des pixels modifiés | aucune |
| `--max_png_dim` | Dimension maximale du PNG (réduction automatique) | 8000 |

### Méthodes

| Méthode | Usage |
|---|---|
| `inpaint_telea` | Inpainting OpenCV TELEA — rapide, petites lacunes |
| `inpaint_ns` | Inpainting Navier-Stokes — grandes lacunes continues |
| `nearest` | Plus proche voisin |
| `bilinear` | Bilinéaire |
| `cubic` | Bicubique — recommandé pour MNT et bathymétrie |

### Taille de tuile selon la mémoire disponible

| RAM | `--tile_size` | `--overlap` |
|---|---|---|
| 8 Go | 1024 | 32 |
| 16 Go | 2048 | 64 |
| 32 Go | 4096 | 128 |

## Sorties

- le raster comblé (GeoTIFF float32, compression DEFLATE) ;
- la carte PNG si demandée : blanc = valide, rouge = interpolé,
  noir = NoData restant.
