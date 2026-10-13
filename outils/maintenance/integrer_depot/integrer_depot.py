"""
integrer_depot.py

Intègre un dépôt GitHub dans outils-travail, sans son historique, en retirant
l'identité de l'auteur et de ses projets, puis archive le dépôt source.

Étapes :
  1. clone du dépôt source dans un dossier temporaire ;
  2. sélection des fichiers : dossiers old/, OLD/, _OLD/ écartés ; pour un
     script décliné en versions (nom_v2.py, nom_v3.py…), seule la dernière
     est gardée, sous le nom sans suffixe (nom.py) ;
  3. anonymisation selon le fichier personnel anonymisation.toml (voir
     anonymat.py) et effacement des métadonnées auteur des fichiers Office ;
  4. contrôle BLOQUANT : termes personnels, clés d'API, mots de passe,
     chemins C:\\Users\\<nom>… — rien n'est copié s'il reste un problème ;
  5. copie dans le dossier de destination, commit sous l'identité neutre
     définie dans anonymisation.toml, push ;
  6. archivage du dépôt source (gh repo archive), seulement si le push a réussi ;
     ou, avec --supprimer, suppression DÉFINITIVE du dépôt source (gh repo
     delete), après confirmation en retapant son nom.

Usage :
    python integrer_depot.py Ono-coder/mon_depot outils/mon_outil
    python integrer_depot.py Ono-coder/mon_depot outils/mon_outil --exclure "*.docx" --simulation
    python integrer_depot.py --archiver-seulement Ono-coder/depot1 Ono-coder/depot2
    python integrer_depot.py --supprimer-seulement Ono-coder/depot1 Ono-coder/depot2

Prérequis : git, et gh (GitHub CLI) authentifié pour l'archivage.
"""

import argparse
import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from anonymat import Anonymisation  # noqa: E402

DOSSIERS_ANCIENS = {"old", "_old", "ancien", "anciens"}
MOTIF_VERSION = re.compile(r"^(?P<base>.+?)_v(?P<num>\d+)$", re.I)


def executer(cmd, cwd=None, verifier=True):
    """Lance une commande ; renvoie sa sortie standard."""
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if verifier and res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{res.stderr.strip()}")
    return res.stdout.strip()


def racine_depot(depuis):
    return Path(executer(["git", "rev-parse", "--show-toplevel"], cwd=depuis))


# ---------------------------------------------------------------------------
# Sélection des fichiers
# ---------------------------------------------------------------------------

def selectionner(fichiers, exclure, garder_versions):
    """Renvoie {chemin source relatif: chemin cible relatif} et la liste des écartés."""
    retenus, ecartes = {}, []
    for f in fichiers:
        parties = Path(f).parts
        if any(p.lower() in DOSSIERS_ANCIENS for p in parties[:-1]):
            ecartes.append((f, "ancienne version (dossier)"))
        elif any(fnmatch.fnmatch(f, m) or fnmatch.fnmatch(Path(f).name, m) for m in exclure):
            ecartes.append((f, "exclu (--exclure)"))
        else:
            retenus[f] = f

    if garder_versions:
        return retenus, ecartes

    # Versions d'un même script : nom.py (v1), nom_v2.py, nom_v3.py… -> dernière, renommée nom.py
    groupes = {}
    for f in retenus:
        p = Path(f)
        m = MOTIF_VERSION.match(p.stem)
        base, num = (m.group("base"), int(m.group("num"))) if m else (p.stem, 1)
        groupes.setdefault((str(p.parent), base.lower(), p.suffix.lower()), []).append((num, f, base))
    for (parent, _, suffixe), membres in groupes.items():
        if len(membres) == 1 and not MOTIF_VERSION.match(Path(membres[0][1]).stem):
            continue
        membres.sort()
        num, garde, base = membres[-1]
        retenus[garde] = str(Path(parent) / f"{base}{suffixe}") if parent != "." else f"{base}{suffixe}"
        for _, f, _ in membres[:-1]:
            del retenus[f]
            ecartes.append((f, f"remplacé par {garde}"))
    return retenus, ecartes


# ---------------------------------------------------------------------------
# Intégration
# ---------------------------------------------------------------------------

def integrer(args, anonym):
    depot_cible = racine_depot(args.depot_cible)
    destination = depot_cible / args.destination
    if destination.exists() and any(destination.iterdir()) and not args.remplacer:
        sys.exit(f"Le dossier {destination} existe déjà (--remplacer pour l'écraser).")
    if executer(["git", "status", "--porcelain"], cwd=depot_cible) and not args.simulation:
        sys.exit("Le dépôt outils-travail a des modifications non commitées : les commiter ou les mettre de côté d'abord.")

    with tempfile.TemporaryDirectory(prefix="integration_") as tmp:
        source = Path(tmp) / "source"
        print(f"Clonage de {args.depot}…")
        executer(["git", "clone", "--depth", "1", f"https://github.com/{args.depot}.git", str(source)])
        fichiers = executer(["git", "ls-files"], cwd=source).splitlines()

        retenus, ecartes = selectionner(fichiers, args.exclure, args.garder_versions)
        for f, raison in ecartes:
            print(f"  écarté : {f} ({raison})")

        # Copie dans une zone de préparation, puis anonymisation
        prep = Path(tmp) / "prep"
        for src, cible in retenus.items():
            (prep / cible).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / src, prep / cible)
            if src != cible:
                print(f"  renommé : {src} -> {cible}")
        modifies = [c for c in retenus.values() if anonym.remplacer_fichier(prep / c)]
        for c in modifies:
            print(f"  anonymisé : {c}")

        # Contrôle bloquant
        problemes = [(c, n, quoi, extrait) for c in retenus.values()
                     for n, quoi, extrait in anonym.problemes_fichier(prep / c)]
        if problemes:
            print("\nIntégration ANNULÉE : il reste des informations à retirer.")
            for c, n, quoi, extrait in problemes:
                print(f"  {c}{':' + str(n) if n else ''} : {quoi} ({extrait})")
            print("Corriger le dépôt source, ajouter un remplacement dans anonymisation.toml,"
                  " ou écarter le fichier avec --exclure.")
            sys.exit(1)

        if args.simulation:
            print(f"\nSimulation : {len(retenus)} fichier(s) seraient copiés dans {args.destination}/")
            return False

        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(prep, destination)

    # Commit sous identité neutre
    nom = anonym.identite.get("nom")
    email = anonym.identite.get("email")
    ident = (["-c", f"user.name={nom}"] if nom else []) + (["-c", f"user.email={email}"] if email else [])
    auteur = f"{nom or executer(['git', 'config', 'user.name'], cwd=depot_cible)} " \
             f"<{email or executer(['git', 'config', 'user.email'], cwd=depot_cible)}>"
    if anonym.problemes_texte(auteur):
        sys.exit(f"L'identité de commit contient un terme personnel : définir [identite] nom / email "
                 f"dans {anonym.chemin}.")
    executer(["git", "add", "--", args.destination], cwd=depot_cible)
    message = f"Intégration de {args.depot.split('/')[-1]} dans {args.destination}/"
    executer(["git", *ident, "commit", "-m", message], cwd=depot_cible)
    print(f"\nCommit : {message}")

    if args.sans_push:
        return False
    executer(["git", "push", "origin", "HEAD"], cwd=depot_cible)
    print("Push effectué.")
    return True


def archiver(depots):
    for depot in depots:
        executer(["gh", "repo", "archive", depot, "--yes"])
        print(f"Archivé : {depot}")


def supprimer(depots):
    """Supprime définitivement des dépôts GitHub, chacun confirmé en retapant son nom."""
    print("ATTENTION : la suppression d'un dépôt GitHub est DÉFINITIVE "
          "(code, historique, issues). Les fichiers écartés à l'intégration "
          "(anciennes versions, documents, secrets) seront perdus.")
    for depot in depots:
        reponse = input(f"\nPour supprimer {depot}, retapez son nom complet (vide = passer) : ").strip()
        if reponse != depot:
            print(f"  Non supprimé : {depot}")
            continue
        res = subprocess.run(["gh", "repo", "delete", depot, "--yes"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0:
            print(f"  Supprimé : {depot}")
        elif "delete_repo" in res.stderr:
            sys.exit("  gh n'a pas le droit de supprimer des dépôts : lancer une fois\n"
                     "    gh auth refresh -h github.com -s delete_repo\npuis recommencer.")
        else:
            sys.exit(f"  Échec de la suppression de {depot} : {res.stderr.strip()}")


def main():
    parser = argparse.ArgumentParser(
        description="Intègre un dépôt GitHub dans outils-travail (anonymisé, sans historique) puis l'archive")
    parser.add_argument("depot", nargs="?", help="Dépôt source, ex. Ono-coder/mon_depot")
    parser.add_argument("destination", nargs="?", help="Dossier cible dans outils-travail, ex. outils/mon_outil")
    parser.add_argument("--exclure", nargs="+", default=[], metavar="MOTIF",
                        help="Fichiers à ne pas reprendre (motifs, ex. '*.docx' 'essais/*')")
    parser.add_argument("--garder-versions", action="store_true",
                        help="Garder toutes les versions (nom_v2.py…) et les dossiers old/")
    parser.add_argument("--remplacer", action="store_true", help="Écraser un dossier de destination existant")
    parser.add_argument("--anonymisation", help="Fichier anonymisation.toml (défaut : voir anonymat.py)")
    parser.add_argument("--depot-cible", default=Path(__file__).resolve().parent,
                        help="Clone local d'outils-travail (défaut : celui qui contient ce script)")
    parser.add_argument("--simulation", action="store_true",
                        help="Tout vérifier sans rien copier, commiter, pousser ni archiver")
    parser.add_argument("--sans-push", action="store_true", help="Commiter sans pousser (et donc sans archiver)")
    parser.add_argument("--sans-archive", action="store_true", help="Ne pas archiver le dépôt source")
    parser.add_argument("--supprimer", action="store_true",
                        help="Supprimer DÉFINITIVEMENT le dépôt source au lieu de l'archiver "
                             "(après push réussi et confirmation)")
    parser.add_argument("--supprimer-seulement", nargs="+", metavar="DEPOT",
                        help="Supprimer DÉFINITIVEMENT ces dépôts déjà intégrés (confirmation pour chacun)")
    parser.add_argument("--archiver-seulement", nargs="+", metavar="DEPOT",
                        help="Archiver ces dépôts déjà intégrés, sans rien intégrer")
    args = parser.parse_args()

    if args.supprimer_seulement:
        supprimer(args.supprimer_seulement)
        return
    if args.archiver_seulement:
        archiver(args.archiver_seulement)
        return
    if args.supprimer and args.sans_archive:
        parser.error("--supprimer et --sans-archive sont incompatibles")
    if not args.depot or not args.destination:
        parser.error("préciser le dépôt source et le dossier de destination")

    anonym = Anonymisation(args.anonymisation)
    if not anonym.trouve:
        sys.exit(f"Fichier d'anonymisation introuvable : {anonym.chemin}\n"
                 "Copier anonymisation_exemple.toml à cet emplacement et le compléter.")

    pousse = integrer(args, anonym)
    if pousse and args.supprimer:
        supprimer([args.depot])
    elif pousse and not args.sans_archive:
        archiver([args.depot])


if __name__ == "__main__":
    main()
