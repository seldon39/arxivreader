"""Contrastes (WCAG 2) des couleurs de static/css/app.css employées pour du texte : 4,5:1 sur leur fond réel."""

import pathlib
import re
import unittest

CSS = (pathlib.Path(__file__).resolve().parent.parent / "static/css/app.css").read_text(encoding="utf-8")


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def mix(color, over, share):
    """color-mix(in srgb, color share%, over)."""
    return "#" + "".join(f"{round(int(color[i:i + 2], 16) * share + int(over[i:i + 2], 16) * (1 - share)):02x}" for i in (1, 3, 5))


def light(name):
    """Première valeur de la variable, celle du thème clair (bloc :root)."""
    return re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", CSS).group(1)


class Contrast(unittest.TestCase):
    def test_conjecture_and_problem_text_in_light_theme(self):
        # .kind-filter[aria-pressed="true"] : texte de la couleur sur 10 % d'elle-même sur la surface ;
        # .reason-keyword : fond à 12 %
        for name in ("k-conjecture", "k-problem"):
            color = light(name)
            for share in (0.10, 0.12):
                self.assertGreaterEqual(contrast(color, mix(color, light("surface"), share)), 4.5, f"{name} {share}")
            self.assertGreaterEqual(contrast(color, light("surface")), 4.5, name)

    def test_muted_text(self):
        for background in ("surface", "surface-soft", "surface-hover", "bg"):
            self.assertGreaterEqual(contrast(light("muted"), light(background)), 4.5, background)


if __name__ == "__main__":
    unittest.main()
