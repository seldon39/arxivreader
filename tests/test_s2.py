"""Semantic Scholar : une requête par seconde au plus pour toute l'application (app/s2.py)."""

import threading
import unittest

from app import s2


class Pace(unittest.TestCase):
    def setUp(self):
        s2._next = 0.0

    def test_requests_are_spaced(self):
        # trois requêtes demandées d'un coup : elles partent à 0, INTERVAL et 2 × INTERVAL
        waits = []
        for _ in range(3):
            s2.pace(sleep=waits.append)
        self.assertEqual(len(waits), 2)
        self.assertAlmostEqual(waits[0], s2.INTERVAL, delta=0.05)
        self.assertAlmostEqual(waits[1], 2 * s2.INTERVAL, delta=0.05)

    def test_threads_share_the_pace(self):
        # depuis plusieurs fils (papiers liés, feed, revues…) : chacun sa place, aucune à moins d'INTERVAL d'une autre
        waits, lock = [], threading.Lock()
        def ask():
            def record(seconds):
                with lock:
                    waits.append(seconds)
            s2.pace(sleep=record)
        threads = [threading.Thread(target=ask) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        slots = sorted([0.0, *waits])
        self.assertEqual(len(slots), 4)
        self.assertTrue(all(b - a >= s2.INTERVAL - 0.05 for a, b in zip(slots, slots[1:])), slots)

    def test_paced_waits_before_each_attempt(self):
        # with_retry rappelle `send` à chaque essai : chaque essai attend son tour
        calls, waits = [], []
        send = s2.paced(lambda: calls.append(1) or len(calls), "a-key", sleep=waits.append)
        self.assertEqual((send(), send()), (1, 2))
        self.assertEqual(len(waits), 1)

    def test_without_a_key_nothing_changes(self):
        # sans clé : la requête telle quelle, sans attente commune ni place réservée (chaque module garde ses pauses)
        send = lambda: "answer"  # noqa: E731
        self.assertIs(s2.paced(send, None), send)
        self.assertIs(s2.paced(send, ""), send)
        self.assertEqual(s2._next, 0.0)


if __name__ == "__main__":
    unittest.main()
