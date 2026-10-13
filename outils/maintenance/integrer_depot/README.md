# integrer_depot — Intégrer un dépôt dans outils-travail, puis l'archiver

Automatise l'ajout d'un nouveau script (publié d'abord dans son propre dépôt
GitHub) dans `outils-travail`, en garantissant qu'aucune information
personnelle ni aucun secret n'arrive dans ce dépôt public. Le dossier
contient aussi des **hooks git** qui appliquent les mêmes contrôles à tous
les commits.

| Fichier | Rôle |
|---|---|
| `integrer_depot.py` | Intégration (sans historique) + anonymisation + contrôle + commit + push + archivage |
| `anonymat.py` | Remplacements et contrôles communs |
| `controle_commit.py`, `hooks/` | Contrôle de chaque commit (contenu, auteur, message) |
| `installer_hooks.py` | Active les hooks dans un clone d'outils-travail |
| `anonymisation_exemple.toml` | Modèle du fichier personnel d'anonymisation |

## Prérequis

- `git` ; Python 3.11 ou plus (sinon `pip install tomli`) ;
- [GitHub CLI](https://cli.github.com/) `gh`, connecté (`gh auth login`), pour
  l'archivage des dépôts sources ;
- un clone local d'`outils-travail` à jour, sans modification en cours.

## Mise en place (une seule fois par poste)

1. **Fichier personnel d'anonymisation** : copier `anonymisation_exemple.toml`
   vers `%USERPROFILE%\.config\outils-travail\anonymisation.toml` (Windows)
   ou `~/.config/outils-travail/anonymisation.toml` (Linux), ou indiquer son
   chemin dans la variable d'environnement `OUTILS_TRAVAIL_ANONYMISATION`.
   Y renseigner :
   - `[remplacements]` : termes à remplacer dans les fichiers intégrés
     (identifiant Windows, noms de dossiers d'études…) ;
   - `[interdits] termes` : termes qui doivent bloquer l'intégration ou le
     commit (adresse e-mail, nom complet…) ;
   - `[identite]` : identité des commits d'intégration. Utiliser l'adresse
     « noreply » de GitHub (*Settings → Emails*), jamais l'adresse
     personnelle.

   Choisir des termes assez précis : un nom de projet qui est aussi un nom
   de commune (« BEAUNE » par exemple) bloquerait le notebook des stations
   Météo-France ; préférer alors le chemin complet (`TEMP\BEAUNE`).

   Ce fichier contient justement les termes à cacher : il ne doit **jamais**
   être placé dans le dépôt (`anonymisation.toml` est de toute façon ignoré
   par git).

2. **Identité git du clone** : configurer la même adresse « noreply » pour
   les commits faits à la main :

   ```bash
   git config user.name  "MonPseudoGitHub"
   git config user.email "12345678+MonPseudoGitHub@users.noreply.github.com"
   ```

   Éviter aussi `git commit -s` (ligne `Signed-off-by`) et l'option
   « Sign off » de l'éditeur, qui recopient l'adresse dans le message.

3. **Hooks** :

   ```bash
   python outils/maintenance/integrer_depot/installer_hooks.py
   ```

## Intégrer un dépôt

```bash
# vérifier d'abord, sans rien modifier
python outils/maintenance/integrer_depot/integrer_depot.py Ono-coder/mon_depot outils/mon_outil --simulation

# intégrer, pousser, puis archiver le dépôt source
python outils/maintenance/integrer_depot/integrer_depot.py Ono-coder/mon_depot outils/mon_outil
```

Déroulement :
1. clone du dépôt source dans un dossier temporaire ;
2. sélection des fichiers :
   - les dossiers `old/`, `OLD/`, `_OLD/`, `ancien(s)/` sont écartés ;
   - pour un script décliné en versions (`nom.py`, `nom_v2.py`,
     `nom_v3.py`…), seule la dernière est gardée, sous le nom `nom.py` ;
3. anonymisation :
   - remplacements de `[remplacements]` dans les fichiers texte et dans le
     XML des fichiers Office ;
   - effacement de l'auteur et du « modifié par » des `.docx`, `.xlsx`,
     `.pptx`… ;
4. **contrôle bloquant** : l'intégration s'arrête, sans rien copier, s'il
   reste :
   - un terme personnel ;
   - une clé d'API, un mot de passe, un jeton ou une clé privée ;
   - un chemin `C:\Users\<nom>\…`.

   Le rapport indique le fichier et la ligne, sans réafficher la valeur des
   secrets ;
5. copie dans le dossier de destination et commit sous l'identité
   `[identite]` ;
6. push, puis **archivage** du dépôt source (`gh repo archive`), seulement si
   le push a réussi ; avec `--supprimer`, le dépôt source est **supprimé**
   à la place (voir ci-dessous).

| Option | Rôle |
|---|---|
| `--simulation` | Tout vérifier sans rien copier, commiter, pousser ni archiver |
| `--exclure MOTIF…` | Fichiers à ne pas reprendre (ex. `"*.docx" "essais/*"`) |
| `--garder-versions` | Garder toutes les versions et les dossiers `old/` |
| `--remplacer` | Écraser un dossier de destination existant |
| `--sans-push` | Commiter sans pousser (et donc sans archiver) |
| `--sans-archive` | Pousser sans archiver le dépôt source |
| `--anonymisation FICHIER` | Autre fichier d'anonymisation |
| `--depot-cible DOSSIER` | Clone d'outils-travail à utiliser (défaut : celui qui contient le script) |
| `--supprimer` | Supprimer définitivement le dépôt source au lieu de l'archiver |
| `--archiver-seulement DEPOT…` | Archiver des dépôts déjà intégrés, sans rien intégrer |
| `--supprimer-seulement DEPOT…` | Supprimer définitivement des dépôts déjà intégrés |

Le script **n'adapte pas le code** : après l'intégration, il reste à passer
les chemins et paramètres d'étude dans un `config_exemple.toml` lu avec
`--config`, à écrire le README de l'outil et à l'ajouter au README racine,
comme pour les autres outils.

## Supprimer les dépôts sources

```bash
# à l'intégration, à la place de l'archivage
python outils/maintenance/integrer_depot/integrer_depot.py Ono-coder/mon_depot outils/mon_outil --supprimer

# dépôts déjà intégrés
python outils/maintenance/integrer_depot/integrer_depot.py --supprimer-seulement Ono-coder/depot1 Ono-coder/depot2
```

La suppression est **définitive** : code, historique, issues. Les fichiers
écartés à l'intégration ne sont plus nulle part : anciennes versions,
documents, fichiers bloqués pour secrets. Pour chaque dépôt, le script
demande de **retaper son nom complet**. Une réponse vide ou différente laisse
le dépôt intact. À l'intégration, la suppression n'a lieu qu'après un push
réussi.

`gh` doit avoir le droit de suppression, à accorder une fois :

```bash
gh auth refresh -h github.com -s delete_repo
```

Archiver reste préférable quand le dépôt contient des fichiers non repris
dans outils-travail : un dépôt archivé est en lecture seule, mais peut être
désarchivé.

## Hooks git

Une fois installés, chaque `git commit` dans outils-travail est refusé si :
- un fichier indexé contient un terme personnel, un secret ou un chemin
  `C:\Users\<nom>` (métadonnées des fichiers Office comprises) ;
- l'auteur ou le committer contient un terme personnel (adresse
  personnelle…) ;
- le message de commit en contient un (ligne `Signed-off-by` par exemple).

Sans fichier d'anonymisation, seuls les secrets et les chemins de profil sont
contrôlés. En cas de faux positif avéré : `git commit --no-verify`.

Les hooks ne s'appliquent qu'aux commits faits depuis un clone où ils sont
installés : une modification faite directement sur le site GitHub n'est pas
contrôlée.
