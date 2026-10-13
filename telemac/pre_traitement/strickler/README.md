# strickler — Strickler aux nœuds d'un maillage à partir de Corine Land Cover

Affecte un coefficient de Strickler (m^(1/3)/s) à chaque nœud d'un maillage
hydraulique selon le polygone Corine Land Cover 2018 qui le contient, via une
table de correspondance code CLC → Strickler. Un polygone optionnel de lit
mineur permet d'imposer un Strickler calé séparément.

## Prérequis

```bash
pip install geopandas pandas
```

Python 3.11 ou plus (sinon `pip install tomli`). Les nœuds et la couche CLC
doivent être dans le même système de coordonnées.

## Utilisation

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python strickler.py --config config.toml
```

## Configuration (`config.toml`)

| Section / clé | Rôle | Défaut |
|---|---|---|
| `[entrees] noeuds_shp` | Couche des nœuds (points) — obligatoire | — |
| `[entrees] clc_shp` | Couche CLC 2018 (polygones) — obligatoire | — |
| `[entrees] lit_mineur_shp` | Emprise du lit mineur (polygone) | aucune |
| `[parametres] champ_code_clc` | Champ du code CLC | `Code_18` |
| `[parametres] ks_lit_mineur` | Strickler imposé dans le lit mineur | 35 |
| `[parametres] dx`, `dy` | Translation ajoutée aux coordonnées exportées (ex. L93 → repère local TELEMAC) | 0 |
| `[sorties] csv` | CSV de sortie — obligatoire | — |
| `[sorties] separateur` | Séparateur du CSV | `;` |
| `[sorties] decimales_coord`, `decimales_ks` | Décimales de X / Y et du Strickler | 3 / 2 |
| `[table_strickler]` | Redéfinition de codes CLC, ex. `231 = 20` | table du script |

La table par défaut (dans `strickler.py`) couvre les principaux codes CLC :
zones urbaines, cultures, prairies, forêts, eaux… Elle est à ajuster selon la
bibliographie retenue et le calage du modèle ; seuls les codes redéfinis dans
`[table_strickler]` changent.

## Sortie

CSV `X ; Y ; Strickler`, un nœud par ligne, dans l'ordre de la couche des
nœuds. Le script affiche :
- les codes CLC présents dans l'emprise mais absents de la table ;
- le nombre de nœuds sans Strickler (hors de tout polygone CLC), laissés
  vides dans le CSV.
