# Script d'Interpolation Linéaire

## Description
Ce script Python permet d'interpoler linéairement des données depuis un fichier texte contenant deux colonnes (abscisse et ordonnée).

## Prérequis
- Python 3
- NumPy (`pip install numpy`)

## Format du fichier d'entrée
Le fichier d'entrée doit contenir deux colonnes séparées par des espaces ou des tabulations :
```
x1  y1
x2  y2
x3  y3
...
```

Exemple (`donnees_exemple.txt`) :
```
0.0 1.0
1.0 2.5
2.5 3.0
4.0 5.5
6.0 4.0
8.0 6.0
10.0 7.5
```

## Utilisation

### Syntaxe de base
```bash
python interpolation_lineaire.py <fichier_entree> <pas> [-o <fichier_sortie>]
```

### Arguments
- `fichier_entree` : Chemin du fichier contenant les données (obligatoire)
- `pas` : Pas d'interpolation (obligatoire)
- `-o, --sortie` : Nom du fichier de sortie (optionnel, défaut: `sortie_interpolee.txt`)
- `--xmin` : Point initial d'interpolation (optionnel, défaut: premier point des données)
- `--xmax` : Point final d'interpolation (optionnel, défaut: dernier point des données)

### Exemples

#### Exemple 1 : Interpolation avec un pas de 0.5
```bash
python interpolation_lineaire.py donnees_exemple.txt 0.5
```
Résultat : `sortie_interpolee.txt`

#### Exemple 2 : Spécifier le fichier de sortie
```bash
python interpolation_lineaire.py donnees_exemple.txt 0.5 -o mes_resultats.txt
```
Résultat : `mes_resultats.txt`

#### Exemple 3 : Interpolation avec bornes personnalisées
```bash
python interpolation_lineaire.py donnees_exemple.txt 0.5 --xmin 2.0 --xmax 8.0
```
Interpole uniquement entre x=2.0 et x=8.0

#### Exemple 4 : Extrapolation (bornes hors des données)
```bash
python interpolation_lineaire.py donnees_exemple.txt 1.0 --xmin -1.0 --xmax 12.0
```
Le script effectue une extrapolation linéaire en dehors de la plage des données

#### Exemple 5 : Interpolation fine sur une zone précise
```bash
python interpolation_lineaire.py donnees_exemple.txt 0.1 --xmin 3.5 --xmax 6.5 -o zone_detaillee.txt
```

## Format du fichier de sortie
Le fichier de sortie contient deux colonnes séparées par des tabulations :
- Colonne 1 : Abscisses interpolées
- Colonne 2 : Ordonnées interpolées

Les valeurs sont formatées avec 6 décimales.

## Fonctionnalités
- ✅ Interpolation linéaire avec NumPy
- ✅ Tri automatique des données si nécessaire
- ✅ Gestion des erreurs
- ✅ Affichage des informations de progression
- ✅ Format de sortie propre et lisible

## Notes
- Si les abscisses du fichier d'entrée ne sont pas triées, le script les trie automatiquement
- Le dernier point (x_max) est toujours inclus dans les résultats
- Par défaut, l'interpolation se fait entre le premier et le dernier point des données d'entrée
- Si vous spécifiez des bornes (`--xmin` et/ou `--xmax`) en dehors de la plage des données, le script effectuera une **extrapolation linéaire** et vous en informera
- L'extrapolation peut donner des résultats imprécis en dehors de la plage des données mesurées

## Aide
Pour afficher l'aide :
```bash
python interpolation_lineaire.py -h
```
