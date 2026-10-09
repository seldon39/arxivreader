"""Chemins et réglages fixes de l'application."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
LIBRARY = ROOT / "library"
PAPERS = LIBRARY / "papers"
DB_PATH = LIBRARY / "arxivreader.db"

HOST = "127.0.0.1"
PORT = 8710

USER_AGENT = "arXivReader/0.1 (personal reader)"

# Catégories suivies dans le feed (étape 2).
FEED_CATEGORIES = ["math.DG", "math.CV", "math.SP", "math-ph"]
