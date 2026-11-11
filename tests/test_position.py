"""Position de lecture : valeurs anciennes et nouvelles, route d'enregistrement, assistant JS (si Node est là)."""

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from fastapi.testclient import TestClient

from app import db, main

BASE = f"http://{main.HOST}:{main.PORT}"
ROOT = pathlib.Path(__file__).resolve().parent.parent


class PositionRoute(unittest.TestCase):
    def setUp(self):
        self.saved = (db.DB_PATH, db.LIBRARY)
        tmp = pathlib.Path(tempfile.mkdtemp())
        db.DB_PATH, db.LIBRARY = tmp / "test.db", tmp
        db.init()
        db.save_paper({"id": "1003.2523", "arxiv_id": "1003.2523", "title": "T", "authors": [], "abstract": "", "categories": []})
        self.client = TestClient(main.app, base_url=BASE)

    def tearDown(self):
        db.DB_PATH, db.LIBRARY = self.saved

    def put(self, anchor):
        headers = {"Content-Type": "application/json"}
        response = self.client.put("/api/papers/1003.2523/position", json={"anchor": anchor}, headers=headers)
        self.assertEqual(response.status_code, 200)
        return db.get_paper("1003.2523")["position"]

    def test_old_and_new_formats_are_stored_as_is(self):
        for value in ("S3.Thm1", "S3.Thm1@120", "pdf:47:312.5", "pdf:12:"):
            self.assertEqual(self.put(value), value)

    def test_value_is_truncated(self):
        self.assertEqual(len(self.put("x" * 500)), 200)


@unittest.skipUnless(shutil.which("node"), "Node absent")
class PositionHelpers(unittest.TestCase):
    def run_js(self, expression):
        code = f'import {{ decodePosition, encodePosition, encodePdfPosition, positionSaver }} from "{ROOT / "static/js/position.js"}";\n' \
               f"console.log(JSON.stringify({expression}));"
        done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_round_trip(self):
        self.assertEqual(self.run_js('decodePosition(encodePosition("S1.p2", 120.4))'), {"id": "S1.p2", "offset": 120})
        self.assertEqual(self.run_js('encodePosition("S1.p2", 0)'), "S1.p2")
        self.assertEqual(self.run_js('decodePosition(encodePdfPosition(47, 312.54))'), {"pdf": {"page": 47, "top": 312.5}})
        self.assertEqual(self.run_js('decodePosition(encodePdfPosition(3, null))'), {"pdf": {"page": 3, "top": None}})

    def test_old_values(self):
        self.assertEqual(self.run_js('decodePosition("S3.Thm1")'), {"id": "S3.Thm1", "offset": 0})
        self.assertEqual(self.run_js('decodePosition("")'), None)
        self.assertEqual(self.run_js('decodePosition("a@b")'), {"id": "a@b", "offset": 0})

    def test_saver_flushes_once(self):
        result = self.run_js('(() => { const sent = []; const s = positionSaver((v) => sent.push(v), 60000);'
                             ' s.save("a"); s.save("b"); s.flush(); s.flush(); s.save("b"); s.flush(); return sent; })()')
        self.assertEqual(result, ["b"])


if __name__ == "__main__":
    unittest.main()
