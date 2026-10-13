"""
Conversion XYZ -> GeoTIFF (moyenne des Z par maille) sans PDAL, avec avancement réel.

Chaque fichier est découpé en portions d'octets lues en parallèle par NB_WORKERS
processus. Chaque processus accumule somme et nombre de points par maille :
  - en tuiles denses TUILE x TUILE là où les points sont nombreux ;
  - en liste creuse (maille, somme, nombre) pour les points épars, ce qui évite
    d'allouer une tuile entière pour chaque point aberrant.
Les résultats partiels sont ensuite fusionnés. L'avancement est suivi en octets lus.

Lignes corrompues :
  - illisibles (texte, nombres collés, champs manquants ou en trop) -> ignorées, seules ;
  - lisibles mais aberrantes (ex. X tronqué qui tombe à des km du levé) -> les groupes
    de points isolés et clairsemés sont écartés avant l'écriture du raster.
Des exemples de lignes rejetées sont affichés à la fin et écrits dans <nom>_rejets.txt.

v2 (corrections) :
  - un guillemet " ou ' dans une ligne faisait avaler à pandas toutes les lignes suivantes
    du bloc (jusqu'à 64 Mo perdus) : guillemets neutralisés (quoting=QUOTE_NONE) ;
  - virgule décimale (« 330000,12 6780000,34 60,5 ») : décidée ligne par ligne ; avant,
    le bloc était soit rejeté entier, soit lu de travers (Y = 12…) puis écarté ;
  - un morceau de bloc illisible par pandas est relu ligne à ligne au lieu d'être jeté ;
  - un groupe détaché mais dense (zone levée à part) n'est plus écarté ; les groupes
    écartés sont listés avec leur emprise ;
  - bilan par portion du fichier (points lus, rejets) pour repérer une zone perdue.

Dépendances : numpy, pandas, rasterio

Usage :
    python xyz2tif.py --config config.toml
    python xyz2tif.py --dossier ./xyz --resolution 1 --epsg EPSG:2154 --workers 4
Les options en ligne de commande restent prioritaires sur le fichier.
"""
import io
import os
import csv
import re
import sys
import time
import shutil
import logging
from pathlib import Path
from multiprocessing import Manager
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

import importlib.util

# rasterio installé par pip embarque sa propre version de PROJ et ses données (proj.db).
# Si l'environnement conda définit PROJ_LIB / PROJ_DATA vers un proj.db plus ancien,
# les codes EPSG deviennent introuvables. On laisse alors rasterio utiliser ses données.
_spec = importlib.util.find_spec("rasterio")
if _spec and _spec.origin:
    _proj_rasterio = os.path.join(os.path.dirname(_spec.origin), "proj_data")
    if os.path.isfile(os.path.join(_proj_rasterio, "proj.db")):
        os.environ["PROJ_DATA"] = _proj_rasterio
        os.environ["PROJ_LIB"] = _proj_rasterio

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from rasterio.fill import fillnodata

# ─── Configuration ────────────────────────────────────────────────────────────
DOSSIER_A_TRAITER    = None     # dossier des .xyz : [chemins] dossier ou --dossier
RESOLUTION_RASTER    = 1.0
CODE_EPSG            = "EPSG:2154"
NB_WORKERS           = 4
ECRASER_EXISTANTS    = True     # le .tif existant est remplacé
SEPARATEUR           = r"\s+"   # espaces et tabulations ; virgules et points-virgules convertis automatiquement
DISTANCE_REMPLISSAGE = 3        # mailles ; comble les petits trous (équivalent de window_size=3). 0 = désactivé
NODATA               = -9999.0
EMPRISE              = None     # optionnel : (xmin, xmax, ymin, ymax) en m pour ne garder que cette zone
PLAGE_Z              = None     # optionnel : (zmin, zmax) pour écarter les altitudes aberrantes

# Filtrage des points aberrants (lignes corrompues mais lisibles)
TUILE                = 256      # mailles par côté de tuile
SEUIL_DENSE          = 32       # points d'une tuile dans un bloc pour la stocker en dense
MIN_POINTS_TUILE     = 10       # une tuile avec moins de points sur tout le fichier est écartée
ECART_MAX_TUILES     = 2        # un vide de plus de 2 tuiles sépare deux groupes de points
PART_MIN_GROUPE      = 0.01     # un groupe détaché est gardé s'il contient >= 1 % des points
DENSITE_REL_MIN      = 0.10     # ... ou s'il couvre >= MIN_MAILLES_GROUPE mailles avec une densité
MIN_MAILLES_GROUPE   = 2000     #     (points/maille) >= 10 % de celle du levé principal (zone levée à part)
MAX_MAILLES_RASTER   = 2_000_000_000  # garde-fou : emprise démesurée = points aberrants non filtrés
NB_EXEMPLES_REJETS   = 5        # lignes rejetées affichées dans la console
NB_REJETS_FICHIER    = 200      # lignes rejetées écrites dans <nom>_rejets.txt (par portion)

TAILLE_BLOC          = 64 * 1024**2   # octets lus par bloc et par worker (~64 Mo)
LONGUEUR_MAX_LIGNE   = 1024**2  # au-delà, une « ligne » sans retour chariot est du bruit (octets nuls, binaire…)
INTERVALLE_AFFICHAGE = 1.0      # s
INTERVALLE_LOG       = 30.0     # s, si la sortie est redirigée vers un fichier

# Fichier de configuration (--config). Son chemin est transmis par variable
# d'environnement pour que les processus de lecture, qui réimportent ce module
# sous Windows, appliquent les mêmes paramètres que le processus principal.
VARIABLE_CONFIG = "XYZ2TIF_CONFIG"
CLES_CONFIG = {
    "chemins":  {"dossier": "DOSSIER_A_TRAITER"},
    "raster":   {"resolution": "RESOLUTION_RASTER", "epsg": "CODE_EPSG", "nodata": "NODATA",
                 "distance_remplissage": "DISTANCE_REMPLISSAGE"},
    "lecture":  {"workers": "NB_WORKERS", "ecraser_existants": "ECRASER_EXISTANTS",
                 "separateur": "SEPARATEUR", "emprise": "EMPRISE", "plage_z": "PLAGE_Z"},
    "filtrage": {"tuile": "TUILE", "seuil_dense": "SEUIL_DENSE", "min_points_tuile": "MIN_POINTS_TUILE",
                 "ecart_max_tuiles": "ECART_MAX_TUILES", "part_min_groupe": "PART_MIN_GROUPE",
                 "densite_rel_min": "DENSITE_REL_MIN", "min_mailles_groupe": "MIN_MAILLES_GROUPE"},
}


def appliquer_config(chemin):
    """Remplace les paramètres ci-dessus par ceux du fichier TOML."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
    with open(chemin, "rb") as f:
        cfg = tomllib.load(f)
    for section, cles in CLES_CONFIG.items():
        for cle, nom in cles.items():
            if cle in cfg.get(section, {}):
                valeur = cfg[section][cle]
                if nom in ("EMPRISE", "PLAGE_Z"):
                    valeur = tuple(valeur)
                elif nom == "CODE_EPSG" and isinstance(valeur, int):
                    valeur = f"EPSG:{valeur}"
                globals()[nom] = valeur


if os.environ.get(VARIABLE_CONFIG):
    appliquer_config(os.environ[VARIABLE_CONFIG])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ─── Affichage ────────────────────────────────────────────────────────────────
def fmt_duree(s):
    s = int(s)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


def fmt_taille(octets):
    for unite in ("o", "Ko", "Mo", "Go"):
        if octets < 1024:
            return f"{octets:.1f} {unite}"
        octets /= 1024
    return f"{octets:.1f} To"


def fmt_nb(n):
    return f"{n:,}".replace(",", " ")


class Progression:
    def __init__(self, total_octets):
        self.total = max(total_octets, 1)
        self.octets_fichiers_finis = 0
        self.t0 = time.time()
        self.console = sys.stdout.isatty()
        self.largeur = 0
        self.dernier_log = 0.0
        self.historique = []  # (instant, octets) pour le débit instantané

    def effacer(self):
        if self.console and self.largeur:
            sys.stdout.write("\r" + " " * self.largeur + "\r")
            sys.stdout.flush()
            self.largeur = 0

    def afficher(self, octets_fichier_courant, nom, rejetes=0, portions=""):
        maintenant = time.time()
        fait = self.octets_fichiers_finis + octets_fichier_courant
        frac = min(fait / self.total, 1.0)
        ecoule = maintenant - self.t0

        # Débit sur les 20 dernières secondes : révèle un ralentissement tout de suite
        self.historique.append((maintenant, fait))
        self.historique = [h for h in self.historique if maintenant - h[0] <= 20]
        t_ref, f_ref = self.historique[0]
        debit = (fait - f_ref) / (maintenant - t_ref) if maintenant > t_ref else 0
        restant = f"~{fmt_duree((self.total - fait) / debit)}" if debit > 0 else "?"

        n = 20
        barre = "#" * int(frac * n) + "-" * (n - int(frac * n))
        txt = (f"[{barre}] {frac*100:5.1f} %  {fmt_taille(fait)} / {fmt_taille(self.total)}  "
               f"{fmt_taille(debit)}/s  écoulé {fmt_duree(ecoule)}  restant {restant}  "
               f"rejets {fmt_nb(rejetes)}  portions {portions}")
        if self.console:
            largeur = shutil.get_terminal_size((120, 20)).columns - 1
            if len(txt) > largeur:
                txt = txt[:largeur - 3] + "..."
            self.effacer()
            sys.stdout.write(txt)
            sys.stdout.flush()
            self.largeur = len(txt)
        elif maintenant - self.dernier_log >= INTERVALLE_LOG:
            log.info(f"{txt} | {nom}")
            self.dernier_log = maintenant

    def log(self, niveau, msg):
        self.effacer()
        log.log(niveau, msg)


# ─── Accumulation : tuiles denses + liste creuse ──────────────────────────────
D = 2**30  # décalage pour encoder (tx, ty) en un seul entier positif


def encoder(tx, ty):
    return (tx + D) * (2 * D) + (ty + D)


def decoder(code):
    return code // (2 * D) - D, code % (2 * D) - D


class Grille:
    def __init__(self):
        self.tuiles = {}      # (tx, ty) -> [somme float64 (T,T), nb uint32 (T,T)]
        self.eparses = []     # DataFrames (ix, iy, s, n) des points épars
        self.nb_eparses = 0

    def ajouter(self, ix, iy, z):
        T = TUILE
        tx, ty = ix // T, iy // T
        num, uniques = pd.factorize(encoder(tx, ty))
        k = len(uniques)
        compte = np.bincount(num, minlength=k)
        txu, tyu = decoder(uniques)
        existe = np.fromiter(((int(a), int(b)) in self.tuiles for a, b in zip(txu, tyu)),
                             dtype=bool, count=k)
        dense = existe | (compte >= SEUIL_DENSE)

        # Tuiles denses : bincount limité à ces tuiles-là
        dj = np.flatnonzero(dense)
        if len(dj):
            renum = np.full(k, -1, np.int64)
            renum[dj] = np.arange(len(dj))
            r = renum[num]
            m = r >= 0
            loc = (iy[m] - ty[m] * T) * T + (ix[m] - tx[m] * T)
            idx = r[m] * (T * T) + loc
            s = np.bincount(idx, weights=z[m], minlength=len(dj) * T * T).reshape(len(dj), T, T)
            n = np.bincount(idx, minlength=len(dj) * T * T).reshape(len(dj), T, T)
            for j, u in enumerate(dj):
                cle = (int(txu[u]), int(tyu[u]))
                t = self.tuiles.get(cle)
                if t is None:
                    self.tuiles[cle] = [s[j].copy(), n[j].astype(np.uint32)]
                else:
                    t[0] += s[j]
                    t[1] += n[j].astype(np.uint32)

        # Points épars : liste creuse
        m = ~dense[num]
        if m.any():
            self.eparses.append(pd.DataFrame({"ix": ix[m], "iy": iy[m], "s": z[m],
                                              "n": np.ones(int(m.sum()), np.int64)}))
            self.nb_eparses += int(m.sum())
            if self.nb_eparses > 5_000_000:
                self.compacter()

    def compacter(self):
        if not self.eparses:
            return None
        df = pd.concat(self.eparses, ignore_index=True)
        df = df.groupby(["ix", "iy"], sort=False, as_index=False)[["s", "n"]].sum()
        self.eparses = [df]
        self.nb_eparses = len(df)
        return df


# ─── Lecture tolérante d'un bloc ──────────────────────────────────────────────
LIMITE_COORD = 1e8  # |coordonnée| au-delà : forcément une ligne corrompue


def champs_ligne(ligne):
    """Découpe une ligne déjà normalisée en (x, y, z) ou None si illisible."""
    champs = ligne.split()
    if len(champs) < 3:
        return None
    try:
        x, y, z = float(champs[0]), float(champs[1]), float(champs[2])
    except ValueError:
        return None
    if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(z)):
        return None
    if abs(x) >= LIMITE_COORD or abs(y) >= LIMITE_COORD:
        return None
    return x, y, z


def lignes_invalides(bloc, maxi, offset_bloc=0):
    """Renvoie jusqu'à `maxi` lignes rejetées du bloc, avec leur position approximative
    (octet) dans le fichier, pour diagnostic."""
    exemples = []
    pos = offset_bloc
    for brute in bloc.split(b"\n"):
        long = len(brute) + 1
        ligne = normaliser(brute).strip()
        if ligne and champs_ligne(ligne) is None:
            exemples.append((pos, brute[:150].decode("latin-1", "replace")))
            if len(exemples) >= maxi:
                break
        pos += long
    return exemples


def _normaliser_virgules(ligne):
    """Virgule décimale (« 330000,12 6780000,34 60,5 ») ou séparateur
    (« 330000.12,6780000.34,60.5 ») : décidé ligne par ligne."""
    if b"," not in ligne:
        return ligne
    if b"." in ligne:
        return ligne.replace(b",", b" ")          # le point est décimal -> virgule = séparateur
    if len(ligne.split()) >= 3:
        return ligne.replace(b",", b".")          # séparés par des blancs -> virgule décimale
    return ligne.replace(b",", b" ")              # « 330000,6780000,60 » -> séparateur


_TABLE = bytes.maketrans(b";\t\"'", b"    ")


def normaliser(bloc):
    """Uniformise les séparateurs : ; tabulation -> espace ; guillemets et apostrophes
    neutralisés (un guillemet isolé faisait avaler à pandas toutes les lignes suivantes
    du bloc) ; virgules traitées ligne par ligne."""
    bloc = bloc.translate(_TABLE)
    if b"," in bloc:
        bloc = b"\n".join(_normaliser_virgules(l) for l in bloc.split(b"\n"))
    return bloc


VIDE = np.empty((0, 3), np.float64)
_LIGNE_VIDE = re.compile(rb"\n(?=[ \t\r]*\n)")


def lire_lignes_python(bloc):
    """Lecture ligne à ligne, lente mais qui ne perd que les lignes réellement illisibles."""
    pts = [p for p in (champs_ligne(l) for l in bloc.split(b"\n")) if p is not None]
    return np.array(pts, np.float64).reshape(-1, 3)


def lire_bloc(bloc, profondeur=0):
    """Voie rapide (tout numérique) ; si une valeur non numérique est rencontrée,
    relecture tolérante de ce bloc seulement. Si le tokenizer échoue (ligne géante,
    manque de mémoire…), le bloc est coupé en deux et chaque moitié relue ; en dernier
    recours le morceau est lu ligne à ligne (rien n'est jeté en bloc). Renvoie (xyz, lent).
    Le bloc doit avoir été passé par normaliser()."""
    options = dict(sep=SEPARATEUR, header=None, names=["x", "y", "z"], usecols=[0, 1, 2],
                   engine="c", on_bad_lines="skip", quoting=csv.QUOTE_NONE,
                   skip_blank_lines=True, encoding="latin-1")  # latin-1 : tout octet est décodable
    try:
        try:
            df = pd.read_csv(io.BytesIO(bloc), dtype=np.float64, **options)
            return df.to_numpy(np.float64), False
        except pd.errors.ParserError:
            raise
        except ValueError:
            pass
        df = pd.read_csv(io.BytesIO(bloc), dtype=str, **options)
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.to_numpy(np.float64), True
    except pd.errors.EmptyDataError:
        return VIDE, True
    except (pd.errors.ParserError, MemoryError, ValueError):
        i = bloc.find(b"\n", len(bloc) // 2)
        if profondeur >= 6 or len(bloc) < 64 * 1024 or i < 0 or i == len(bloc) - 1:
            return lire_lignes_python(bloc), True
        a, _ = lire_bloc(bloc[:i + 1], profondeur + 1)
        b, _ = lire_bloc(bloc[i + 1:], profondeur + 1)
        return np.vstack([a, b]), True


# ─── Travail d'un worker : une portion d'octets du fichier ────────────────────
def traiter_portion(args):
    chemin, debut, fin, res, k, etat = args
    grille = Grille()
    nb_points = nb_rejetes = nb_blocs_lents = nb_blocs_nuls = 0
    exemples = []
    limite = LIMITE_COORD
    with open(chemin, "rb") as f:
        if debut > 0:
            # Se caler au début de la première ligne complète de la portion
            f.seek(debut - 1)
            f.readline(LONGUEUR_MAX_LIGNE)
        pos = f.tell()
        while pos < fin:
            offset_bloc = pos
            bloc = f.read(min(TAILLE_BLOC, fin - pos))
            if not bloc:
                break
            if not bloc.endswith(b"\n"):
                bloc += f.readline(LONGUEUR_MAX_LIGNE)  # terminer la ligne coupée (longueur bornée)
            pos = f.tell()
            if b"\x00" in bloc:
                bloc = bloc.replace(b"\x00", b"")  # zones d'octets nuls (fichier abîmé ou copie incomplète)
                nb_blocs_nuls += 1
            # lignes non vides (les lignes blanches ne sont pas des rejets)
            nb_lignes = (bloc.count(b"\n") + (0 if bloc.endswith(b"\n") else 1)
                         - len(_LIGNE_VIDE.findall(bloc))
                         - (1 if bloc.startswith((b"\n", b"\r\n")) else 0))

            xyz, lent = lire_bloc(normaliser(bloc))
            if lent:
                nb_blocs_lents += 1

            ok = np.isfinite(xyz).all(axis=1)
            ok &= (np.abs(xyz[:, 0]) < limite) & (np.abs(xyz[:, 1]) < limite)
            if EMPRISE is not None:
                xmin, xmax, ymin, ymax = EMPRISE
                ok &= ((xyz[:, 0] >= xmin) & (xyz[:, 0] <= xmax)
                       & (xyz[:, 1] >= ymin) & (xyz[:, 1] <= ymax))
            if PLAGE_Z is not None:
                ok &= (xyz[:, 2] >= PLAGE_Z[0]) & (xyz[:, 2] <= PLAGE_Z[1])
            xyz = xyz[ok]
            rejets_bloc = max(nb_lignes - len(xyz), 0)
            nb_rejetes += rejets_bloc
            if rejets_bloc and len(exemples) < NB_REJETS_FICHIER:
                # quelques exemples par bloc, pour couvrir tout le fichier
                exemples += lignes_invalides(bloc, min(10, NB_REJETS_FICHIER - len(exemples)),
                                             offset_bloc)
            del bloc

            if len(xyz):
                ix = np.floor(xyz[:, 0] / res).astype(np.int64)
                iy = np.floor(xyz[:, 1] / res).astype(np.int64)
                grille.ajouter(ix, iy, xyz[:, 2])
                nb_points += len(xyz)
            del xyz
            etat[k] = (min(pos, fin) - debut, nb_rejetes)

    etat[k] = (fin - debut, nb_rejetes)
    return {"tuiles": grille.tuiles, "eparses": grille.compacter(), "portion": (debut, fin),
            "points": nb_points, "rejetes": nb_rejetes,
            "blocs_lents": nb_blocs_lents, "blocs_nuls": nb_blocs_nuls, "exemples": exemples}


# ─── Fusion, filtrage des groupes isolés, écriture du GeoTIFF ─────────────────
def fusionner(resultats):
    tuiles = {}
    for r in resultats:
        for cle, (s, n) in r["tuiles"].items():
            t = tuiles.get(cle)
            if t is None:
                tuiles[cle] = [s, n]
            else:
                t[0] += s
                t[1] += n
    eparses = [r["eparses"] for r in resultats if r["eparses"] is not None]
    if eparses:
        eparses = pd.concat(eparses, ignore_index=True)
        eparses = eparses.groupby(["ix", "iy"], sort=False, as_index=False)[["s", "n"]].sum()
    else:
        eparses = None
    return tuiles, eparses


def compter_par_tuile(tuiles, eparses):
    """{(tx, ty): [nb de points, nb de mailles renseignées]}"""
    nb = {cle: [int(t[1].sum()), int((t[1] > 0).sum())] for cle, t in tuiles.items()}
    if eparses is not None and len(eparses):
        g = (eparses.assign(tx=eparses["ix"] // TUILE, ty=eparses["iy"] // TUILE)
             .groupby(["tx", "ty"])["n"].agg(["sum", "size"]))
        for (tx, ty), (n, m) in zip(g.index, g.to_numpy()):
            v = nb.setdefault((int(tx), int(ty)), [0, 0])
            v[0] += int(n)
            v[1] += int(m)
    return nb


def selectionner_tuiles(nb, res):
    """Regroupe les tuiles voisines (vide toléré : ECART_MAX_TUILES) et garde le groupe
    principal, plus les groupes détachés qui contiennent au moins PART_MIN_GROUPE des points
    OU dont la densité est comparable à celle du levé principal (une zone levée à part est
    dense ; des lignes corrompues éparpillées donnent ~1 point par maille).
    Renvoie (tuiles gardées, points écartés, liste des groupes écartés)."""
    total = sum(v[0] for v in nb.values())
    ecartes = []
    petites = {c: v for c, v in nb.items() if v[0] < MIN_POINTS_TUILE}
    if petites:
        ecartes.append(("tuiles quasi vides", sum(v[0] for v in petites.values()),
                        len(petites), list(petites)))
    nb = {c: v for c, v in nb.items() if v[0] >= MIN_POINTS_TUILE}
    g = ECART_MAX_TUILES + 1
    restantes = set(nb)
    groupes = []
    while restantes:
        depart = restantes.pop()
        groupe, pile = [depart], [depart]
        while pile:
            tx, ty = pile.pop()
            for dx in range(-g, g + 1):
                for dy in range(-g, g + 1):
                    v = (tx + dx, ty + dy)
                    if v in restantes:
                        restantes.remove(v)
                        groupe.append(v)
                        pile.append(v)
        pts = sum(nb[c][0] for c in groupe)
        mailles = max(sum(nb[c][1] for c in groupe), 1)
        groupes.append((pts, pts / mailles, groupe))

    groupes.sort(key=lambda x: -x[0])
    gardees = set()
    densite_ref = groupes[0][1] if groupes else 0
    for i, (pts, densite, groupe) in enumerate(groupes):
        mailles = pts / max(densite, 1e-9)
        zone_levee = (mailles >= MIN_MAILLES_GROUPE and densite >= DENSITE_REL_MIN * densite_ref)
        if i == 0 or pts >= PART_MIN_GROUPE * total or zone_levee:
            gardees.update(groupe)
        else:
            ecartes.append((f"groupe isolé ({densite:.1f} pt/maille)", pts, 1, groupe))
    pts_ecartes = sum(e[1] for e in ecartes)
    return gardees, pts_ecartes, ecartes


def emprise_tuiles(cles, res):
    T = TUILE * res
    xs = [c[0] for c in cles]; ys = [c[1] for c in cles]
    return (f"X {min(xs) * T:.0f}–{(max(xs) + 1) * T:.0f}  "
            f"Y {min(ys) * T:.0f}–{(max(ys) + 1) * T:.0f}")


def ecrire_geotiff(tuiles, eparses, cles, chemin_tif, res, epsg):
    T = TUILE
    txmin = min(c[0] for c in cles); txmax = max(c[0] for c in cles)
    tymin = min(c[1] for c in cles); tymax = max(c[1] for c in cles)
    forme = ((tymax - tymin + 1) * T, (txmax - txmin + 1) * T)
    if forme[0] * forme[1] > MAX_MAILLES_RASTER:
        raise ValueError(f"emprise démesurée ({forme[1]} x {forme[0]} mailles) : des points aberrants "
                         f"n'ont pas été filtrés ; renseigner EMPRISE = (xmin, xmax, ymin, ymax)")
    ix0, iy0 = txmin * T, tymin * T
    somme = np.zeros(forme, np.float64)
    nb = np.zeros(forme, np.uint32)
    for cle, (s, n) in tuiles.items():
        if cle in cles:
            r, c = (cle[1] - tymin) * T, (cle[0] - txmin) * T
            somme[r:r + T, c:c + T] += s
            nb[r:r + T, c:c + T] += n

    if eparses is not None and len(eparses):
        ix = eparses["ix"].to_numpy(); iy = eparses["iy"].to_numpy()
        codes_gardes = np.array([encoder(a, b) for a, b in cles], dtype=np.int64)
        m = np.isin(encoder(ix // T, iy // T), codes_gardes)
        r, c = iy[m] - iy0, ix[m] - ix0
        np.add.at(somme, (r, c), eparses["s"].to_numpy()[m])
        np.add.at(nb, (r, c), eparses["n"].to_numpy()[m].astype(np.uint32))

    # Rognage aux mailles renseignées + marge pour le remplissage des bords
    valide = nb > 0
    lignes = np.where(valide.any(axis=1))[0]
    colonnes = np.where(valide.any(axis=0))[0]
    r0, r1 = lignes[0], lignes[-1]
    c0, c1 = colonnes[0], colonnes[-1]
    p = DISTANCE_REMPLISSAGE
    somme = np.pad(somme[r0:r1 + 1, c0:c1 + 1], p)
    nb = np.pad(nb[r0:r1 + 1, c0:c1 + 1], p)
    valide = nb > 0
    ix0, iy0, iy1 = ix0 + c0 - p, iy0 + r0 - p, iy0 + r1 + p

    moyenne = np.full(somme.shape, NODATA, np.float32)
    moyenne[valide] = (somme[valide] / nb[valide]).astype(np.float32)
    del somme, nb

    if DISTANCE_REMPLISSAGE > 0:
        moyenne = fillnodata(moyenne, mask=valide.astype(np.uint8),
                             max_search_distance=DISTANCE_REMPLISSAGE, smoothing_iterations=0)

    moyenne = np.flipud(moyenne)  # ligne 0 = nord
    transform = from_origin(ix0 * res, (iy1 + 1) * res, res, res)
    profil = dict(driver="GTiff", height=moyenne.shape[0], width=moyenne.shape[1], count=1,
                  dtype="float32", transform=transform, nodata=NODATA,
                  compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256,
                  BIGTIFF="IF_SAFER")
    try:
        with rasterio.open(chemin_tif, "w", crs=epsg, **profil) as dst:
            dst.write(moyenne, 1)
        crs_ok = True
    except Exception as e:
        # Installation PROJ incohérente : on écrit quand même le raster, sans système de coordonnées
        log.warning(f"Système de coordonnées non écrit ({e}). Raster écrit sans CRS : "
                    f"attribuer {epsg} dans QGIS (Couche > Définir la projection).")
        with rasterio.open(chemin_tif, "w", **profil) as dst:
            dst.write(moyenne, 1)
        crs_ok = False
    return moyenne.shape, crs_ok


# ─── Programme principal ──────────────────────────────────────────────────────
def xyz_vers_tif(dossier, resolution, epsg, workers, ecraser):
    dossier_path = Path(dossier)
    if not dossier_path.is_dir():
        log.error(f"Dossier introuvable : {dossier}")
        return

    fichiers = sorted(dossier_path.glob("*.xyz"))
    if not fichiers:
        log.warning("Aucun fichier .xyz trouvé.")
        return

    a_traiter = []
    for xyz in fichiers:
        tif = xyz.with_suffix(".tif")
        if tif.exists() and not ecraser:
            log.info(f"[IGNORÉ] {xyz.name} → .tif déjà présent")
            continue
        a_traiter.append((xyz, tif, xyz.stat().st_size))

    if not a_traiter:
        log.info("Aucun fichier à traiter.")
        return

    total = sum(t[2] for t in a_traiter)
    log.info(f"{len(a_traiter)} fichier(s) à traiter ({fmt_taille(total)}) avec {workers} worker(s).")
    prog = Progression(total)
    ok = ko = 0

    with Manager() as manager, ProcessPoolExecutor(max_workers=workers) as executor:
        for xyz, tif, taille in a_traiter:
            t_debut = time.time()
            with open(xyz, "rb") as f:
                debut_fichier = f.read(4096).split(b"\n")[:3]
            for ligne in debut_fichier:
                prog.log(logging.INFO, f"{xyz.name} : aperçu {ligne[:120].decode('latin-1', 'replace')!r}")
            nb_portions = workers if taille > 64 * 1024**2 else 1
            bornes = [taille * i // nb_portions for i in range(nb_portions + 1)]
            etat = manager.dict()
            futures = [executor.submit(traiter_portion,
                                       (str(xyz), bornes[i], bornes[i + 1], resolution, i, etat))
                       for i in range(nb_portions)]
            try:
                restants = set(futures)
                while restants:
                    _, restants = wait(restants, timeout=INTERVALLE_AFFICHAGE,
                                       return_when=FIRST_COMPLETED)
                    try:
                        e = dict(etat)
                    except Exception:
                        e = {}
                    lus = sum(v[0] for v in e.values())
                    rej = sum(v[1] for v in e.values())
                    portions = "/".join(
                        f"{100 * e.get(i, (0, 0))[0] / max(bornes[i + 1] - bornes[i], 1):.0f}"
                        for i in range(nb_portions)) + " %"
                    prog.afficher(lus, xyz.name, rej, portions)

                resultats = [f.result() for f in futures]
                nb_points = sum(r["points"] for r in resultats)
                nb_rejetes = sum(r["rejetes"] for r in resultats)
                blocs_lents = sum(r["blocs_lents"] for r in resultats)
                blocs_nuls = sum(r["blocs_nuls"] for r in resultats)
                tous_exemples = sorted(ex for r in resultats for ex in r["exemples"])

                prog.log(logging.INFO, f"{xyz.name} : {fmt_nb(nb_points)} points lus en "
                                       f"{fmt_duree(time.time() - t_debut)}")
                for r in resultats:
                    d, fn = r["portion"]
                    prog.log(logging.INFO, f"    portion {fmt_taille(d)} – {fmt_taille(fn)} : "
                                           f"{fmt_nb(r['points'])} points, {fmt_nb(r['rejetes'])} rejet(s)")
                if nb_rejetes:
                    prog.log(logging.WARNING, f"{xyz.name} : {fmt_nb(nb_rejetes)} ligne(s) illisible(s) "
                                              f"ignorée(s), réparties dans {blocs_lents} bloc(s)")
                    if blocs_nuls:
                        prog.log(logging.WARNING, f"{xyz.name} : octets nuls trouvés dans {blocs_nuls} bloc(s) "
                                                  f"de {fmt_taille(TAILLE_BLOC)} (zone du fichier abîmée ?)")
                    pas = max(len(tous_exemples) // NB_EXEMPLES_REJETS, 1)
                    for pos, ex in tous_exemples[::pas][:NB_EXEMPLES_REJETS]:
                        prog.log(logging.WARNING, f"    exemple (octet {pos}) : {ex[:80]!r}")
                    fichier_rejets = xyz.with_name(xyz.stem + "_rejets.txt")
                    with open(fichier_rejets, "w", encoding="utf-8") as fr:
                        fr.write(f"# {nb_rejetes} lignes rejetées ; échantillon ci-dessous "
                                 f"(position approximative en octets dans {xyz.name})\n")
                        for pos, ex in tous_exemples:
                            fr.write(f"{pos}\t{ex}\n")
                    prog.log(logging.WARNING, f"    échantillon de lignes rejetées : {fichier_rejets.name}")

                tuiles, eparses = fusionner(resultats)
                del resultats
                nb_tuile = compter_par_tuile(tuiles, eparses)
                if not nb_tuile:
                    raise ValueError("aucun point valide lu")
                cles, pts_ecartes, ecartes = selectionner_tuiles(nb_tuile, resolution)
                prog.log(logging.INFO, f"{xyz.name} : levé conservé {emprise_tuiles(cles, resolution)}")
                if pts_ecartes:
                    prog.log(logging.WARNING, f"{xyz.name} : {fmt_nb(pts_ecartes)} point(s) aberrant(s) "
                                              f"écarté(s) :")
                    for nom_g, pts, n, groupe in sorted(ecartes, key=lambda e: -e[1])[:10]:
                        prog.log(logging.WARNING, f"    {nom_g} : {fmt_nb(pts)} pts, "
                                                  f"{emprise_tuiles(groupe, resolution)}")

                prog.log(logging.INFO, f"{xyz.name} : écriture du GeoTIFF…")
                forme, crs_ok = ecrire_geotiff(tuiles, eparses, cles, tif, resolution, epsg)
                del tuiles, eparses
                prog.log(logging.INFO, f"[OK] {xyz.name} → {tif.name} ({forme[1]} x {forme[0]} mailles) "
                                       f"en {fmt_duree(time.time() - t_debut)}")
                ok += 1
            except Exception as e:
                for f in futures:
                    f.cancel()
                prog.log(logging.ERROR, f"[ERREUR] {xyz.name} → {e}")
                ko += 1
            prog.octets_fichiers_finis += taille

    prog.effacer()
    log.info(f"─── Terminé en {fmt_duree(time.time() - prog.t0)} : {ok} succès, {ko} erreur(s) ───")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Conversion XYZ -> GeoTIFF (moyenne des Z par maille)")
    parser.add_argument("--config", help="Fichier de configuration TOML (voir config_exemple.toml)")
    parser.add_argument("--dossier", help="Dossier contenant les fichiers .xyz")
    parser.add_argument("--resolution", type=float, help="Taille de maille (m)")
    parser.add_argument("--epsg", help="Code EPSG, ex. EPSG:2154 ou 2154")
    parser.add_argument("--workers", type=int, help="Processus de lecture en parallèle")
    parser.add_argument("--conserver", action="store_true", help="Ne pas remplacer les .tif existants")
    args = parser.parse_args()
    if args.config:
        os.environ[VARIABLE_CONFIG] = str(Path(args.config).resolve())
        appliquer_config(args.config)
    if args.dossier:
        DOSSIER_A_TRAITER = args.dossier
    if args.resolution:
        RESOLUTION_RASTER = args.resolution
    if args.epsg:
        CODE_EPSG = args.epsg if not args.epsg.isdigit() else f"EPSG:{args.epsg}"
    if args.workers:
        NB_WORKERS = args.workers
    if args.conserver:
        ECRASER_EXISTANTS = False
    if not DOSSIER_A_TRAITER:
        parser.error("préciser le dossier des .xyz : --dossier ou [chemins] dossier dans la configuration")
    xyz_vers_tif(
        dossier    = DOSSIER_A_TRAITER,
        resolution = RESOLUTION_RASTER,
        epsg       = CODE_EPSG,
        workers    = NB_WORKERS,
        ecraser    = ECRASER_EXISTANTS,
    )
