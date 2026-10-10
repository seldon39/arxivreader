#!/bin/bash
# Double-cliquer sur ce fichier connecte arXivReader à ton compte Claude (une seule fois).
# La page de connexion s'ouvre dans le navigateur ; ensuite, cette fenêtre peut être fermée.
exec "$(dirname "$0")/arxivreader" login
