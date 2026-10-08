# extract_rgealti — Extraction de dalles RGE ALTI 1 m

Extrait d'une archive `.7z` départementale du RGE ALTI 1 m de l'IGN
uniquement les dalles (`.asc`, 1 km × 1 km) qui recoupent une emprise
rectangulaire en Lambert 93, sans décompresser toute l'archive.

## Prérequis

```bash
pip install py7zr
```

Télécharger au préalable l'archive du département voulu sur
<https://geoservices.ign.fr/telechargement-api/RGEALTI>.

## Utilisation

```bash
python extract_rgealti.py --archive RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D035_2022-12-16.7z \
    --xmin 315500 --ymin 6774400 --xmax 330415 --ymax 6788515 \
    --outdir ./rgealti_output
```

| Argument | Rôle |
|---|---|
| `--archive` | Archive `.7z` du RGE ALTI (obligatoire) |
| `--xmin --ymin --xmax --ymax` | Emprise en Lambert 93, en mètres (obligatoire) |
| `--outdir` | Dossier de sortie (défaut : `./rgealti_output`) |

## Suite du traitement

Les dalles extraites sont au format ESRI ASCII Grid. Pour les exploiter :
- les convertir en GeoTIFF avec [`asc2tif`](../../traitement/asc2tif/),
  puis les assembler avec [`fusion_tif`](../../traitement/fusion_tif/) ;
- ou créer directement un raster virtuel (VRT) dans QGIS avec l'ensemble
  des fichiers `.asc`.

Voir aussi `Exemple_commande.txt`.
