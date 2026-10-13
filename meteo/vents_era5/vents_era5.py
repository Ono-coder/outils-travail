"""
vents_era5.py

Rose des vents en un point à partir de la réanalyse ERA5 (API d'archive
Open-Meteo) : vitesse et direction du vent horaire à 10 m.

Usage :
    python vents_era5.py --lat 48.42 --lon -4.78
    python vents_era5.py --lat 48.42 --lon -4.78 --debut 2010-01-01 --fin 2020-12-31 --sortie rose.png
    python vents_era5.py --config config.toml

Les options en ligne de commande restent prioritaires sur le fichier.
"""

import sys
import argparse

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import requests
import pandas as pd
from windrose import WindroseAxes
import matplotlib.pyplot as plt

url = "https://archive-api.open-meteo.com/v1/archive"


def lire_arguments():
    parser = argparse.ArgumentParser(description="Rose des vents ERA5 (Open-Meteo) en un point")
    parser.add_argument("--config", help="Fichier de configuration TOML (voir config_exemple.toml)")
    parser.add_argument("--lat", type=float, help="Latitude WGS84 du point")
    parser.add_argument("--lon", type=float, help="Longitude WGS84 du point")
    parser.add_argument("--debut", default="2000-01-01", help="Date de début AAAA-MM-JJ (défaut : 2000-01-01)")
    parser.add_argument("--fin", default="2025-12-31", help="Date de fin AAAA-MM-JJ (défaut : 2025-12-31)")
    parser.add_argument("--fuseau", default="Europe/Paris", help="Fuseau horaire (défaut : Europe/Paris)")
    parser.add_argument("--csv", help="Enregistre aussi la chronique horaire dans ce CSV")
    parser.add_argument("--sortie", help="Enregistre la rose des vents dans cette image au lieu de l'afficher")

    # Les valeurs du fichier de configuration remplacent les défauts,
    # les options passées en ligne de commande restent prioritaires
    args_config, _ = parser.parse_known_args()
    if args_config.config:
        with open(args_config.config, "rb") as f:
            cfg = tomllib.load(f)
        point = cfg.get("point", {})
        periode = cfg.get("periode", {})
        sorties = cfg.get("sorties", {})
        defauts = {"lat": point.get("lat"), "lon": point.get("lon"),
                   "debut": periode.get("debut"), "fin": periode.get("fin"),
                   "fuseau": periode.get("fuseau"),
                   "csv": sorties.get("csv"), "sortie": sorties.get("image")}
        parser.set_defaults(**{k: v for k, v in defauts.items() if v is not None})
    args = parser.parse_args()
    if args.lat is None or args.lon is None:
        parser.error("préciser le point : --lat et --lon (ou [point] lat / lon dans la configuration)")
    return args


def main():
    args = lire_arguments()
    lat, lon = args.lat, args.lon

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": args.debut,
        "end_date": args.fin,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",      # m/s ; retire cette ligne pour des km/h
        "timezone": args.fuseau,
    }

    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    h = r.json()["hourly"]

    df = pd.DataFrame({
        "time": pd.to_datetime(h["time"]),
        "wspd": h["wind_speed_10m"],
        "wdir": h["wind_direction_10m"],
    }).dropna()

    if args.csv:
        df.to_csv(args.csv, sep=";", index=False)
        print(f"Chronique enregistrée : {args.csv} ({len(df)} heures)")

    ax = WindroseAxes.from_ax()
    ax.bar(df["wdir"], df["wspd"], normed=True, opening=0.8, edgecolor="white")
    ax.set_legend(title="Vitesse (m/s)",
                  loc="upper left",
                  bbox_to_anchor=(1.05, 1.0))
    plt.title(f"Rose des vents — {lat:.3f}, {lon:.3f} (ERA5, {args.debut[:4]}–{args.fin[:4]})")
    if args.sortie:
        plt.savefig(args.sortie, dpi=150, bbox_inches="tight")
        print(f"Rose des vents enregistrée : {args.sortie}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
