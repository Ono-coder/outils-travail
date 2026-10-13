# scan_ign — Fonds de carte SCAN de l'IGN

Procédure pour récupérer les cartes raster de l'IGN, gratuites, sous forme
d'archives départementales `.7z` de dalles GeoTIFF en Lambert 93 :

| Produit | Contenu |
|---|---|
| SCAN 25 | Carte topographique au 1:25 000 |
| SCAN 100 | Carte topographique au 1:100 000 |
| SCAN OACI | Carte aéronautique au 1:500 000 (France métropolitaine) |

## Télécharger

Les liens de téléchargement sont publiés par l'IGN sur la Géoplateforme :
- <https://geoservices.ign.fr/scan25>
- <https://geoservices.ign.fr/scan100>
- <https://geoservices.ign.fr/scanoaci>

Choisir le département (ou la France entière pour le SCAN OACI) et
télécharger l'archive `.7z` correspondante. Les liens contiennent une clé
d'accès propre à chaque utilisateur ou à chaque diffusion : ne pas les
recopier dans un dépôt public.

## Assembler les dalles

Après décompression de l'archive (7-Zip), assembler les dalles d'un
département en une seule image avec [`fusion_tif`](../../mnt/traitement/fusion_tif/) :

```bash
python fusion.py --input ./SCAN25_D067/dalles --output SCAN25_D067.tif
```

Ou créer un raster virtuel (VRT) dans QGIS, sans fusion.
