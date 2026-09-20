import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
import shutil
import stat
from unittest.mock import patch

from luple.core import LupleError, Repository, git


class LupleTests(unittest.TestCase):
    def setUp(self):
        self.test_parent = Path(os.environ.get("LUPLE_TEST_ROOT", tempfile.gettempdir())).resolve()
        self.root = self.test_parent / ("luple-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.repo = Repository.initialize(self.root)

    def tearDown(self):
        assert self.root.resolve().parent == self.test_parent
        assert self.root.name.startswith("luple-test-")
        def writable(function, path, error):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            function(path)
        shutil.rmtree(self.root, onerror=writable)

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_branch_futures_and_original_future_survive(self):
        for value in ("one", "two", "three"):
            self.put("code.txt", value)
            self.repo.save(value)
        self.repo.load("2")
        self.put("code.txt", "alternate")
        self.repo.save("다른 미래")
        state = self.repo.read()
        self.assertEqual(state["lines"]["S0"]["head"], "3")
        self.assertEqual(state["lines"]["S1"]["head"], "4")
        self.assertIn("#2 [FORK]", self.repo.history())
        self.assertIn("S0: #3", self.repo.futures("2"))
        self.assertIn("S1: #4", self.repo.futures("2"))
        self.repo.load("3", "S0")
        self.assertEqual((self.root / "code.txt").read_text(), "three")
        self.repo.load("4", "S1")
        self.assertEqual((self.root / "code.txt").read_text(), "alternate")

    def test_dirty_new_deleted_and_binary_files_recover(self):
        self.put("old.txt", "initial")
        self.repo.save("base")
        (self.root / "old.txt").unlink()
        (self.root / "binary.bin").write_bytes(bytes(range(256)))
        self.put("한글 경로/new.txt", "복원")
        self.repo.load("0")
        self.assertFalse((self.root / "binary.bin").exists())
        self.repo.recover_temp("T1")
        self.assertFalse((self.root / "old.txt").exists())
        self.assertEqual((self.root / "binary.bin").read_bytes(), bytes(range(256)))
        self.assertEqual((self.root / "한글 경로/new.txt").read_text(encoding="utf-8"), "복원")

    def test_ignored_files_remain_and_collision_is_rejected(self):
        self.put("secret.txt", "versioned")
        self.repo.save("tracked secret")
        self.repo.load("0")
        self.put(".gitignore", "secret.txt\n")
        self.put("secret.txt", "private local content")
        with self.assertRaisesRegex(LupleError, "Protected path"):
            self.repo.load("1")
        self.assertEqual((self.root / "secret.txt").read_text(), "private local content")
        self.assertEqual(self.repo.read()["current"]["save"], "0")

    def test_temp_is_not_regular_save_and_no_change_does_not_fork(self):
        self.put("x", "one")
        self.repo.save("one")
        self.repo.load("0")
        self.assertEqual(self.repo.save("empty"), "No changes to save.")
        self.repo.temp()
        self.assertEqual(len(self.repo.read()["lines"]), 1)
        with self.assertRaises(LupleError):
            self.repo.load("T1")

    def test_git_head_and_staging_unchanged(self):
        self.put("file", "staged")
        git(self.root, "add", "file")
        index_before = git(self.root, "ls-files", "--stage")
        self.put("file", "working")
        head_before = git(self.root, "symbolic-ref", "HEAD")
        self.repo.save("working")
        self.repo.load("0")
        self.assertEqual(git(self.root, "ls-files", "--stage"), index_before)
        self.assertEqual(git(self.root, "symbolic-ref", "HEAD"), head_before)

    def test_observe_and_futures_do_not_change_files(self):
        self.put("x", "first")
        self.repo.save("first")
        self.put("x", "dirty")
        self.assertIn("first", self.repo.observe("1"))
        self.repo.futures("0")
        self.assertEqual((self.root / "x").read_text(), "dirty")

    def test_autosave_deduplicates_and_clean_retains_12(self):
        self.assertIsNone(self.repo.autosave())
        for n in range(14):
            self.put("x", str(n))
            self.repo.autosave()
        self.assertIsNone(self.repo.autosave())
        self.repo.clean()
        self.assertEqual(len(self.repo.read()["temps"]), 12)
        self.assertNotIn("T1", self.repo.read()["temps"])

    def test_interrupted_load_recovers(self):
        self.put("x", "saved")
        self.repo.save("base")
        self.put("x", "unsaved")
        real_restore = self.repo.restore

        def fail_after_restore(source, target):
            real_restore(source, target)
            raise OSError("simulated failure")

        with patch.object(self.repo, "restore", side_effect=fail_after_restore):
            with self.assertRaises(LupleError):
                self.repo.load("0")
        with self.assertRaisesRegex(LupleError, "interrupted"):
            self.repo.read()
        self.repo.recover()
        self.assertEqual((self.root / "x").read_text(), "unsaved")
        self.assertEqual(self.repo.read()["current"]["save"], "1")

    def test_exclusive_lock(self):
        with self.repo.lock():
            with self.assertRaisesRegex(LupleError, "running"):
                self.repo.save("blocked")

    def test_status_uses_luple_position_not_git_head(self):
        self.put("x", "saved")
        self.repo.save("saved")
        self.assertIn("No unsaved changes", self.repo.status())
        self.put("x", "dirty")
        self.assertIn("M\tx", self.repo.status())

    def test_pending_git_merge_is_rejected(self):
        marker = self.repo.directory.parent / "MERGE_HEAD"
        marker.write_text("0" * 40)
        try:
            with self.assertRaisesRegex(LupleError, "active Git"):
                self.repo.save("blocked")
        finally:
            marker.unlink()

    def test_existing_git_repository_initial_state(self):
        self.put("base", "committed")
        git(self.root, "add", "base")
        git(self.root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "existing history")
        original = git(self.root, "rev-parse", "HEAD").decode().strip()
        # Remove only metadata created by this test's setup, then initialize
        # against the real existing Git commit.
        self.repo.path.unlink()
        self.repo = Repository.initialize(self.root)
        self.put("base", "changed")
        self.repo.save("change")
        self.repo.load("0")
        self.assertEqual((self.root / "base").read_text(), "committed")
        self.assertEqual(git(self.root, "rev-parse", "HEAD").decode().strip(), original)

    def test_cli_end_to_end(self):
        import sys
        source = str(Path(__file__).resolve().parents[1] / "lu.py")
        self.put("hello", "world")
        for args in (["s", "hello"], ["h", "--list"], ["l", "--list"], ["status"], ["h", "--show", "1"], ["sys", "autosave", "list"]):
            result = subprocess.run([sys.executable, source, "-C", str(self.root), *args], capture_output=True, env={**os.environ, "LUPLE_NO_WORKER": "1"})
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_menu_browse_cancel_does_not_modify_dirty_work(self):
        from luple.menu import browse
        self.put("x", "saved")
        self.repo.save("saved")
        self.put("x", "unsaved")
        before = self.repo.path.read_bytes()
        selections = iter([0, 1, 0, None, None])
        result = browse(self.repo, picker=lambda *args: next(selections))
        self.assertIn("닫았습니다", result)
        self.assertEqual(self.repo.path.read_bytes(), before)
        self.assertEqual((self.root / "x").read_text(), "unsaved")

    def test_menu_choose_future_and_load(self):
        from luple.menu import browse
        for value in ("one", "two", "three"):
            self.put("x", value)
            self.repo.save(value)
        self.repo.load("2")
        self.put("x", "alternate")
        self.repo.save("alternate")
        screens = []
        choices = iter([1, 2, 0, 0])  # #2 -> original S0 future -> #3 -> load
        def picker(title, options, subtitle):
            screens.append((title, options))
            return next(choices)
        browse(self.repo, picker=picker)
        self.assertTrue(any("갈림길" in row for _, rows in screens for row in rows))
        self.assertEqual(self.repo.read()["current"], {"line": "S0", "save": "3"})
        self.assertEqual((self.root / "x").read_text(), "three")


if __name__ == "__main__":
    unittest.main()
