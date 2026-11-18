"""Fonction pure d'un module JavaScript du navigateur, essayée dans Node sans charger le module (qui importe le DOM)."""

import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def source_of(path, name):
    """Texte de `function name(…) { … }` (de la colonne 0 à la première « } » seule sur sa ligne) dans static/js/<path>."""
    text = (ROOT / "static/js" / path).read_text(encoding="utf-8")
    match = re.search(rf"^(?:async )?function {name}\(.*?^}}$", text, re.S | re.M)
    if match is None:
        raise LookupError(name)
    return match.group(0)


def run(path, name, expression):
    """Valeur JSON de `expression`, évaluée avec la fonction `name` de static/js/<path> à portée."""
    code = source_of(path, name) + f"\nconsole.log(JSON.stringify({expression}));"
    done = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)
