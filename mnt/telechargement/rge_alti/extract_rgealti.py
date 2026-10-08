"""
Extraction sélective des dalles RGE ALTI 1m depuis une archive .7z locale,
selon une emprise rectangulaire en Lambert 93.

Usage :
    python extract_rgealti.py \
        --archive ./RGEALTI_2-0_1M_ASC_LAMB93-IGN69_D035_2022-12-16.7z \
        --xmin 1200000 --ymin 6800000 --xmax 1210000 --ymax 6810000 \
        --outdir ./rgealti_output

Dépendances :
    pip install py7zr
"""

import argparse
import math
import os
import sys


DALLE_SIZE_M = 1000  # Chaque dalle couvre 1 km x 1 km


def compute_target_names(xmin, ymin, xmax, ymax, all_names):
    """
    Calcule les dalles qui intersectent l'emprise en se basant sur
    les coordonnées km encodées dans les noms de fichiers présents
    dans l'archive (format IGN : ..._XXXX_YYYY.asc).
    """
    x0 = math.floor(xmin / DALLE_SIZE_M) * DALLE_SIZE_M
    y0 = math.floor(ymin / DALLE_SIZE_M) * DALLE_SIZE_M

    # Coordonnées km des coins NW des dalles dans l'emprise
    coords_attendues = set()
    x = x0
    while x < xmax:
        y = y0
        while y < ymax:
            nw_x_km = x // 1000
            nw_y_km = (y + DALLE_SIZE_M) // 1000
            coords_attendues.add((nw_x_km, nw_y_km))
            y += DALLE_SIZE_M
        x += DALLE_SIZE_M

    # Correspondance avec les fichiers réels dans l'archive
    to_extract = []
    for path in all_names:
        if not path.endswith(".asc"):
            continue
        basename = os.path.basename(path)
        parts = basename.replace(".asc", "").split("_")
        # Format IGN : RGEALTI_FXX_0315_6775_MNT_LAMB93_IGN69.asc -> X=parts[2], Y=parts[3]
        try:
            x_km = int(parts[2])
            y_km = int(parts[3])
            if (x_km, y_km) in coords_attendues:
                to_extract.append(path)
        except (ValueError, IndexError):
            continue

    return to_extract


def extract(archive_path, xmin, ymin, xmax, ymax, outdir):
    try:
        import py7zr
    except ImportError:
        print("[ERREUR] Module py7zr manquant : pip install py7zr")
        sys.exit(1)

    os.makedirs(outdir, exist_ok=True)

    print(f"\n[INFO] Ouverture de l'archive : {archive_path}")
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        all_names = archive.getnames()

        # Aperçu du format réel des noms dans l'archive
        exemples = [n for n in all_names if n.endswith(".asc")][:3]
        if exemples:
            print(f"[INFO] Exemples de noms dans l'archive :")
            for e in exemples:
                print(f"       {e}")
        else:
            print("[ATTENTION] Aucun fichier .asc trouvé dans l'archive.")
            sys.exit(1)

        to_extract = compute_target_names(xmin, ymin, xmax, ymax, all_names)

        if not to_extract:
            print("\n[ATTENTION] Aucune dalle correspondante trouvée pour l'emprise donnée.")
            print("  Vérifiez que l'emprise est bien couverte par cette archive.")
            return []

        print(f"\n[INFO] {len(to_extract)} dalle(s) à extraire :")
        for n in sorted(to_extract):
            print(f"       {os.path.basename(n)}")

        archive.extract(targets=to_extract, path=outdir)

    # Aplatir les sous-dossiers éventuels vers outdir
    for root, dirs, files in os.walk(outdir):
        for fname in files:
            if fname.endswith(".asc"):
                src = os.path.join(root, fname)
                dst = os.path.join(outdir, fname)
                if src != dst:
                    os.replace(src, dst)

    extracted = [os.path.basename(n) for n in to_extract]
    print(f"\n[OK] {len(extracted)} dalle(s) extraite(s) vers : {outdir}")
    return extracted


def main():
    parser = argparse.ArgumentParser(
        description="Extraction sélective de dalles RGE ALTI 1m depuis une archive .7z locale."
    )
    parser.add_argument("--archive", required=True,
                        help="Chemin vers l'archive .7z")
    parser.add_argument("--xmin", type=float, required=True,
                        help="X min emprise (Lambert 93, mètres)")
    parser.add_argument("--ymin", type=float, required=True,
                        help="Y min emprise (Lambert 93, mètres)")
    parser.add_argument("--xmax", type=float, required=True,
                        help="X max emprise (Lambert 93, mètres)")
    parser.add_argument("--ymax", type=float, required=True,
                        help="Y max emprise (Lambert 93, mètres)")
    parser.add_argument("--outdir", default="./rgealti_output",
                        help="Dossier de sortie (défaut : ./rgealti_output)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Extraction RGE ALTI 1m — archive locale")
    print("=" * 60)
    print(f"  Archive  : {args.archive}")
    print(f"  Emprise  : X [{args.xmin:.0f} ; {args.xmax:.0f}]")
    print(f"             Y [{args.ymin:.0f} ; {args.ymax:.0f}]")
    print(f"  Sortie   : {args.outdir}")
    print("=" * 60)

    extracted = extract(args.archive, args.xmin, args.ymin, args.xmax, args.ymax, args.outdir)

    print("\n" + "=" * 60)
    if extracted:
        print(f"  {len(extracted)} dalle(s) extraite(s) avec succès.")
    else:
        print("  Aucune dalle extraite.")
    print("=" * 60)


if __name__ == "__main__":
    main()
