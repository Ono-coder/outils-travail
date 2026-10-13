"""
controle_commit.py

Contrôle appelé par les hooks git d'outils-travail (voir installer_hooks.py) :
  - pre-commit : refuse le commit si un fichier indexé, l'auteur ou le
    committer contient un terme personnel, un secret ou un chemin
    C:\\Users\\<nom> ;
  - commit-msg : même contrôle sur le message de commit (Signed-off-by…).

Les termes personnels sont lus dans anonymisation.toml (voir anonymat.py).
Sans ce fichier, seuls les secrets et les chemins de profil sont contrôlés.
En cas de faux positif avéré : git commit --no-verify.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from anonymat import Anonymisation, EXTENSIONS_OFFICE, decoder  # noqa: E402


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def controle_pre_commit(anonym):
    problemes = []
    for ident in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        texte = git("var", ident).decode("utf-8", "replace").rsplit(">", 1)[0] + ">"
        problemes += [(ident, n, quoi, e) for n, quoi, e in anonym.problemes_texte(texte)]

    fichiers = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8").split("\0")
    for f in filter(None, fichiers):
        contenu = git("show", f":{f}")
        if Path(f).suffix.lower() in EXTENSIONS_OFFICE or decoder(contenu)[0] is None:
            with tempfile.TemporaryDirectory() as tmp:
                chemin = Path(tmp) / Path(f).name
                chemin.write_bytes(contenu)
                trouves = anonym.problemes_fichier(chemin)
        else:
            trouves = anonym.problemes_texte(decoder(contenu)[0])
        problemes += [(f, n, quoi, e) for n, quoi, e in trouves]
    return problemes


def controle_message(anonym, fichier_message):
    texte = Path(fichier_message).read_text(encoding="utf-8", errors="replace")
    lignes = "\n".join(l for l in texte.splitlines() if not l.startswith("#"))
    return [("message de commit", n, quoi, e) for n, quoi, e in anonym.problemes_texte(lignes)]


def main():
    mode = sys.argv[1]
    anonym = Anonymisation()
    if not anonym.trouve:
        print(f"[controle] {anonym.chemin} absent : seuls les secrets et chemins de profil sont contrôlés.",
              file=sys.stderr)
    if mode == "pre-commit":
        problemes = controle_pre_commit(anonym)
    else:
        problemes = controle_message(anonym, sys.argv[2])
    if problemes:
        print("\nCommit REFUSÉ : informations personnelles ou secrètes détectées.", file=sys.stderr)
        for ou, n, quoi, extrait in problemes:
            print(f"  {ou}{':' + str(n) if n else ''} : {quoi} ({extrait})", file=sys.stderr)
        print("Corriger, ou en cas de faux positif avéré : git commit --no-verify", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
