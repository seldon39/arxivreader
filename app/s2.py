"""Semantic Scholar : une requête par seconde au plus pour toute l'application.

Avec une clé d'API, la limite (1 requête/s) est celle de l'utilisateur : la dépasser vaut un refus (429). Plusieurs
parties de l'application l'appellent, parfois en même temps (papiers liés, feed, pertinence, revues, citations,
premier lancement) : chaque requête avec une clé réserve ici sa place, au moins INTERVAL secondes après la
précédente, quel que soit le fil qui l'envoie. Sans clé, rien ne change : chaque module garde ses propres pauses
(quota partagé par tous les anonymes, que l'application ne peut pas compter).
"""

import threading
import time

from .config import USER_AGENT

GRAPH = "https://api.semanticscholar.org/graph/v1"
INTERVAL = 1.05  # secondes entre deux requêtes (limite d'une clé : 1 par seconde)
_lock = threading.Lock()
_next = 0.0  # instant (time.monotonic) à partir duquel la prochaine requête peut partir


def pace(sleep=time.sleep):
    """Attend la place de la prochaine requête (le verrou n'est tenu que le temps de la réserver) ; `sleep` : attente
    interruptible (construction de graphe annulable)."""
    global _next
    with _lock:
        now = time.monotonic()
        start = max(now, _next)
        _next = start + INTERVAL
    if start > now:
        sleep(start - now)


def paced(send, keyed, sleep=time.sleep):
    """`send` (une requête), précédée de son tour si elle part avec une clé (`keyed`) : pour with_retry, qui réessaie
    en rappelant `send`. Sans clé : `send` tel quel."""
    if not keyed:
        return send
    def run():
        pace(sleep)
        return send()
    return run


def headers(api_key=None):
    """En-têtes d'une requête à l'API Graph : User-Agent, et la clé de l'utilisateur s'il en a une."""
    return {"User-Agent": USER_AGENT, **({"x-api-key": api_key} if api_key else {})}
