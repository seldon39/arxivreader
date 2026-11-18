"""Scripts de publication (macos/release.sh, macos/build-beta.sh) : les garde-fous refusent avant toute construction.

Chaque test monte un petit dépôt (origine locale, `gh` et `build.sh` factices) ; `build.sh` laisse un fichier
`built` : s'il existe, le script est allé trop loin.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


# Scripts du Mac : sous Windows, le bash de Git ne lit pas un PATH séparé par « : » ni .venv/bin (CI de la 1.14.11)
@unittest.skipIf(sys.platform == "win32", "scripts de publication macOS")
@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "git et bash requis")
class Scripts(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        self.work = self.tmp / "work"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text("#!/bin/sh\nexit 0\n")
        gh.chmod(0o755)
        self.git(self.tmp, "init", "-q", "-b", "main", "--bare", str(self.origin))
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.work))
        (self.work / "macos").mkdir()
        (self.work / "app").mkdir()
        for name in ("release.sh", "build-beta.sh"):
            shutil.copy(ROOT / "macos" / name, self.work / "macos" / name)
        (self.work / "macos" / "release-footer.md").write_text("\n")
        build = self.work / "macos" / "build.sh"
        build.write_text('#!/bin/sh\ntouch "$(dirname "$0")/../built"\nmkdir -p dist; touch dist/arXivReader.dmg\n')
        build.chmod(0o755)
        (self.work / "dist").mkdir()
        (self.work / ".gitignore").write_text(".venv\ndist\nbuilt\n")
        python = self.work / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\necho OK\n")
        python.chmod(0o755)
        self.version("1.2.3")
        self.git(self.work, "add", ".")
        self.git(self.work, "commit", "-q", "-m", "départ")
        self.git(self.work, "push", "-q", "origin", "main")

    def git(self, cwd, *args):
        env = dict(os.environ, **GIT_ENV)
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout

    def version(self, number, commit=False):
        (self.work / "app" / "version.py").write_text(f'VERSION = "{number}"\n')
        if commit:
            self.git(self.work, "commit", "-q", "-am", "version")

    def run_script(self, name, *args):
        env = dict(os.environ, **GIT_ENV, PATH=f"{self.bin}:{os.environ['PATH']}", HOME=str(self.tmp / "home"))
        (self.tmp / "home").mkdir(exist_ok=True)
        return subprocess.run(["bash", f"macos/{name}", *args], cwd=self.work, env=env, capture_output=True, text=True)

    def built(self):
        return (self.work / "built").exists()

    def test_release_goes_through_when_everything_is_in_order(self):
        result = self.run_script("release.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.built())
        self.assertIn("v1.2.3", self.git(self.origin, "tag"))

    def test_release_refuses_when_origin_main_has_moved(self):
        other = self.tmp / "other"
        self.git(self.tmp, "clone", "-q", str(self.origin), str(other))
        (other / "x.txt").write_text("x")
        self.git(other, "add", ".")
        self.git(other, "commit", "-q", "-m", "ailleurs")
        self.git(other, "push", "-q", "origin", "main")
        result = self.run_script("release.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.built())
        self.assertEqual(self.git(self.origin, "tag"), "")

    def test_release_refuses_a_tag_that_exists_only_on_github(self):
        self.git(self.work, "tag", "-a", "v1.2.3", "-m", "x")
        self.git(self.work, "push", "-q", "origin", "v1.2.3")
        self.git(self.work, "tag", "-d", "v1.2.3")
        result = self.run_script("release.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.built())

    def test_release_refuses_a_beta_version(self):
        self.version("1.2.3-beta.1", commit=True)
        result = self.run_script("release.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.built())

    def test_beta_build_refuses_a_stable_version(self):
        self.git(self.work, "checkout", "-q", "-b", "beta")
        result = self.run_script("build-beta.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.built())

    def test_beta_build_refuses_the_main_branch(self):
        self.version("1.2.3-beta.1", commit=True)
        result = self.run_script("build-beta.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.built())

    def test_beta_build_starts_on_a_beta_branch_with_a_beta_version(self):
        self.git(self.work, "checkout", "-q", "-b", "beta")
        self.version("1.2.3-beta.1", commit=True)
        self.run_script("build-beta.sh")  # la suite (plist, codesign) échoue sans vraie application : seul le départ compte
        self.assertTrue(self.built())


if __name__ == "__main__":
    unittest.main()
