"""
anonymat.py

Anonymisation et contrôles communs à integrer_depot.py et aux hooks git.

La liste des termes à remplacer ou à interdire (nom, identifiants, noms de
projets…) est lue dans un fichier personnel, hors du dépôt :
    1. variable d'environnement OUTILS_TRAVAIL_ANONYMISATION, sinon
    2. ~/.config/outils-travail/anonymisation.toml
Voir anonymisation_exemple.toml. Ces termes ne doivent jamais être écrits
dans le dépôt lui-même.
"""

import io
import os
import re
import sys
import zipfile
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

FICHIER_DEFAUT = Path.home() / ".config" / "outils-travail" / "anonymisation.toml"

# Motifs bloquants quel que soit le fichier d'anonymisation
MOTIFS_SECRETS = [
    (re.compile(r"api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}", re.I), "clé d'API"),
    (re.compile(r"(mot de passe|password|passwd|mdp)\s*[:=]\s*\S{4,}", re.I), "mot de passe"),
    (re.compile(r"(token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.I), "jeton / secret"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "clé privée"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "jeton GitHub"),
]
# Chemin de profil Windows nominatif (C:\Users\<nom>\…), sauf le gabarit <utilisateur>
MOTIF_PROFIL = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<utilisateur>|Public\b|Default\b)([^\\/\s'\"<>]+)", re.I)

EXTENSIONS_OFFICE = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".odt", ".ods"}
CHAMPS_AUTEUR = re.compile(
    r"<(dc:creator|cp:lastModifiedBy|meta:initial-creator|dc:contributor)>.*?</\1>", re.S)


class Anonymisation:
    """Remplacements et termes interdits lus dans le fichier personnel."""

    def __init__(self, chemin=None):
        chemin = chemin or os.environ.get("OUTILS_TRAVAIL_ANONYMISATION") or FICHIER_DEFAUT
        self.chemin = Path(chemin)
        self.trouve = self.chemin.is_file()
        cfg = {}
        if self.trouve:
            with open(self.chemin, "rb") as f:
                cfg = tomllib.load(f)
        # Remplacements appliqués du plus long au plus court (un nom de projet
        # complet avant un fragment qu'il contient)
        self.remplacements = sorted(cfg.get("remplacements", {}).items(), key=lambda kv: -len(kv[0]))
        self.interdits = [t for t in cfg.get("interdits", {}).get("termes", []) if t]
        self.identite = cfg.get("identite", {})
        termes = [k for k, _ in self.remplacements] + self.interdits
        self._motif = re.compile("|".join(re.escape(t) for t in termes), re.I) if termes else None

    # -- remplacement --------------------------------------------------------
    def remplacer(self, texte):
        for ancien, nouveau in self.remplacements:
            texte = re.sub(re.escape(ancien), lambda m: nouveau, texte, flags=re.I)
        return texte

    def remplacer_fichier(self, chemin):
        """Anonymise un fichier en place. Renvoie True s'il a été modifié."""
        chemin = Path(chemin)
        if chemin.suffix.lower() in EXTENSIONS_OFFICE:
            return self._remplacer_office(chemin)
        brut = chemin.read_bytes()
        texte, encodage = decoder(brut)
        if texte is None:
            return False
        nouveau = self.remplacer(texte)
        if nouveau != texte:
            chemin.write_bytes(nouveau.encode(encodage))
            return True
        return False

    def _remplacer_office(self, chemin):
        """Vide les champs auteur des métadonnées et anonymise le XML interne."""
        tampon = io.BytesIO()
        modifie = False
        with zipfile.ZipFile(chemin) as zin, zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith((".xml", ".rels")):
                    texte = data.decode("utf-8", errors="surrogateescape")
                    nouveau = CHAMPS_AUTEUR.sub(lambda m: f"<{m.group(1)}></{m.group(1)}>", texte)
                    nouveau = self.remplacer(nouveau)
                    if nouveau != texte:
                        modifie = True
                        data = nouveau.encode("utf-8", errors="surrogateescape")
                zout.writestr(item, data)
        if modifie:
            chemin.write_bytes(tampon.getvalue())
        return modifie

    # -- contrôle -------------------------------------------------------------
    def problemes_texte(self, texte):
        """Liste (n° de ligne, motif, extrait) des problèmes trouvés dans un texte."""
        resultats = []
        for num, ligne in enumerate(texte.splitlines(), 1):
            if self._motif:
                for m in self._motif.finditer(ligne):
                    resultats.append((num, "terme personnel", masquer(m.group(0))))
            for motif, nom in MOTIFS_SECRETS:
                m = motif.search(ligne)
                if m:
                    # Seul le mot-clé est affiché, jamais la valeur du secret
                    cle = re.split(r"\s*[=:]", m.group(0), maxsplit=1)[0]
                    resultats.append((num, nom, f"{cle[:20]} = ***"))
            m = MOTIF_PROFIL.search(ligne)
            if m:
                resultats.append((num, "chemin de profil Windows", masquer(m.group(0))))
        return resultats

    def problemes_fichier(self, chemin):
        chemin = Path(chemin)
        if chemin.suffix.lower() in EXTENSIONS_OFFICE:
            resultats = []
            with zipfile.ZipFile(chemin) as z:
                for nom in z.namelist():
                    if nom.endswith((".xml", ".rels")):
                        texte = z.read(nom).decode("utf-8", errors="replace")
                        for m in CHAMPS_AUTEUR.finditer(texte):
                            if re.sub(r"<[^>]+>", "", m.group(0)).strip():
                                resultats.append((0, "métadonnée auteur", f"{nom} : {m.group(1)}"))
                        resultats += [(0, p, f"{nom} : {e}") for _, p, e in self.problemes_texte(texte)]
            return resultats
        brut = chemin.read_bytes()
        texte, _ = decoder(brut)
        if texte is None:
            # Fichier binaire : recherche des termes en UTF-8 et UTF-16
            resultats = []
            if self._motif:
                for enc in ("utf-8", "utf-16-le"):
                    try:
                        if self._motif.search(brut.decode(enc, errors="ignore")):
                            resultats.append((0, "terme personnel (binaire)", enc))
                    except Exception:
                        pass
            return resultats
        return self.problemes_texte(texte)


def decoder(brut):
    """Décode un fichier texte ; (None, None) pour un fichier binaire."""
    if b"\x00" in brut[:8192]:
        return None, None
    for encodage in ("utf-8", "cp1252"):
        try:
            return brut.decode(encodage), encodage
        except UnicodeDecodeError:
            continue
    return None, None


def masquer(terme):
    """Affiche un terme sensible sans le recopier en entier (journaux, CI…)."""
    return terme if len(terme) <= 3 else terme[:2] + "…" + terme[-1]
