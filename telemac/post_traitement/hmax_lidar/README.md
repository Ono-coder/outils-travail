# hmax_lidar — Hauteur d'eau maximale TELEMAC projetée sur le LiDAR HD

Projette la hauteur d'eau maximale d'un calcul TELEMAC-2D (rupture de
digue, brèche…) sur un MNT LiDAR HD, pour obtenir une carte de Hmax à la
résolution du LiDAR plutôt qu'aux nœuds du maillage.

Principe :
1. lecture du résultat TELEMAC : cote du fond et cote maximale de la
   surface libre (variable « cote maxi » si présente, sinon maximum de la
   surface libre sur les pas de temps) ;
2. prolongement horizontal de la ligne d'eau d'une maille au-delà des nœuds
   mouillés, pour que le MNT fin dessine la limite d'inondation ;
3. fusion du MNT : LiDAR là où il existe, petits trous comblés par
   interpolation, grands trous (lit mineur, bâti) remplis par la bathymétrie
   du modèle avec une transition ;
4. interpolation de la surface libre maximale sur la grille du LiDAR, puis
   H = Zs − MNT ;
5. nettoyage : hauteur minimale, suppression des zones non connectées à la
   zone inondée.

## Prérequis

```bash
pip install numpy scipy matplotlib rasterio
```

L'environnement TELEMAC n'est pas nécessaire : le fichier Selafin
(`.slf` / `.res`) est lu directement. Le maillage et le LiDAR doivent être
dans le même système (ex. Lambert 93 / NGF-IGN69).

## Utilisation

```bash
python hmax_lidar.py r2d_breche.slf lidarhd.vrt -o sorties
```

Pour un LiDAR en dalles, créer d'abord un VRT :
`gdalbuildvrt lidarhd.vrt dalles/*.tif`. Seule l'emprise du maillage
(plus une marge) est lue. Les chemins copiés depuis QGIS au format
`file:///C:/...` sont acceptés.

| Argument | Rôle | Défaut |
|---|---|---|
| `resultat` | Résultat TELEMAC-2D (`.slf` / `.res`) | — |
| `lidar` | MNT LiDAR HD (GeoTIFF ou VRT) | — |
| `-o`, `--sortie` | Dossier de sortie | `.` |
| `--h-sec` | Seuil de nœud mouillé (m) | 0,02 |
| `--h-min` | Hauteur minimale conservée sur le raster (m) | 0,01 |
| `--erosion` | Pixels retirés en bord de trou LiDAR | 1 |
| `--s-min` | Surface maximale d'un trou comblé par le LiDAR (m²) | 50 |
| `--l-trans` | Largeur de transition bord de trou → bathymétrie (m) | 3 |
| `--fill-dist` | Distance maximale d'interpolation des petits trous (px) | 20 |
| `--marge` | Marge autour du maillage pour lire le LiDAR (m) | 20 |
| `--emprise XMIN YMIN XMAX YMAX` | Restreindre le calcul à cette emprise | tout le maillage |
| `--var-fond`, `--var-surface`, `--var-zmax` | Noms exacts des variables si la détection automatique échoue | détection automatique |
| `--sans-intermediaires` | N'écrire que `hmax.tif` | non |
| `-v`, `--verbeux` | Messages détaillés | non |

## Sorties

| Fichier | Contenu |
|---|---|
| `hmax.tif` | Hauteur d'eau maximale projetée sur le MNT |
| `mnt_fusion.tif` | MNT fusionné LiDAR / bathymétrie |
| `classes_fusion.tif` | 1 = LiDAR, 2 = trou comblé par le LiDAR, 3 = bathymétrie |
| `zs_max.tif` | Surface libre maximale interpolée |

## Mémoire

Compter environ 50 octets par pixel au pic : une emprise de 5 km × 5 km à
0,5 m (10⁸ pixels) demande environ 5 Go. Au-delà, découper le calcul avec
`--emprise`.
