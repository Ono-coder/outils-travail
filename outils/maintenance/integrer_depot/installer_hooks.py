"""
installer_hooks.py

Active les hooks de contrôle d'anonymat dans le clone local d'outils-travail
(à lancer une fois par clone) :

    python outils/maintenance/integrer_depot/installer_hooks.py
"""

import subprocess
from pathlib import Path

hooks = Path(__file__).resolve().parent / "hooks"
racine = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=hooks,
                             capture_output=True, text=True, check=True).stdout.strip())
chemin = hooks.relative_to(racine).as_posix()
subprocess.run(["git", "config", "core.hooksPath", chemin], cwd=racine, check=True)
print(f"Hooks activés : core.hooksPath = {chemin}")
