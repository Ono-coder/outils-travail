"""
interpolation_lacunes.py
=======================
Interpolation des pixels NoData dans un raster GeoTIFF, par tuiles.
Gère les rasters de grande taille (>10 GB) sans saturer la RAM.

Produit en sortie :
  - Le raster interpolé (GeoTIFF float32 compressé)
  - Une carte PNG des pixels modifiés (blanc=valide, rouge=interpolé, noir=NoData restant)

Méthodes :
  inpaint_telea  Inpainting TELEA OpenCV  – rapide, petites lacunes
  inpaint_ns     Inpainting Navier-Stokes – grandes lacunes continues
  nearest        Plus proche voisin scipy
  bilinear       Bilinéaire scipy
  cubic          Bicubique scipy – recommandé MNT/bathymétrie

Utilisation :
  python interpolation_lacunes.py input.tif output.tif --method inpaint_telea
  python interpolation_lacunes.py dem.tif dem_fill.tif --method cubic --tile_size 4096 --overlap 64
"""

import argparse
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# INTERPOLATION D'UNE TUILE
# ─────────────────────────────────────────────────────────────────────────────

def inpaint_tile(data: np.ndarray, mask: np.ndarray, method: str) -> np.ndarray:
    """Inpainting OpenCV sur une tuile normalisée en uint16."""
    import cv2

    if mask.sum() == 0:
        return data

    valid = data[~mask]
    if len(valid) == 0:
        return data

    vmin, vmax = valid.min(), valid.max()
    data_range = vmax - vmin if vmax != vmin else 1.0

    norm = ((data - vmin) / data_range * 65535).clip(0, 65535).astype(np.uint16)
    norm[mask] = 0

    inpaint_mask = mask.astype(np.uint8) * 255
    flag = cv2.INPAINT_TELEA if method == "inpaint_telea" else cv2.INPAINT_NS

    result_norm = cv2.inpaint(norm, inpaint_mask, 5, flag)

    result = result_norm.astype(np.float64) / 65535.0 * data_range + vmin
    result[~mask] = data[~mask]
    return result


def scipy_tile(data: np.ndarray, mask: np.ndarray, method: str) -> np.ndarray:
    """griddata scipy sur une tuile."""
    from scipy.interpolate import griddata

    if mask.sum() == 0:
        return data

    rows, cols = np.indices(data.shape)
    valid = ~mask
    points = np.column_stack([rows[valid].ravel(), cols[valid].ravel()])
    values = data[valid].ravel()
    xi = np.column_stack([rows[mask].ravel(), cols[mask].ravel()])

    if len(points) == 0:
        return data

    interpolated = griddata(points, values, xi, method=method, fill_value=np.nan)

    if np.isnan(interpolated).any():
        interpolated_nn = griddata(points, values, xi, method='nearest')
        nan_idx = np.isnan(interpolated)
        interpolated[nan_idx] = interpolated_nn[nan_idx]

    result = data.copy()
    result[mask] = interpolated
    return result


def interpolate_tile(data: np.ndarray, mask: np.ndarray, method: str) -> np.ndarray:
    if method in ("inpaint_telea", "inpaint_ns"):
        return inpaint_tile(data, mask, method)
    scipy_method = {"nearest": "nearest", "bilinear": "linear", "cubic": "cubic"}[method]
    return scipy_tile(data, mask, scipy_method)


# ─────────────────────────────────────────────────────────────────────────────
# GÉNÉRATEUR DE FENÊTRES
# ─────────────────────────────────────────────────────────────────────────────

def iter_windows(width, height, tile_size, overlap):
    for row_off in range(0, height, tile_size):
        for col_off in range(0, width, tile_size):
            read_col = max(0, col_off - overlap)
            read_row = max(0, row_off - overlap)
            read_w   = min(tile_size + 2 * overlap, width  - read_col)
            read_h   = min(tile_size + 2 * overlap, height - read_row)
            read_win  = Window(read_col, read_row, read_w, read_h)

            write_w   = min(tile_size, width  - col_off)
            write_h   = min(tile_size, height - row_off)
            write_win = Window(col_off, row_off, write_w, write_h)

            inner_col = col_off - read_col
            inner_row = row_off - read_row

            yield read_win, write_win, inner_col, inner_row


# ─────────────────────────────────────────────────────────────────────────────
# CARTE PNG DES MODIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

def build_png_map(
    nodata_mask_path: str,
    output_png: str,
    width: int,
    height: int,
    tile_size: int,
    max_png_dim: int = 8000,
):
    """
    Lit le raster de masque (uint8 : 0=valide, 1=interpolé, 2=NoData restant)
    et génère un PNG colorié :
      Blanc [255,255,255] → pixel valide d'origine (non modifié)
      Rouge [220,  50, 50] → pixel interpolé (était NoData)
      Noir  [  0,   0,  0] → pixel toujours NoData (lacune trop grande)
    Avec downscale automatique pour les grands rasters.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ImportError("Pillow requis : pip install Pillow")

    # Downscale automatique
    factor = max(1, max(width, height) // max_png_dim)
    w_out = width  // factor
    h_out = height // factor

    print(f"  Dimensions PNG : {h_out} x {w_out} px  (1/{factor} du raster)")

    # Tableau de couleurs RGB
    rgb = np.empty((h_out, w_out, 3), dtype=np.uint8)

    with rasterio.open(nodata_mask_path) as src:
        for row_out in range(h_out):
            row_in = row_out * factor
            win = Window(0, row_in, width, factor)
            stripe = src.read(1, window=win)  # shape: (factor, width)

            # Sous-échantillonnage par bloc : valeur modale sur le bloc
            # On prend la valeur centrale du bloc pour simplifier
            col_indices = np.arange(w_out) * factor
            col_indices = np.clip(col_indices, 0, width - 1)
            row_mid = min(factor // 2, stripe.shape[0] - 1)
            strip_row = stripe[row_mid, col_indices]

            # Colorisation
            r = np.where(strip_row == 0, 255,
                np.where(strip_row == 1, 220, 0)).astype(np.uint8)
            g = np.where(strip_row == 0, 255,
                np.where(strip_row == 1,  50, 0)).astype(np.uint8)
            b = np.where(strip_row == 0, 255,
                np.where(strip_row == 1,  50, 0)).astype(np.uint8)

            rgb[row_out, :, 0] = r
            rgb[row_out, :, 1] = g
            rgb[row_out, :, 2] = b

    img = Image.fromarray(rgb, mode="RGB")

    # Légende
    legend_h = max(60, h_out // 20)
    legend_img = Image.new("RGB", (w_out, h_out + legend_h), (240, 240, 240))
    legend_img.paste(img, (0, 0))

    draw = ImageDraw.Draw(legend_img)
    sq = legend_h // 3
    y0 = h_out + legend_h // 2 - sq // 2

    items = [
        ((255, 255, 255), "Valide (non modifié)"),
        ((220,  50,  50), "Interpolé (était NoData)"),
        ((  0,   0,   0), "NoData restant"),
    ]
    x = sq
    for color, label in items:
        draw.rectangle([x, y0, x + sq, y0 + sq], fill=color, outline=(100, 100, 100))
        draw.text((x + sq + 6, y0), label, fill=(30, 30, 30))
        x += sq + len(label) * 7 + 20  # espacement approx.

    legend_img.save(output_png, dpi=(150, 150))
    print(f"  Carte PNG sauvegardée : {output_png}")


# ─────────────────────────────────────────────────────────────────────────────
# TRAITEMENT PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def interpolate_raster(
    input_path: str,
    output_path: str,
    method: str = "inpaint_telea",
    band: int = 1,
    tile_size: int = 2048,
    overlap: int = 64,
    map_png: str = "",
    max_png_dim: int = 8000,
):
    METHODS = ("nearest", "bilinear", "cubic", "inpaint_telea", "inpaint_ns")
    if method not in METHODS:
        raise ValueError(f"Méthode '{method}' inconnue. Choix : {METHODS}")

    print(f"\n{'─'*57}")
    print(f"  Raster NoData Interpolation  (mode tuilé)")
    print(f"  Entrée  : {input_path}")
    print(f"  Sortie  : {output_path}")
    print(f"  Méthode : {method} | Bande : {band}")
    print(f"  Tuile   : {tile_size}x{tile_size} px | Overlap : {overlap} px")
    print(f"{'─'*57}")

    t0 = time.time()

    # Chemin du raster masque temporaire (uint8)
    mask_path = str(Path(output_path).with_suffix("")) + "_nodata_mask.tif"
    gen_map   = bool(map_png)

    with rasterio.open(input_path) as src:
        profile  = src.profile.copy()
        width    = src.width
        height   = src.height
        nodata   = src.nodata if src.nodata is not None else -9999.0

        print(f"\n[1/4] Raster : {height} x {width} px  |  NoData : {nodata}")

        # Profils construits from scratch — BigTIFF forcé YES (évite limite 4 GB)
        estimated_gb = width * height * 4 / 1e9
        print(f"  Taille estimée sortie : {estimated_gb:.1f} GB -> BigTIFF force")

        out_profile = {
            "driver":     "GTiff",
            "dtype":      rasterio.float32,
            "width":      width,
            "height":     height,
            "count":      1,
            "crs":        src.crs,
            "transform":  src.transform,
            "nodata":     None,
            "compress":   "deflate",
            "tiled":      True,
            "blockxsize": 512,
            "blockysize": 512,
            "predictor":  2,
            "BIGTIFF":    "YES",
        }

        mask_profile = {
            "driver":     "GTiff",
            "dtype":      rasterio.uint8,
            "width":      width,
            "height":     height,
            "count":      1,
            "crs":        src.crs,
            "transform":  src.transform,
            "nodata":     None,
            "compress":   "lzw",
            "tiled":      True,
            "blockxsize": 512,
            "blockysize": 512,
            "BIGTIFF":    "YES",
        }

        n_tiles_col = (width  + tile_size - 1) // tile_size
        n_tiles_row = (height + tile_size - 1) // tile_size
        n_total = n_tiles_col * n_tiles_row
        print(f"  Tuiles  : {n_tiles_row} x {n_tiles_col} = {n_total}")

        print(f"\n[2/4] Interpolation + construction du masque...")

        total_missing = 0
        total_filled  = 0
        tile_idx      = 0

        with rasterio.open(output_path, "w", **out_profile) as dst, \
             rasterio.open(mask_path,   "w", **mask_profile) as dst_mask:

            for read_win, write_win, inner_col, inner_row in iter_windows(
                width, height, tile_size, overlap
            ):
                tile_idx += 1

                # Lecture
                tile = src.read(band, window=read_win).astype(np.float64)
                mask_nodata = (tile == nodata) | (~np.isfinite(tile))

                # Zone utile (sans overlap)
                wh, ww = write_win.height, write_win.width
                core_mask = mask_nodata[inner_row:inner_row+wh, inner_col:inner_col+ww]
                n_missing = int(core_mask.sum())
                total_missing += n_missing

                # Interpolation
                if mask_nodata.sum() > 0:
                    tile_filled = interpolate_tile(tile, mask_nodata, method)
                else:
                    tile_filled = tile

                # Extraction zone utile
                out_data = tile_filled[inner_row:inner_row+wh, inner_col:inner_col+ww]

                # Calcul masque de sortie :
                # 0 = valide d'origine (core_mask False)
                # 1 = interpolé avec succès (core_mask True ET valeur finie)
                # 2 = toujours NoData (core_mask True ET valeur non finie)
                out_mask = np.zeros((wh, ww), dtype=np.uint8)
                still_nodata = core_mask & (~np.isfinite(out_data))
                filled_ok    = core_mask & np.isfinite(out_data)
                out_mask[filled_ok]    = 1
                out_mask[still_nodata] = 2

                n_filled = int(filled_ok.sum())
                total_filled += n_filled

                # Écriture
                dst.write(out_data.astype(np.float32), 1, window=write_win)
                if gen_map:
                    dst_mask.write(out_mask, 1, window=write_win)

                # Progression toutes les ~5%
                if tile_idx % max(1, n_total // 20) == 0 or tile_idx == n_total:
                    elapsed = time.time() - t0
                    pct = tile_idx / n_total * 100
                    eta = elapsed / tile_idx * (n_total - tile_idx) if tile_idx < n_total else 0
                    print(
                        f"  [{pct:5.1f}%] tuile {tile_idx:4d}/{n_total}"
                        f"  | écoule {elapsed:5.0f}s | ETA {eta:5.0f}s"
                    )

    print(f"\n[3/4] Résumé interpolation")
    print(f"  Pixels NoData en entrée : {total_missing:,}")
    print(f"  Pixels interpolés       : {total_filled:,}"
          f"  ({total_filled/max(total_missing,1)*100:.1f}%)")
    print(f"  Pixels NoData restants  : {total_missing - total_filled:,}")
    print(f"  Raster de sortie        : {output_path}")

    # Génération carte PNG
    if gen_map:
        print(f"\n[4/4] Génération carte PNG...")
        build_png_map(mask_path, map_png, width, height, tile_size, max_png_dim)

        # Nettoyage masque temporaire
        Path(mask_path).unlink(missing_ok=True)
    else:
        print(f"\n[4/4] (carte PNG non demandée — ajoutez --map_png carte.png)")

    print(f"\n  ✓ Terminé en {time.time()-t0:.1f}s")
    print(f"{'─'*57}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Interpolation tuilée des pixels NoData + carte PNG des modifications.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python interpolation_lacunes.py LIDARHD.tif LIDARHD_fill.tif \\
      --method inpaint_telea --map_png modifications.png

  python interpolation_lacunes.py bathy.tif bathy_fill.tif \\
      --method cubic --tile_size 4096 --map_png carte.png --max_png_dim 6000

Tile size selon RAM :
  8 GB  → tile_size=1024  overlap=32
  16 GB → tile_size=2048  overlap=64   (défaut)
  32 GB → tile_size=4096  overlap=128
        """
    )
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--method",      default="inpaint_telea",
                        choices=["nearest","bilinear","cubic","inpaint_telea","inpaint_ns"])
    parser.add_argument("--band",        type=int, default=1)
    parser.add_argument("--tile_size",   type=int, default=2048)
    parser.add_argument("--overlap",     type=int, default=64)
    parser.add_argument("--map_png",     default="",
                        help="Chemin du PNG de carte des modifications (ex: modifications.png)")
    parser.add_argument("--max_png_dim", type=int, default=8000,
                        help="Dimension max du PNG (défaut: 8000 px, downscale auto)")

    args = parser.parse_args()

    try:
        interpolate_raster(
            args.input, args.output,
            method=args.method,
            band=args.band,
            tile_size=args.tile_size,
            overlap=args.overlap,
            map_png=args.map_png,
            max_png_dim=args.max_png_dim,
        )
    except Exception as e:
        print(f"\n  [ERREUR] {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()