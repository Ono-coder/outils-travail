# swissalti3d — MNT swissALTI3D de swisstopo

Télécharge les dalles du MNT swissALTI3D (Suisse) couvrant une emprise en
coordonnées suisses MN95 / LV95 (EPSG:2056), via l'API STAC de
`data.geo.admin.ch`.

## Prérequis

```bash
pip install requests
```

Accès internet à `data.geo.admin.ch`.

## Utilisation

```bash
python swissalti3d.py --emprise 2494961 1145942 2502877 1156360 --sortie ./swissalti3d_downloads
```

Ou avec un fichier de configuration :

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python swissalti3d.py --config config.toml
```

Les options passées en ligne de commande sont prioritaires sur le fichier.

| Option | Clé TOML | Rôle | Défaut |
|---|---|---|---|
| `--emprise XMIN YMIN XMAX YMAX` | `[emprise] lv95` | Emprise en MN95 / LV95 — obligatoire | — |
| `--sortie` | `[sorties] dossier` | Dossier de téléchargement | `./swissalti3d_downloads` |
| `--max-items` | `[telechargement] max_items` | Nombre maximal de dalles demandées | 1000 |

## Limite de 100 dalles par recherche

Le service STAC renvoie au plus **100 dalles par requête** : sur une grande
emprise, seules les 100 premières sont téléchargées. Découper alors
l'emprise en plusieurs parties, lancer le script pour chacune, assembler
chaque lot avec [`fusion_reechantillonnage`](../../traitement/fusion_reechantillonnage/)
ou [`fusion_tif`](../../traitement/fusion_tif/), puis fusionner les
mosaïques obtenues (dans QGIS par exemple).

## Sorties

Les fichiers GeoTIFF des dalles swissALTI3D, en MN95 / LV95, dans le dossier
de sortie.
