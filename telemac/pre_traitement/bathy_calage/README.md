# bathy_calage — Bathymétrie aux nœuds d'un maillage TELEMAC

Affecte une cote de fond à chaque nœud d'un maillage TELEMAC à partir de
plusieurs sources de bathymétrie / topographie (rasters ou fichiers XYZ),
**par ordre de priorité** : un nœud prend la valeur de la première source
qui le couvre avec une valeur valide.

Chaque source peut avoir son propre système de coordonnées et une
translation verticale (changement de référence altimétrique par exemple).

## Prérequis

```bash
pip install numpy pandas geopandas rasterio pyproj matplotlib
```

Python 3.11 ou plus.

## Utilisation

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python bathy_calage.py --config config.toml
```

Conseil : tester d'abord sur une petite emprise de quelques milliers de
nœuds. Le temps de calcul croît avec le nombre de nœuds (ordre de grandeur :
20 min pour 4 millions de nœuds et 3 sources dont une dans un autre
référentiel).

## Configuration (`config.toml`)

| Section / clé | Rôle | Défaut |
|---|---|---|
| `[entrees] noeuds_shp` | Couche des nœuds du maillage (points ou géométries ramenées à des points) — obligatoire | — |
| `[[rasters]]` | Une section par source, **dans l'ordre de priorité** — au moins une | — |
| ↳ `chemin` | Raster (GeoTIFF…) ou fichier `.xyz` / `.txt` (X Y Z) | — |
| ↳ `label` | Nom de la source (carte, attributs) | — |
| ↳ `epsg` | EPSG de la source (sinon lu dans le fichier) | lu dans le fichier |
| ↳ `z_offset` | Translation verticale ajoutée aux valeurs (m) | 0 |
| `[parametres] epsg_sortie` | Système de travail et de sortie | 2154 |
| `[parametres] z_min`, `z_max` | Valeurs hors de cet intervalle ignorées | -30 / 300 |
| `[parametres] nodata_fallback` | Valeurs inférieures considérées comme NoData | -9000 |
| `[parametres] offset_x`, `offset_y` | Translation repère local TELEMAC → Lambert 93 (0 si nœuds déjà en L93) | 0 |
| `[sorties] dossier` | Dossier de sortie — obligatoire | — |
| `[sorties] nom_shp` | Nom du shapefile de sortie (sans extension) | `noeuds_bathy` |
| `[sorties] nom_csv_bathy` | Nom du CSV de bathymétrie (sans extension) | `noeuds_bathy_locale` |
| `[carte] titre`, `colormap` | Titre et palette matplotlib de la carte | `Bathymétrie` / `terrain` |
| `[proj] chemins_secours` | Dossiers contenant `proj.db`, essayés si l'environnement conda n'en fournit pas | aucun |

Chemins Windows : les écrire entre apostrophes simples (`'C:\...'`).

## Sorties (dans `[sorties] dossier`)

- `<nom_shp>.shp` : nœuds en repère local TELEMAC, avec `Z_bathy` (cote
  retenue), `src_bathy` (source utilisée) et `has_bathy` ;
- `<nom_csv_bathy>.csv` : bathymétrie des nœuds calés, colonnes
  `X_loc ; Y_loc ; Z_bathy` ;
- `noeuds_manquants.csv` : nœuds sans valeur (coordonnées locales et L93) ;
- `carte_bathymetrie.png` : carte de la bathymétrie et des sources
  utilisées.
