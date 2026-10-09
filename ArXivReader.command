#!/bin/bash
# Double-cliquer sur ce fichier dans le Finder lance arXivReader dans une fenêtre Terminal.
# Fermer la fenêtre (ou Ctrl+C) arrête l'application.
exec "$(dirname "$0")/arxivreader"
