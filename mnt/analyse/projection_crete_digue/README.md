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

Ou avec un fichier de configuration, plus pratique vu le nombre d'options :

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python projection_crete_digue.py --config config.toml
python projection_crete_digue.py --config config.toml --pas-pk 1   # une option ponctuelle
```

Les options passées en ligne de commande l'emportent sur le fichier. Une clé
mal orthographiée ou une valeur invalide arrête le programme avec un message
explicite. Correspondance entre les options et les clés du fichier :

| Section | Clés (option correspondante : `cle` → `--cle` avec des tirets) |
|---|---|
| `[entrees]` | `axe`, `couche_axe`, `crete`, `couche_crete`, `mnt`, `crs_mnt`, `ignorer_crs`, `ligne_eau` |
| `[pk]` | `bornes`, `couche_bornes`, `champ_pk`, `unite_bornes`, `pk_origine`, `inverser_axe` |
| `[echantillonnage]` | `pas_crete`, `demi_largeur`, `pas_transversal`, `lissage_tangente`, `lissage_axe`, `distance_max` |
| `[profil]` | `pas_pk`, `angle_max`, `longueur_min_retour`, `retours`, `pas_qa_bandes`, `axe_km` |
| `[sorties]` | `dossier` (→ `--sortie`) |

Les options oui/non (`ignorer_crs`, `inverser_axe`, `axe_km`) activées dans le
fichier ne peuvent pas être désactivées en ligne de commande : les modifier
dans le fichier.

| Option | Rôle | Défaut |
|---|---|---|
| `--config` | Fichier de configuration TOML | aucun |
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
| `--pk-origine` | PK à l'origine de l'axe (décalage constant ; inutile avec `--bornes`) | 0 |
| `--bornes` | Couche de points des bornes PK réelles : recalage intervalle par intervalle (voir ci-dessous) | aucune |
| `--couche-bornes` | Couche à lire si le fichier en contient plusieurs | première |
| `--champ-pk` | Champ portant le PK des bornes | `pk`, `pk_km`, `borne`… détecté |
| `--unite-bornes` | Unité de ce champ : `km` ou `m` | `km` |
| `--axe-km` | Graduer l'axe des PK du graphique en km | non |
| `--inverser-axe` | PK croissants vers l'amont | non |
| `--ligne-eau` | CSV `pk,z` d'une ligne d'eau, pour calculer la revanche | aucun |
| `--pas-qa-bandes` | Pas d'export des bandes de contrôle (m) | 25 |
| `--sortie` | Dossier de sortie | `./resultats` |

## Comment sont calculés les PK

Chaque point de crête est projeté perpendiculairement sur l'axe de
référence. Son PK dépend de la position du pied de cette perpendiculaire le
long de l'axe, jamais de la longueur propre de la digue. Une digue qui
méandre ne décale donc pas les PK : plusieurs mètres de crête tombent sur le
même mètre de PK (colonne `compression` du profil).

Deux façons de passer de cette position aux PK du canal :

- **Sans bornes** : `PK = --pk-origine + distance le long de l'axe`. C'est
  exact si l'espacement réel entre bornes est égal à la longueur de l'axe
  numérisé.
- **Avec `--bornes`** : chaque borne est projetée sur l'axe ; entre deux
  bornes successives, le PK est interpolé linéairement. L'écart réel entre
  bornes (985 m, 1 025 m…) est donc respecté, et le profil tombe exactement
  sur le PK officiel en face de chaque borne. Avant la première et après la
  dernière borne, le PK avance de 1 m par mètre d'axe. Une seule borne
  revient à calculer automatiquement `--pk-origine`.

  ```bash
  python projection_crete_digue.py --axe axe.shp --crete crete.shp --mnt mnt.tif \
      --bornes bornes_pk.shp --champ-pk PK --axe-km
  ```

  Les bornes doivent se succéder dans le même ordre le long de l'axe que
  leurs PK (tous croissants ou tous décroissants) ; sinon le programme
  s'arrête en indiquant la borne suspecte. Le bilan affiche, pour chaque
  intervalle, la longueur mesurée sur l'axe et l'écart au kilomètre
  officiel. Au-delà de 10 % d'écart, une borne est probablement mal placée
  ou mal numérotée.

  Avec `--ligne-eau`, les PK du CSV de ligne d'eau doivent être des PK
  officiels en mètres.

## Sorties (dans `--sortie`)

| Fichier | Contenu |
|---|---|
| `crete_points.csv` | Un enregistrement par point densifié : PK, déport, Z, indicateurs de contrôle |
| `profil_crete_pk.csv` | Profil agrégé par pas de PK : Z min / médian / max |
| `qa_points.gpkg` | Points de crête retenus, avec leurs drapeaux de contrôle |
| `qa_bandes.gpkg` | Bandes transversales échantillonnées, pour contrôle visuel |
| `profil_en_long.png` | Figure du profil en long (avec une ligne verticale par borne si `--bornes`) |
| `bornes_recalage.csv` | Avec `--bornes` : PK officiel, position sur l'axe et distance à l'axe de chaque borne |
| `bornes_intervalles.csv` | Avec `--bornes` : longueur réelle de chaque intervalle et écart au PK officiel |
