# extract_flux — Débits aux frontières liquides TELEMAC-2D

Extrait du listing d'un calcul TELEMAC-2D (`.out`) les débits aux
frontières liquides (lignes `FLUX BOUNDARY <n>: <valeur> M3/S` du bilan de
masse) et les associe à l'itération et au temps. Permet de récupérer les
hydrogrammes sans relancer de post-traitement sur le fichier résultat.

## Prérequis

Python 3 uniquement. `matplotlib` est nécessaire seulement pour l'option
`--plot`.

## Utilisation

```bash
python extract_flux.py listing.out                   # frontière 6 par défaut
python extract_flux.py listing.out -b 6 -o flux6.csv
python extract_flux.py listing.out -b 2 4 6 --plot
```

| Argument | Rôle | Défaut |
|---|---|---|
| `listing` | Listing TELEMAC-2D (`.out`) | — |
| `-b`, `--boundaries` | Numéros des frontières à extraire | 6 |
| `-o`, `--output` | CSV de sortie | `<listing>_flux.csv` |
| `--plot` | Trace les hydrogrammes en PNG | non |

Le bilan de masse doit être activé dans le fichier de paramètres TELEMAC
(`MASS-BALANCE = YES`) pour que les lignes `FLUX BOUNDARY` figurent dans le
listing.

## Sorties

- CSV (séparateur `;`) : `iteration ; temps_s ; temps_h ; Q_<n>…`
  (débits en m³/s) ;
- avec `--plot` : PNG des hydrogrammes, de même nom que le CSV ;
- résumé min / max / moyenne par frontière affiché à l'écran.
