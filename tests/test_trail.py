"""Historique de sauts et touches de lecture (static/js/trail.js) : fonctions pures, exécutées avec Node si présent."""

import json
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node absent")
class TrailHelpers(unittest.TestCase):
    def run_js(self, expression):
        code = f'import * as T from "{(ROOT / "static/js/trail.js").as_uri()}";\nconsole.log(JSON.stringify({expression}));'
        done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    def test_back_and_forward(self):
        result = self.run_js('''(() => {
          const t = new T.Trail(); const A = {kind:"html", id:"a", off:0}, B = {kind:"pdf", page:5, top:100}, C = {kind:"html", id:"c", off:0};
          t.visit(A); t.visit(B);
          const out = [];
          out.push(t.back(C));            // revient à B, C attend devant
          out.push(t.back(B));            // revient à A
          out.push(t.back(A));            // plus rien
          out.push(t.forward(A));         // B
          out.push(t.forward(B));         // C
          out.push(t.forward(C));
          return out; })()''')
        self.assertEqual([r and (r.get("id") or r.get("page")) for r in result], [5, "a", None, 5, "c", None])

    def test_visit_truncates_future_and_ignores_duplicates(self):
        result = self.run_js('''(() => {
          const t = new T.Trail(); const A = {kind:"html", id:"a", off:0}, B = {kind:"html", id:"b", off:0};
          t.visit(A); t.visit({...A, off: 10}); const n = t.past.length;
          t.back(B); const hadFuture = t.canForward; t.visit(B);
          return [n, hadFuture, t.canForward, t.canBack]; })()''')
        self.assertEqual(result, [1, True, False, True])

    def test_limit(self):
        self.assertEqual(self.run_js('(() => { const t = new T.Trail(); for (let i = 0; i < 100; i++) t.visit({kind:"pdf", page:i+1, top:0}); return t.past.length; })()'), 60)

    def test_same_place_needs_same_kind(self):
        self.assertFalse(self.run_js('T.samePlace({kind:"html", id:"a"}, {kind:"pdf", page:1, top:0})'))
        self.assertTrue(self.run_js('T.samePlace({kind:"pdf", page:3, top:10}, {kind:"pdf", page:3, top:30})'))
        self.assertFalse(self.run_js('T.samePlace({kind:"pdf", page:3, top:10}, {kind:"pdf", page:4, top:10})'))

    def test_keys(self):
        self.assertEqual(self.run_js('T.trailKey({altKey:true, key:"ArrowLeft"})'), -1)
        self.assertEqual(self.run_js('T.trailKey({altKey:true, key:"ArrowRight"})'), 1)
        # ⌘[ et ⌘] restent au menu de l'application Mac
        self.assertEqual(self.run_js('T.trailKey({metaKey:true, key:"[", code:"BracketLeft"})'), 0)
        self.assertEqual(self.run_js('T.trailKey({metaKey:true, code:"BracketRight"})'), 0)
        self.assertEqual(self.run_js('T.trailKey({altKey:true, shiftKey:true, key:"ArrowLeft"})'), 0)
        self.assertEqual(self.run_js('T.trailKey({ctrlKey:true, key:"ArrowLeft"})'), 0)
        self.assertEqual(self.run_js('T.trailKey({key:"["})'), 0)
        self.assertEqual(self.run_js('T.readingKey({key:"]"})'), "next")
        self.assertEqual(self.run_js('T.readingKey({key:"p"})'), "proof")
        self.assertEqual(self.run_js('T.readingKey({key:"j"})'), "next")
        self.assertEqual(self.run_js('T.readingKey({key:"[", altKey:true, shiftKey:true})'), "prev")  # AZERTY
        self.assertIsNone(self.run_js('T.readingKey({key:"p", altKey:true})'))
        self.assertIsNone(self.run_js('T.readingKey({key:"[", metaKey:true})'))
        self.assertIsNone(self.run_js('T.readingKey({key:"p", metaKey:true})'))
        self.assertIsNone(self.run_js('T.readingKey({key:"a"})'))
        self.assertIsNone(self.run_js('T.readingKey({key:"]", isComposing:true})'))

    def test_neighbour_statement(self):
        items = '[{id:"a",y:100},{id:"b",y:500},{id:"c",y:900}]'
        self.assertEqual(self.run_js(f"T.neighbourStatement({items}, 0, 1)")["id"], "a")
        self.assertEqual(self.run_js(f"T.neighbourStatement({items}, 512, 1)")["id"], "c")   # b est à 12 au-dessus de la ligne
        self.assertEqual(self.run_js(f"T.neighbourStatement({items}, 512, -1)")["id"], "a")  # pas b lui-même
        self.assertEqual(self.run_js(f"T.neighbourStatement({items}, 700, -1)")["id"], "b")
        self.assertIsNone(self.run_js(f"T.neighbourStatement({items}, 950, 1)"))
        self.assertIsNone(self.run_js(f"T.neighbourStatement({items}, 50, -1)"))
        # sauts rapides : on repart du dernier énoncé atteint, pas de la ligne de lecture
        self.assertEqual(self.run_js(f'T.neighbourStatement({items}, 0, 1, "b")')["id"], "c")
        self.assertEqual(self.run_js(f'T.neighbourStatement({items}, 0, -1, "b")')["id"], "a")
        self.assertIsNone(self.run_js(f'T.neighbourStatement({items}, 0, 1, "c")'))

    def test_proof_owner(self):
        # DOM minimal simulé : parentElement, id, contains et compareDocumentPosition
        result = self.run_js('''(() => {
          globalThis.Node = { DOCUMENT_POSITION_FOLLOWING: 4, DOCUMENT_POSITION_PRECEDING: 2 };
          const order = [];
          const mk = (id, parent = null) => { const e = { id, parentElement: parent, i: order.length, contains(x) { for (let p = x; p; p = p.parentElement) if (p === e) return true; return false; },
            compareDocumentPosition(x) { return x.i > e.i ? 4 : 2; } }; order.push(e); return e; };
          const proof = mk("pf"), inner = mk("p1", proof), a = mk("x1"), b = mk("x2"), c = mk("x3");
          const owners = new Map([["pf", "thm1"]]);
          const passages = [{ node: "thm2", start: a, end: b }];
          return [T.proofOwnerOf(inner, owners, passages), T.proofOwnerOf(b, owners, passages), T.proofOwnerOf(c, owners, passages), T.proofOwnerOf(a, owners, [])]; })()''')
        self.assertEqual(result, ["thm1", "thm2", None, None])


if __name__ == "__main__":
    unittest.main()
