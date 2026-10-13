"""
calcul_rasters.py

Calcul pixel à pixel entre deux rasters GeoTIFF A et B qui ne se
superposent que partiellement (différence de MNT, cumul, moyenne…).

Dans la zone de recouvrement (A et B valides), l'opération choisie est
appliquée. En dehors (un seul raster valide), le comportement est choisi
par l'utilisateur :
    nodata     → NoData
    conserver  → valeur du raster présent
    constante  → valeur fixe (--valeur)
    remplacer  → le raster absent est remplacé par --valeur, puis
                 l'opération est appliquée (ex. A - 0 = A, 0 - B = -B)

B est rééchantillonné à la volée sur la grille de A (résolution, alignement
et projection de A) ; le calcul se fait par blocs pour les gros rasters.

Usage:
    python calcul_rasters.py --a mnt_2025.tif --b mnt_2020.tif --output diff.tif
    python calcul_rasters.py --a a.tif --b b.tif --operation moyenne --hors-recouvrement conserver
    python calcul_rasters.py --a a.tif --b b.tif --expression "np.where(a > b, a, b)"

    Ou avec un fichier de configuration (les options en ligne de commande
    restent prioritaires sur le fichier) :
    python calcul_rasters.py --config config.toml
"""

import sys
import argparse
import logging
import time
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

gdal.UseExceptions()

NODATA_SORTIE = -9999.0

OPERATIONS = {
    'difference':     ('A - B',       lambda a, b: a - b),
    'addition':       ('A + B',       lambda a, b: a + b),
    'multiplication': ('A × B',       lambda a, b: a * b),
    'division':       ('A / B',       lambda a, b: np.divide(a, b, out=np.full_like(a, np.nan), where=b != 0)),
    'moyenne':        ('(A + B) / 2', lambda a, b: (a + b) / 2),
    'minimum':        ('min(A, B)',   np.minimum),
    'maximum':        ('max(A, B)',   np.maximum),
    'a':              ('A',           lambda a, b: a),
    'b':              ('B',           lambda a, b: b),
}

MODES_HORS_RECOUVREMENT = ['nodata', 'conserver', 'constante', 'remplacer']

# ============================================================================
# CONFIGURATION DU LOGGING
# ============================================================================

def setup_logging():
    """Configure le logging"""
    logger = logging.getLogger('calcul_rasters')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        file_handler = logging.FileHandler('calcul_rasters.log', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        try:
            console_handler.stream.reconfigure(encoding='utf-8')
        except Exception:
            pass
        logger.addHandler(console_handler)

    return logger

# ============================================================================
# GRILLE COMMUNE
# ============================================================================

def emprise(ds):
    """(xmin, ymin, xmax, ymax) d'un dataset GDAL"""
    gt = ds.GetGeoTransform()
    xmin, ymax = gt[0], gt[3]
    xmax = xmin + gt[1] * ds.RasterXSize
    ymin = ymax + gt[5] * ds.RasterYSize
    return xmin, ymin, xmax, ymax


def emprise_dans_srs(ds, srs_cible):
    """Emprise d'un dataset reprojetée dans srs_cible (coins transformés)"""
    xmin, ymin, xmax, ymax = emprise(ds)
    srs = osr.SpatialReference(wkt=ds.GetProjection()) if ds.GetProjection() else None
    if srs is None or srs_cible is None or srs.IsSame(srs_cible):
        return xmin, ymin, xmax, ymax
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs_cible.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(srs, srs_cible)
    pts = [tr.TransformPoint(x, y)[:2] for x in (xmin, xmax) for y in (ymin, ymax)]
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


def grille_commune(ds_a, ds_b, mode_emprise):
    """
    Grille de sortie : résolution et alignement de A, emprise selon
    mode_emprise (union, intersection, a, b), calée sur les pixels de A.

    Returns:
        (geotransform, ncols, nrows) ou None si l'emprise est vide
    """
    gt = ds_a.GetGeoTransform()
    if gt[2] != 0 or gt[4] != 0:
        raise ValueError("Les rasters avec rotation ne sont pas pris en charge")
    rx, ry = gt[1], -gt[5]

    srs_a = osr.SpatialReference(wkt=ds_a.GetProjection()) if ds_a.GetProjection() else None
    ea = emprise(ds_a)
    eb = emprise_dans_srs(ds_b, srs_a)

    if mode_emprise == 'union':
        e = (min(ea[0], eb[0]), min(ea[1], eb[1]), max(ea[2], eb[2]), max(ea[3], eb[3]))
    elif mode_emprise == 'intersection':
        e = (max(ea[0], eb[0]), max(ea[1], eb[1]), min(ea[2], eb[2]), min(ea[3], eb[3]))
    elif mode_emprise == 'a':
        e = ea
    else:
        e = eb

    if e[2] <= e[0] or e[3] <= e[1]:
        return None

    # Caler l'emprise sur la grille de A
    x0, y0 = gt[0], gt[3]
    xmin = x0 + np.floor((e[0] - x0) / rx + 1e-6) * rx
    xmax = x0 + np.ceil((e[2] - x0) / rx - 1e-6) * rx
    ymax = y0 - np.floor((y0 - e[3]) / ry + 1e-6) * ry
    ymin = y0 - np.ceil((y0 - e[1]) / ry - 1e-6) * ry

    ncols = int(round((xmax - xmin) / rx))
    nrows = int(round((ymax - ymin) / ry))
    return (xmin, rx, 0.0, ymax, 0.0, -ry), ncols, nrows


def vrt_sur_grille(chemin, geotransform, ncols, nrows, projection, resampling):
    """Raster virtuel (lazy) du fichier rééchantillonné sur la grille commune"""
    xmin, rx, _, ymax, _, mry = geotransform
    return gdal.Warp(
        '', chemin, format='VRT',
        outputBounds=(xmin, ymax + mry * nrows, xmin + rx * ncols, ymax),
        width=ncols, height=nrows,
        dstSRS=projection or None,
        resampleAlg=resampling,
        dstNodata=np.nan,
        outputType=gdal.GDT_Float64,
        multithread=True,
    )

# ============================================================================
# CALCUL
# ============================================================================

def lire_bloc(band, x, y, w, h, nodata_force=None):
    """Lit un bloc en float64 et renvoie (valeurs, masque_valide)"""
    arr = band.ReadAsArray(x, y, w, h).astype(np.float64)
    valide = np.isfinite(arr)
    nd = nodata_force if nodata_force is not None else band.GetNoDataValue()
    if nd is not None and not np.isnan(nd):
        valide &= arr != nd
    return arr, valide


def calculer(fonction, a, b, valide_a, valide_b, seul_a, seul_b, valeur):
    """
    Applique l'opération sur un bloc et gère les zones hors recouvrement.

    Returns:
        tableau float64 avec NaN pour NoData
    """
    sortie = np.full(a.shape, np.nan)

    # Zone de recouvrement
    m = valide_a & valide_b
    if m.any():
        sortie[m] = fonction(a[m], b[m])

    # A seul / B seul
    for masque, mode, present, absent_vers_valeur in (
        (valide_a & ~valide_b, seul_a, a, 'b'),
        (valide_b & ~valide_a, seul_b, b, 'a'),
    ):
        if not masque.any() or mode == 'nodata':
            continue
        if mode == 'conserver':
            sortie[masque] = present[masque]
        elif mode == 'constante':
            sortie[masque] = valeur
        elif mode == 'remplacer':
            rempl = np.full(int(masque.sum()), float(valeur))
            if absent_vers_valeur == 'b':
                sortie[masque] = fonction(a[masque], rempl)
            else:
                sortie[masque] = fonction(rempl, b[masque])

    return sortie


def fonction_expression(expression):
    """Construit une fonction (a, b) → résultat à partir d'une expression numpy"""
    code = compile(expression, '<expression>', 'eval')
    return lambda a, b: np.asarray(eval(code, {'np': np, '__builtins__': {}}, {'a': a, 'b': b}),
                                   dtype=np.float64) * np.ones_like(a)


def calcul_rasters(raster_a, raster_b, sortie, operation='difference', expression=None,
                   seul_a='nodata', seul_b='nodata', valeur=0.0, mode_emprise='union',
                   resampling='bilinear', nodata_a=None, nodata_b=None,
                   compression='DEFLATE', taille_bloc=2048):
    """
    Calcule A ∘ B pixel à pixel et écrit le résultat en GeoTIFF float32.

    Args:
        raster_a, raster_b: chemins des rasters d'entrée
        sortie: GeoTIFF de sortie
        operation: clé de OPERATIONS (ignorée si expression est donnée)
        expression: expression numpy en a et b, ex. "np.abs(a - b)"
        seul_a, seul_b: comportement là où seul A / seul B est valide
        valeur: valeur utilisée par les modes constante et remplacer
        mode_emprise: union, intersection, a ou b
        resampling: méthode de rééchantillonnage GDAL de B (et de A si besoin)
        nodata_a, nodata_b: NoData forcés si absents ou faux dans les fichiers
    """
    logger = logging.getLogger('calcul_rasters')
    logger.info("=" * 70)
    logger.info("🧮 CALCUL ENTRE DEUX RASTERS")
    logger.info("=" * 70)

    for chemin in (raster_a, raster_b):
        if not Path(chemin).exists():
            logger.error(f"❌ Fichier introuvable: {chemin}")
            return False

    if expression:
        fonction, libelle = fonction_expression(expression), expression
    else:
        libelle, fonction = OPERATIONS[operation]

    ds_a = gdal.Open(str(raster_a))
    ds_b = gdal.Open(str(raster_b))

    logger.info(f"📄 A : {raster_a} ({ds_a.RasterXSize}x{ds_a.RasterYSize} px, "
                f"rés. {ds_a.GetGeoTransform()[1]})")
    logger.info(f"📄 B : {raster_b} ({ds_b.RasterXSize}x{ds_b.RasterYSize} px, "
                f"rés. {ds_b.GetGeoTransform()[1]})")
    logger.info(f"🧮 Opération (zone de recouvrement) : {libelle}")
    logger.info(f"🔲 Seul A valide : {seul_a}   |   Seul B valide : {seul_b}"
                + (f"   (valeur = {valeur})" if 'constante' in (seul_a, seul_b)
                   or 'remplacer' in (seul_a, seul_b) else ''))
    logger.info(f"🗺️  Emprise de sortie : {mode_emprise}  |  Rééchantillonnage : {resampling}")

    srs_a, srs_b = ds_a.GetProjection(), ds_b.GetProjection()
    if srs_a and srs_b and not osr.SpatialReference(wkt=srs_a).IsSame(osr.SpatialReference(wkt=srs_b)):
        logger.warning("⚠️  Projections différentes : B est reprojeté dans la projection de A")
    elif not srs_a or not srs_b:
        logger.warning("⚠️  Projection absente sur au moins un raster : supposées identiques")

    grille = grille_commune(ds_a, ds_b, mode_emprise)
    if grille is None:
        logger.error("❌ Les deux rasters ne se superposent pas (emprise vide)")
        return False
    gt, ncols, nrows = grille
    logger.info(f"📐 Grille de sortie : {ncols}x{nrows} px, origine ({gt[0]:.3f}, {gt[3]:.3f})")

    # Forcer les NoData lus dans les fichiers si demandé, avant la mise en VRT
    entrees = []
    for chemin, ds, nd in ((raster_a, ds_a, nodata_a), (raster_b, ds_b, nodata_b)):
        if nd is not None:
            ds = gdal.Translate('', ds, format='VRT', noData=nd)
        entrees.append(ds)
    vrt_a = vrt_sur_grille(entrees[0], gt, ncols, nrows, srs_a, resampling)
    vrt_b = vrt_sur_grille(entrees[1], gt, ncols, nrows, srs_a, resampling)
    band_a, band_b = vrt_a.GetRasterBand(1), vrt_b.GetRasterBand(1)

    Path(sortie).parent.mkdir(parents=True, exist_ok=True)
    options = ['TILED=YES', 'BLOCKXSIZE=512', 'BLOCKYSIZE=512', 'BIGTIFF=IF_SAFER']
    if compression != 'NONE':
        options += [f'COMPRESS={compression}', 'PREDICTOR=3']
    ds_out = gdal.GetDriverByName('GTiff').Create(str(sortie), ncols, nrows, 1,
                                                   gdal.GDT_Float32, options)
    ds_out.SetGeoTransform(gt)
    if srs_a:
        ds_out.SetProjection(srs_a)
    band_out = ds_out.GetRasterBand(1)
    band_out.SetNoDataValue(NODATA_SORTIE)

    stats = {'recouvrement': 0, 'seul_a': 0, 'seul_b': 0}
    start = time.time()
    nb_blocs_y = (nrows + taille_bloc - 1) // taille_bloc
    with np.errstate(all='ignore'):
        for iy, y in enumerate(range(0, nrows, taille_bloc), 1):
            h = min(taille_bloc, nrows - y)
            for x in range(0, ncols, taille_bloc):
                w = min(taille_bloc, ncols - x)
                a, va = lire_bloc(band_a, x, y, w, h)
                b, vb = lire_bloc(band_b, x, y, w, h)
                res = calculer(fonction, a, b, va, vb, seul_a, seul_b, valeur)
                stats['recouvrement'] += int((va & vb).sum())
                stats['seul_a'] += int((va & ~vb).sum())
                stats['seul_b'] += int((vb & ~va).sum())
                res[~np.isfinite(res)] = NODATA_SORTIE
                band_out.WriteArray(res.astype(np.float32), x, y)
            if nb_blocs_y > 1:
                logger.info(f"   Bande {iy}/{nb_blocs_y} traitée")

    band_out.FlushCache()
    band_out.ComputeStatistics(False)
    mn, mx, moy, ect = band_out.GetStatistics(False, False)
    ds_out = None

    rx = gt[1]
    logger.info("")
    logger.info("=" * 70)
    logger.info("📊 RÉSUMÉ")
    logger.info("=" * 70)
    for cle, libelle_zone in (('recouvrement', 'Recouvrement A∩B'),
                              ('seul_a', 'A seul'), ('seul_b', 'B seul')):
        n = stats[cle]
        logger.info(f"   {libelle_zone:<17}: {n:>12} px  ({n * rx * rx / 1e4:.2f} ha)")
    logger.info(f"   Résultat : min {mn:.3f}  max {mx:.3f}  moyenne {moy:.3f}  écart-type {ect:.3f}")
    logger.info(f"✅ Raster écrit : {Path(sortie).resolve()} en {time.time() - start:.1f}s")
    logger.info("=" * 70)
    return True

# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(
        description='Calcul pixel à pixel entre deux rasters GeoTIFF partiellement superposés',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  # Différence A - B, NoData hors recouvrement
  python calcul_rasters.py --a mnt_2025.tif --b mnt_2020.tif --output diff.tif

  # Moyenne, en conservant le raster présent hors recouvrement
  python calcul_rasters.py --a a.tif --b b.tif -o moy.tif --operation moyenne --hors-recouvrement conserver

  # Différence, 0 là où un seul raster existe
  python calcul_rasters.py --a a.tif --b b.tif -o diff.tif --hors-recouvrement constante --valeur 0

  # Conserver A seul, NoData là où seul B existe
  python calcul_rasters.py --a a.tif --b b.tif -o diff.tif --seul-a conserver --seul-b nodata

  # Expression libre (numpy, variables a et b)
  python calcul_rasters.py --a a.tif --b b.tif -o r.tif --expression "np.abs(a - b)"

  # Avec un fichier de configuration
  python calcul_rasters.py --config config.toml
        """
    )

    parser.add_argument('--config',
                        help='Fichier de configuration TOML (voir config_exemple.toml) ; '
                             'les options en ligne de commande restent prioritaires')
    parser.add_argument('--a', dest='raster_a', help='Raster A (sa grille sert de référence)')
    parser.add_argument('--b', dest='raster_b', help='Raster B')
    parser.add_argument('--output', '-o', default='./resultat.tif',
                        help='Raster de sortie (défaut: ./resultat.tif)')
    parser.add_argument('--operation', choices=list(OPERATIONS), default='difference',
                        help='Opération dans la zone de recouvrement (défaut: difference = A - B)')
    parser.add_argument('--expression',
                        help='Expression numpy en a et b, remplace --operation (ex: "np.abs(a - b)")')
    parser.add_argument('--hors-recouvrement', dest='hors_recouvrement',
                        choices=MODES_HORS_RECOUVREMENT, default='nodata',
                        help='Comportement là où un seul raster est valide (défaut: nodata)')
    parser.add_argument('--seul-a', dest='seul_a', choices=MODES_HORS_RECOUVREMENT,
                        help='Comportement là où seul A est valide (remplace --hors-recouvrement)')
    parser.add_argument('--seul-b', dest='seul_b', choices=MODES_HORS_RECOUVREMENT,
                        help='Comportement là où seul B est valide (remplace --hors-recouvrement)')
    parser.add_argument('--valeur', type=float, default=0.0,
                        help='Valeur des modes constante / remplacer (défaut: 0)')
    parser.add_argument('--emprise', choices=['union', 'intersection', 'a', 'b'], default='union',
                        help='Emprise du raster de sortie (défaut: union)')
    parser.add_argument('--resampling', choices=['near', 'bilinear', 'cubic', 'average'],
                        default='bilinear',
                        help='Rééchantillonnage de B sur la grille de A (défaut: bilinear)')
    parser.add_argument('--nodata-a', dest='nodata_a', type=float,
                        help='Forcer la valeur NoData de A')
    parser.add_argument('--nodata-b', dest='nodata_b', type=float,
                        help='Forcer la valeur NoData de B')
    parser.add_argument('--compress', '-c', choices=['DEFLATE', 'LZW', 'NONE'], default='DEFLATE',
                        help='Compression du GeoTIFF de sortie (défaut: DEFLATE)')
    parser.add_argument('--taille-bloc', dest='taille_bloc', type=int, default=2048,
                        help='Taille des blocs de calcul en pixels (défaut: 2048)')

    # Les valeurs du fichier de configuration remplacent les défauts,
    # les options passées en ligne de commande restent prioritaires
    args_config, _ = parser.parse_known_args()
    if args_config.config:
        if tomllib is None:
            parser.error("--config nécessite Python 3.11+ ou le paquet tomli (pip install tomli)")
        with open(args_config.config, 'rb') as f:
            cfg = tomllib.load(f)
        chemins = cfg.get('chemins', {})
        calcul = cfg.get('calcul', {})
        hors = cfg.get('hors_recouvrement', {})
        defauts = {
            'raster_a': chemins.get('raster_a'),
            'raster_b': chemins.get('raster_b'),
            'output': chemins.get('sortie'),
            'operation': calcul.get('operation'),
            'expression': calcul.get('expression') or None,
            'emprise': calcul.get('emprise'),
            'resampling': calcul.get('resampling'),
            'nodata_a': calcul.get('nodata_a'),
            'nodata_b': calcul.get('nodata_b'),
            'hors_recouvrement': hors.get('mode'),
            'seul_a': hors.get('seul_a'),
            'seul_b': hors.get('seul_b'),
            'valeur': hors.get('valeur'),
        }
        parser.set_defaults(**{k: v for k, v in defauts.items() if v is not None})
    args = parser.parse_args()

    if not args.raster_a or not args.raster_b:
        parser.error("les rasters A et B sont obligatoires (--a / --b ou fichier de configuration)")
    for nom in ('hors_recouvrement', 'seul_a', 'seul_b'):
        v = getattr(args, nom)
        if v is not None and v not in MODES_HORS_RECOUVREMENT:
            parser.error(f"{nom} = '{v}' invalide, valeurs possibles : {MODES_HORS_RECOUVREMENT}")

    logger = setup_logging()
    logger.info("=== CALCUL ENTRE RASTERS ===")
    logger.info("")

    try:
        success = calcul_rasters(
            args.raster_a, args.raster_b, args.output,
            operation=args.operation,
            expression=args.expression,
            seul_a=args.seul_a or args.hors_recouvrement,
            seul_b=args.seul_b or args.hors_recouvrement,
            valeur=args.valeur,
            mode_emprise=args.emprise,
            resampling=args.resampling,
            nodata_a=args.nodata_a,
            nodata_b=args.nodata_b,
            compression=args.compress,
            taille_bloc=args.taille_bloc,
        )
        sys.exit(0 if success else 1)

    except Exception as e:
        logger.error(f"💥 Erreur fatale: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
