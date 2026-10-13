# vents_era5 — Rose des vents ERA5 en un point

Télécharge la chronique horaire du vent à 10 m (vitesse et direction) en un
point, issue de la réanalyse ERA5 via l'API d'archive d'Open-Meteo, et trace
la rose des vents correspondante.

## Prérequis

```bash
pip install requests pandas matplotlib windrose
```

Accès internet à `archive-api.open-meteo.com` (gratuit, sans clé).

## Utilisation

```bash
python vents_era5.py --lat 48.42 --lon -4.78
python vents_era5.py --lat 48.42 --lon -4.78 --debut 2010-01-01 --fin 2020-12-31 --sortie rose.png --csv vent.csv
```

Ou avec un fichier de configuration :

```bash
cp config_exemple.toml config.toml   # puis adapter config.toml
python vents_era5.py --config config.toml
```

Les options passées en ligne de commande sont prioritaires sur le fichier.

| Option | Clé TOML | Rôle | Défaut |
|---|---|---|---|
| `--lat`, `--lon` | `[point] lat`, `lon` | Point en WGS84 — obligatoire | — |
| `--debut`, `--fin` | `[periode] debut`, `fin` | Période (AAAA-MM-JJ) | 2000-01-01 → 2025-12-31 |
| `--fuseau` | `[periode] fuseau` | Fuseau horaire des dates | `Europe/Paris` |
| `--sortie` | `[sorties] image` | Image de la rose des vents (sinon affichée à l'écran) | affichage |
| `--csv` | `[sorties] csv` | Chronique horaire (`time ; wspd ; wdir`) | aucun |

## Sorties

- la rose des vents (fréquences par direction et classe de vitesse, en m/s) ;
- en option, la chronique horaire en CSV.

ERA5 a une résolution d'environ 30 km : la rose représente le vent régional,
pas les effets de site (relief, obstacles).
