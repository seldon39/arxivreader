"""TeX des titres, résumés et réponses (static/js/math.js) : le MathJax livré, exécuté avec Node si présent."""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAYER = "position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:2147483647"


@unittest.skipUnless(shutil.which("node"), "Node absent")
class TexStyles(unittest.TestCase):
    def render(self, *formulas):
        done = subprocess.run(["node", str(ROOT / "tests/mathjax_in_node.mjs"), json.dumps(formulas)],
                              capture_output=True, text=True, timeout=120, check=True)
        return json.loads(done.stdout.splitlines()[-1])

    def styles(self, html):
        return " ".join(re.findall(r'style="([^"]*)"', html))

    def test_no_free_css_from_the_tex(self):
        frame, font = self.render(rf"\fcolorbox{{red;{LAYER};opacity:0}}{{white}}{{x}}",
                                  rf"\unicode[1,1][x;{LAYER};background:url(data:image/svg+xml,%3Csvg%3E%3C/svg%3E)]{{x41}}")
        for html in (frame, font):
            self.assertNotIn("fixed", self.styles(html))
            self.assertNotIn("100vw", self.styles(html))
            self.assertNotIn("url(", self.styles(html))
        self.assertIn("mjx-c78", frame)  # la boîte et son contenu restent
        self.assertIn("mjx-c41", font)

    def test_colours_and_frames_kept(self):
        frame, background, colour, boxed = self.render(r"\fcolorbox{red}{yellow}{x}", r"\colorbox{yellow}{x}",
                                                       r"\color{red}{x}", r"\boxed{x}")
        self.assertIn("border: 2px solid red", self.styles(frame))
        self.assertIn("background-color: yellow", self.styles(frame))
        self.assertIn("background-color: yellow", self.styles(background))
        self.assertIn("color: red", self.styles(colour))
        self.assertIn("border: 0.067em solid", self.styles(boxed))


if __name__ == "__main__":
    unittest.main()
