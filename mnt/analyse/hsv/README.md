# HSV — Loi Hauteur / Surface / Volume

Calcule la loi cote / surface / volume d'un MNT (GeoTIFF) dans une emprise
polygonale fournie par une couche GeoPackage.

Pour chaque cote `h` :
- **surface** (ha) = aire des pixels du MNT d'altitude ≤ `h` dans l'emprise ;
- **volume** (hm³) = Σ (`h` − altitude) × aire_pixel sur ces pixels.

La hauteur est comptée depuis le point le plus bas de l'emprise. Le volume est
toujours le volume total stocké sous la cote, y compris sous `--cote-min`.
Avec `--cote-min`/`--cote-max`, le graphique est cadré exactement sur ces bornes.

## Installation

    pip install -r requirements.txt

## Utilisation

    python hsv.py mnt.tif emprise.gpkg --pas 0.1 --sortie hsv.csv --graphique hsv.png

Options utiles :

| Option | Rôle |
|---|---|
| `--couche NOM` | couche du GeoPackage (défaut : la première) |
| `--filtre "nom == 'Bassin A'"` | ne garder que certaines entités |
| `--champ-id nom` | une loi par entité (sinon les entités sont fusionnées) |
| `--pas 0.1` | pas de cote en mètres |
| `--cotes 101 102.5 104` | cotes explicites à la place du pas |
| `--cote-min 245` | borne basse de la retenue (défaut : point bas de l'emprise) |
| `--cote-max 252` | borne haute de la retenue (défaut : point haut de l'emprise) |
| `--all-touched` | inclure tous les pixels touchés par l'emprise |
| `--separateur ,` | séparateur du CSV (défaut `;`) |

L'emprise est automatiquement reprojetée dans le SCR du MNT ; les pixels
NoData sont ignorés.

Sortie CSV : `entite;cote_m;hauteur_m;surface_ha;volume_hm3`.
