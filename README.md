# outils-travail

Scripts Python d'études hydrauliques : données météo, MNT, TELEMAC.
Chaque outil a son dossier, avec un README qui détaille son utilisation.

| Thème | Outil | Rôle |
|---|---|---|
| **Météo** | [`meteo_download`](meteo/meteo_download/) | Données climatologiques quotidiennes Météo-France : stations proches, téléchargement, analyse des lacunes |
| **MNT — téléchargement** | [`lidarhd`](mnt/telechargement/lidarhd/) | MNT LiDAR HD de l'IGN (WMS) sur une emprise GeoPackage |
| | [`dgm1`](mnt/telechargement/dgm1/) | MNT DGM1 du Bade-Wurtemberg (LGL-BW) sur une emprise GeoPackage |
| | [`rge_alti`](mnt/telechargement/rge_alti/) | Extraction des dalles RGE ALTI 1 m d'une archive `.7z` sur une emprise |
| **MNT — traitement** | [`fusion_tif`](mnt/traitement/fusion_tif/) | Mosaïque des GeoTIFF d'un dossier |
| | [`asc2tif`](mnt/traitement/asc2tif/) | Conversion `.asc` (ESRI ASCII Grid ou points XYZ) → GeoTIFF |
| | [`lacunes_raster`](mnt/traitement/lacunes_raster/) | Comblement des NoData d'un raster, par tuiles |
| | [`calcul_rasters`](mnt/traitement/calcul_rasters/) | Calcul entre deux rasters partiellement superposés (différence, moyenne…) |
| **MNT — analyse** | [`hsv`](mnt/analyse/hsv/) | Loi hauteur / surface / volume d'une retenue |
| **TELEMAC — pré-traitement** | [`bathy_calage`](telemac/pre_traitement/bathy_calage/) | Bathymétrie aux nœuds du maillage à partir de sources priorisées |
| **TELEMAC — post-traitement** | [`hmax_lidar`](telemac/post_traitement/hmax_lidar/) | Hauteur d'eau maximale projetée sur le LiDAR HD |
| | [`extract_flux`](telemac/post_traitement/extract_flux/) | Débits aux frontières liquides depuis le listing |
| | [`extract_section`](telemac/post_traitement/extract_section/) | Sections de contrôle vers Excel |
| **Outils** | [`interpolation_lineaire`](outils/interpolation_lineaire/) | Interpolation linéaire 1D d'une série x / y à pas constant |

## Configuration

Les outils qui ont des chemins ou des paramètres d'étude les lisent dans un
fichier TOML passé avec `--config`. Chaque dossier concerné contient un
`config_exemple.toml` commenté :

```bash
cp config_exemple.toml config.toml   # puis adapter
python <script>.py --config config.toml
```

Les fichiers `config.toml` sont ignorés par git : les chemins propres à
chaque poste restent locaux.

Python 3.11 ou plus est nécessaire pour `--config` (module `tomllib`).

## Enchaînements courants

- **MNT d'une zone d'étude** : `lidarhd` (ou `rge_alti` puis `asc2tif`) →
  `fusion_tif` → `lacunes_raster`.
- **Modèle TELEMAC** : `bathy_calage` pour la bathymétrie aux nœuds, puis
  après calcul `extract_flux`, `extract_section` et `hmax_lidar`.
