# elements_surcontraints — Triangles surcontraints d'un maillage TELEMAC

Repère les triangles surcontraints d'un maillage TELEMAC : les triangles qui
ont **plus d'un côté sur la frontière** du domaine, donc tous leurs nœuds sur
le bord. Ces éléments perturbent le calcul et sont à supprimer ou à
remailler avant de lancer TELEMAC.

## Prérequis

```bash
pip install numpy matplotlib
```

Les modules Python de TELEMAC (`data_manip`) : soit l'environnement TELEMAC
est chargé, soit on indique le dossier `scripts/python3` de TELEMAC avec
`--telemac`.

## Utilisation

```bash
python elements_surcontraints.py maillage.slf
python elements_surcontraints.py maillage.slf --telemac C:/TELEMAC/V8P5/scripts/python3 --graphique carte.png
```

| Option | Rôle | Défaut |
|---|---|---|
| `maillage` | Maillage Selafin (`.slf`) | — |
| `-o`, `--sortie` | Fichier des nœuds des triangles repérés | `over_constrained_triangles.xyz` à côté du maillage |
| `--telemac` | Dossier `scripts/python3` de TELEMAC | environnement chargé |
| `--graphique` | Carte PNG du maillage avec les triangles repérés en rouge | aucune |

## Sortie

Un fichier texte `X Y` listant les nœuds des triangles surcontraints (trois
lignes par triangle), à importer dans QGIS ou BlueKenue pour localiser les
éléments à corriger.
