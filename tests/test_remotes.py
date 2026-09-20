import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
import uuid

from luple.core import git, LupleError
from luple.model import Repository
from luple import remotes, integration


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(os.environ.get("LUPLE_TEST_ROOT", tempfile.gettempdir())).resolve()
        self.root = self.parent / ("remote-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.remote = self.root / "remote.git"; self.remote.mkdir()
        git(self.remote, "init", "--bare")
        self.a = self.make("A")
        self.b = self.make("B")

    def make(self, name):
        folder = self.root / name; folder.mkdir()
        return Repository.open(folder)

    def tearDown(self):
        assert self.root.parent == self.parent and self.root.name.startswith("remote-test-")
        def writable(fn, path, exc):
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE); fn(path)
        shutil.rmtree(self.root, onerror=writable)

    def save(self, repo, name, content):
        (repo.root / name).write_text(content, encoding="utf-8")
        return repo.save(content)

    def test_personal_sync_load_fork_and_second_pc(self):
        remotes.configure(self.a, str(self.remote))
        self.assertIn("동기화 완료", self.save(self.a, "code.txt", "one"))
        self.save(self.a, "code.txt", "two")
        remotes.configure(self.b, str(self.remote))
        remotes.sync(self.b, push=False)
        self.assertFalse((self.b.root / "code.txt").exists())
        self.b.load("S0-v1.0000")
        self.assertEqual((self.b.root / "code.txt").read_text(), "one")
        self.assertIn("동기화 완료", self.save(self.b, "code.txt", "alternate"))
        remote_tip = git(self.remote, "rev-parse", "refs/heads/luple/state")
        remotes.sync(self.a, push=False)
        self.assertEqual((self.a.root / "code.txt").read_text(), "two")
        self.a.load("S1-v1.0001")
        self.assertEqual((self.a.root / "code.txt").read_text(), "alternate")
        self.a.load("S0-v1.0001")
        self.assertEqual((self.a.root / "code.txt").read_text(), "two")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/luple/state"), remote_tip)

    def test_two_offline_devices_keep_both_heads_without_growth(self):
        remotes.configure(self.a, str(self.remote)); self.save(self.a, "code", "base")
        remotes.configure(self.b, str(self.remote)); remotes.sync(self.b, False); self.b.load("S0-v1.0000")
        remotes.configure(self.a, "off"); remotes.configure(self.b, "off")
        self.save(self.a, "code", "A"); self.save(self.b, "code", "B")
        remotes.configure(self.a, str(self.remote)); remotes.sync(self.a)
        remotes.configure(self.b, str(self.remote)); remotes.sync(self.b)
        remotes.sync(self.a); remotes.sync(self.b)
        before = len(self.b.read()["lines"])
        remotes.sync(self.b); remotes.sync(self.a); remotes.sync(self.b)
        self.assertEqual(len(self.b.read()["lines"]), before)
        messages = [e["message"] for e in self.b.read()["saves"].values()]
        self.assertIn("A", messages); self.assertIn("B", messages)

    def test_network_failure_keeps_local_save(self):
        remotes.configure(self.a, str(self.root / "missing.git"))
        result = self.save(self.a, "x", "local")
        self.assertIn("동기화 대기", result)
        self.assertEqual(self.a.read()["current"]["save"], "1")

    def test_send_then_import_unrelated_project(self):
        self.save(self.a, "a.txt", "A")
        remotes.send(self.a, str(self.remote), "delivery", "1")
        self.save(self.b, "b.txt", "B")
        integration.prepare(self.b, str(self.remote), "delivery")
        self.assertFalse((self.b.root / "a.txt").exists())
        integration.finish(self.b, "combined")
        self.assertEqual((self.b.root / "a.txt").read_text(), "A")
        self.assertEqual((self.b.root / "b.txt").read_text(), "B")
        state = self.b.read(); entry = state["saves"][state["current"]["save"]]
        self.assertEqual(len(entry["merge_parents"]), 2)
        self.assertEqual(len(git(self.b.root, "show", "-s", "--format=%P", entry["commit"]).split()), 2)

    def test_conflict_preview_requires_resolution(self):
        self.save(self.a, "code", "base\n")
        self.save(self.a, "code", "left\n")
        self.a.load("1"); self.save(self.a, "code", "right\n")
        result = integration.prepare(self.a, identifier="2")
        self.assertIn("충돌", result)
        self.assertEqual((self.a.root / "code").read_text(), "right\n")
        with self.assertRaises(LupleError): integration.finish(self.a)
        with self.assertRaises(LupleError): integration.finish(self.a, resolved=True)
        data = json.loads(integration.pending_path(self.a).read_text())
        (Path(data["folder"]) / "code").write_text("resolved\n")
        integration.finish(self.a, resolved=True)
        self.assertEqual((self.a.root / "code").read_text(), "resolved\n")

    def test_send_rejects_non_fast_forward(self):
        self.save(self.a, "a", "one"); remotes.send(self.a, str(self.remote), "main", "1")
        self.save(self.b, "b", "two")
        with self.assertRaises(LupleError): remotes.send(self.b, str(self.remote), "main", "1")

    def test_abort_and_unsafe_address(self):
        self.save(self.a, "a", "one")
        self.save(self.a, "b", "two")
        integration.prepare(self.a, identifier="1")
        integration.abort(self.a)
        self.assertEqual((self.a.root / "b").read_text(), "two")
        self.assertFalse(integration.pending_path(self.a).exists())
        for bad in ("ext::arbitrary-command", "--upload-pack=command", "https://token:secret@example.com/repo.git"):
            with self.assertRaises(LupleError): remotes.address(bad)
