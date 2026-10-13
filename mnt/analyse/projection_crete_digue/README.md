# projection_crete_digue — Crête de digue sur le profil en long

Projette la crête d'une digue (rive gauche ou droite) sur le profil en long
d'un cours d'eau, à partir d'un MNT, en traitant deux difficultés
classiques :

1. **Décalage des PK** : la digue méandre davantage que l'axe hydraulique.
   Chaque point de crête est reporté sur l'axe de référence (celui qui porte
   les PK) par projection orthogonale, et non par sa propre abscisse
   curviligne.
2. **Dépressions parasites** : la ligne de crête numérisée peut tomber à côté
   du point haut (ornière, ligne décalée, bruit LiDAR). Le MNT est
   échantillonné sur une bande transversale de part et d'autre de la ligne,
   et le **maximum transversal** est retenu. Aucun lissage longitudinal
   n'est appliqué : un point bas réel de la crête (point de surverse) est
   conservé.

Les tronçons perpendiculaires à l'axe (retours de digue) sont détectés et,
au choix, signalés, exclus ou exportés à part.

## Prérequis

```bash
pip install numpy pandas geopandas "shapely>=2" rasterio matplotlib
```

Utiliser un **MNT** (terrain nu) et non un MNS.

## Utilisation

```bash
python projection_crete_digue.py --axe axe_profil_long.shp --crete crete_digue_RD.shp \
    --mnt mnt_lidar.tif --pas-crete 0.5 --demi-largeur 4 --pas-transversal 0.25 \
    --lissage-axe 50 --pas-pk 5 --angle-max 70 --longueur-min-retour 10 --retours exclure
```

| Option | Rôle | Défaut |
|---|---|---|
| `--axe` | Ligne de l'axe de référence portant les PK — obligatoire | — |
| `--crete` | Ligne de crête de digue — obligatoire | — |
| `--mnt` | MNT (GeoTIFF ou VRT) — obligatoire | — |
| `--couche-axe`, `--couche-crete` | Couche à lire si le fichier en contient plusieurs | première |
| `--crs-mnt` | Force le système du MNT (ex. `EPSG:2154`) sans reprojection | lu dans le fichier |
| `--ignorer-crs` | Ne pas vérifier la cohérence des systèmes | non |
| `--pas-crete` | Pas de densification le long de la crête (m) | 0,5 |
| `--demi-largeur` | Demi-largeur de la bande transversale (m) | 4 |
| `--pas-transversal` | Pas d'échantillonnage transversal (m), ≤ résolution du MNT | 0,25 |
| `--lissage-tangente` | Longueur de lissage de la direction de la crête (m) | 10 |
| `--lissage-axe` | Longueur de lissage de l'axe (m), 0 = aucun | 50 |
| `--pas-pk` | Pas d'agrégation du profil en long (m) | 5 |
| `--angle-max` | Angle crête / axe (°) au-delà duquel un tronçon est perpendiculaire | 60 |
| `--longueur-min-retour` | Longueur minimale d'un retour de digue (m) | 10 |
| `--retours` | `signaler`, `exclure` ou `separer` les tronçons perpendiculaires | `signaler` |
| `--distance-max` | Distance crête–axe maximale acceptée (m) | aucune |
| `--pk-origine` | PK à l'origine de l'axe | 0 |
| `--inverser-axe` | PK croissants vers l'amont | non |
| `--ligne-eau` | CSV `pk,z` d'une ligne d'eau, pour calculer la revanche | aucun |
| `--pas-qa-bandes` | Pas d'export des bandes de contrôle (m) | 25 |
| `--sortie` | Dossier de sortie | `./resultats` |

## Sorties (dans `--sortie`)

| Fichier | Contenu |
|---|---|
| `crete_points.csv` | Un enregistrement par point densifié : PK, déport, Z, indicateurs de contrôle |
| `profil_crete_pk.csv` | Profil agrégé par pas de PK : Z min / médian / max |
| `qa_points.gpkg` | Points de crête retenus, avec leurs drapeaux de contrôle |
| `qa_bandes.gpkg` | Bandes transversales échantillonnées, pour contrôle visuel |
| `profil_en_long.png` | Figure du profil en long |
