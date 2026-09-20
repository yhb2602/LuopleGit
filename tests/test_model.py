import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
import threading
import time
from unittest.mock import patch

from luple.model import Repository
from luple.core import LupleError, git
from luple.ui import browse


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(os.environ.get("LUPLE_TEST_ROOT", tempfile.gettempdir())).resolve()
        self.root = self.parent / ("model-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.repo = Repository.open(self.root)

    def tearDown(self):
        assert self.root.resolve().parent == self.parent and self.root.name.startswith("model-test-")
        def writable(fn, p, exc):
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            fn(p)
        shutil.rmtree(self.root, onerror=writable)

    def save(self, value):
        (self.root / "code.txt").write_text(value, encoding="utf-8")
        return self.repo.save(value)

    def test_labels_metadata_branch_and_history(self):
        for key, value in {"org": "LuCre", "project": "luple-kernel", "branch": "PayMain", "initials": "HB", "name": "HB", "email": "hb@example.invalid"}.items():
            self.repo.configure(key, value)
        self.save("첫 저장")
        self.save("기존 미래")
        self.repo.load("S0-v1.0000")
        self.save("새 미래")
        state = self.repo.read()
        self.assertEqual(self.repo.label(state, "3"), "S1-v1.0001")
        self.assertEqual(state["lines"]["S0"]["head"], "2")
        message = git(self.root, "show", "-s", "--format=%B", state["saves"]["3"]["commit"]).decode()
        self.assertIn("[LuCre/luple-kernel/v1.0/PayMain/HB] (S1-v1.0001) 새 미래", message)
        self.assertIn("◆", self.repo.markers(state, "1"))
        self.assertIn("●", self.repo.markers(state, "2"))
        history = self.repo.history()
        self.assertLess(history.index("새 미래"), history.index("기존 미래"))
        self.repo.load("S0-v1.0001")
        self.save("계속")
        self.assertEqual(self.repo.label(self.repo.read(), "4"), "S0-v1.0002")

    def test_favorites_limit_replace_and_delete_lifecycle(self):
        for n in range(5): self.save(str(n))
        for n in ("1", "2", "3"): self.repo.favorite(n)
        with self.assertRaises(LupleError): self.repo.favorite("4")
        self.repo.favorite("4", replace="1")
        self.assertEqual(self.repo.favorites(self.repo.read()), ["2", "3", "4"])
        self.repo.trash("2")
        with self.assertRaises(LupleError): self.repo.load("2")
        self.assertNotIn("2", self.repo.history_ids(self.repo.read()))
        self.repo.trash("2", "restore")
        self.repo.load("2")
        with self.assertRaises(LupleError): self.repo.trash("2")
        self.repo.load("5")
        self.repo.trash("2")
        with self.assertRaises(LupleError): self.repo.trash("2", "purge", "incorrect")
        self.repo.trash("2", "purge", "S0-v1.0001")
        with self.assertRaises(LupleError): self.repo.trash("2", "restore")
        self.repo.load("4")  # Descendants remain intact after purging the parent.
        self.save("still branches")
        self.assertEqual(self.repo.read()["current"]["line"], "S1")

    def test_menu_cancel_and_history_load_confirmation(self):
        self.save("one"); self.save("two")
        (self.root / "code.txt").write_text("dirty")
        before = self.repo.path.read_bytes()
        choices = iter([0, None, None])
        browse(self.repo, picker=lambda *a: next(choices))
        self.assertEqual(before, self.repo.path.read_bytes())
        choices = iter([1, 0, 1, None, None])
        browse(self.repo, history=True, picker=lambda *a: next(choices))
        self.assertEqual((self.root / "code.txt").read_text(), "dirty")
        choices = iter([1, 0, 0])
        with patch.dict(os.environ, {"LUPLE_NO_WORKER": "1"}):
            browse(self.repo, history=True, picker=lambda *a: next(choices))
        self.assertEqual((self.root / "code.txt").read_text(), "one")

    def test_auto_config_rejects_invalid_and_prunes_only_autosaves(self):
        self.repo.configure("autosave_keep", 1)
        self.save("base")
        (self.root / "code.txt").write_text("temp1")
        self.repo.autosave()
        self.repo.temp("manual recovery")
        (self.root / "code.txt").write_text("temp2")
        self.repo.autosave()
        entries = list(self.repo.read()["temps"].values())
        self.assertEqual(len(entries), 2)
        self.assertTrue(any(e["message"] == "manual recovery" for e in entries))
        with self.assertRaises(LupleError): self.repo.configure("autosave_interval", 0)
        self.repo.configure("autosave_enabled", "off")
        self.assertIsNone(self.repo.autosave())

    def test_counter_overflow_and_no_change(self):
        self.save("one")
        state = self.repo.read()
        state["saves"]["1"]["step"] = 10000
        self.assertEqual(self.repo.label(state, "1"), "S0-v2.0000")
        self.repo.load("0")
        self.assertIn("변경사항이 없습니다", self.repo.save("empty"))
        self.assertEqual(len(self.repo.read()["lines"]), 1)

    def test_foreground_waits_for_short_autosave_lock(self):
        from luple.core import Repository as Storage
        acquired = threading.Event()
        def hold():
            with Storage.lock(self.repo):
                acquired.set()
                time.sleep(.3)
        worker = threading.Thread(target=hold)
        worker.start()
        self.assertTrue(acquired.wait(2))
        try:
            self.repo.configure("autosave_enabled", "off")
        finally:
            worker.join()
        self.assertFalse(self.repo.read()["config"]["autosave_enabled"])

    def test_cli_new_commands(self):
        source = str(Path(__file__).resolve().parents[1] / "lu.py")
        self.save("hello")
        for args in (["h", "--list"], ["l", "--list"], ["h", "--show", "S0-v1.0000"], ["sys", "config"], ["sys", "autosave", "off"], ["sys", "delete"], ["review"]):
            result = subprocess.run([sys.executable, source, "-C", str(self.root), *args], capture_output=True, env={**os.environ, "LUPLE_NO_WORKER": "1"})
            self.assertEqual(result.returncode, 0, result.stderr)
