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

    def test_code_branches_and_names(self):
        self.assertEqual(git(self.a.root, "symbolic-ref", "--short", "HEAD").decode().strip(), "main")
        remotes.configure(self.a, str(self.remote))
        self.save(self.a, "code.txt", "one")
        self.save(self.a, "code.txt", "two")
        tip = git(self.remote, "rev-parse", "refs/heads/main")
        self.assertEqual(git(self.remote, "show", "main:code.txt"), b"two")
        self.a.load("S0-v1.0000")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/main"), tip)
        self.save(self.a, "code.txt", "alternate")
        self.assertEqual(git(self.remote, "show", "luple/S1:code.txt"), b"alternate")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/main"), tip)
        remotes.configure_branch(self.a, "S1", "payment")
        remotes.sync(self.a)
        self.assertEqual(git(self.remote, "show", "payment:code.txt"), b"alternate")
        with self.assertRaises(LupleError):
            remotes.configure_branch(self.a, "S1", "main")
        with self.assertRaises(LupleError):
            remotes.configure_branch(self.a, "S1", "luple/state")

    def test_connection_empty_remote_replaces_legacy_master(self):
        remotes.configure_branch(self.a, "S0", "master")
        result = remotes.configure(self.a, str(self.remote))
        self.assertIn("S0 → main", result)
        self.save(self.a, "code", "local")
        self.assertEqual(git(self.remote, "show", "main:code"), b"local")
        self.assertFalse(git(self.remote, "show-ref", "--verify", "refs/heads/master", check=False))

    def test_connection_existing_default_requires_review_then_syncs(self):
        self.save(self.a, "remote.txt", "remote")
        remotes.send(self.a, str(self.remote), "trunk", "1")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
        self.save(self.b, "local.txt", "local")
        remotes.configure_branch(self.b, "S0", "master")
        result = remotes.configure(self.b, str(self.remote))
        self.assertIn("통합이 필요", result)
        self.assertEqual(self.b.read()["lines"]["S0"]["branch"], "trunk")
        before = git(self.remote, "rev-parse", "trunk")
        result = remotes.safe_sync(self.b)
        self.assertIn("lu i connect", result)
        self.assertNotIn("다시 시도", result)
        self.assertEqual(git(self.remote, "rev-parse", "trunk"), before)
        integration.connect(self.b)
        self.assertFalse((self.b.root / "remote.txt").exists())
        integration.finish(self.b, "reviewed")
        self.assertEqual(git(self.remote, "show", "trunk:remote.txt"), b"remote")
        self.assertEqual(git(self.remote, "show", "trunk:local.txt"), b"local")

    def test_connection_compatible_history_and_legacy_upgrade(self):
        self.save(self.a, "code", "one")
        remotes.send(self.a, str(self.remote), "main", "1")
        self.save(self.a, "code", "two")
        with self.a.lock():
            state = self.a.read()
            state["lines"]["S0"]["branch"] = "master"
            state["config"]["personal_remote"] = str(self.remote)
            state.pop("remote_connection", None)
            self.a.write(state, "legacy-fixture")
        remotes.sync(self.a)
        self.assertEqual(git(self.remote, "show", "main:code"), b"two")
        self.assertEqual(self.a.read()["lines"]["S0"]["branch"], "main")
        remotes.configure_branch(self.a, "S0", "custom")
        remotes.sync(self.a)
        self.assertEqual(self.a.read()["lines"]["S0"]["branch"], "custom")

    def test_save_shows_remote_address_after_successful_sync(self):
        remotes.configure(self.a, str(self.remote))
        output = self.save(self.a, "readme.md", "saved")
        self.assertIn("개인 원격 동기화 완료", output)
        self.assertIn("▶ GitHub에서 확인: " + str(self.remote), output)

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

    def test_guided_conflict_review_and_resume(self):
        from luple import conflicts
        self.save(self.a, "code", "base\n")
        self.save(self.a, "code", "left\n")
        self.a.load("1"); self.save(self.a, "code", "right\n")
        integration.prepare(self.a, identifier="2")
        self.assertEqual(conflicts.unresolved(self.a), ["code"])
        self.assertIn("left", conflicts.compare(self.a, "code"))
        with self.assertRaises(LupleError): conflicts.resolve(self.a, "code", "manual")
        conflicts.resolve(self.a, "code", "theirs")
        self.assertEqual(conflicts.unresolved(self.a), [])
        self.assertEqual((self.a.root / "code").read_text(), "right\n")
        self.assertIn("보존", conflicts.review(self.a, picker=lambda *args: None))
        choices = iter([1, 0])
        conflicts.review(self.a, picker=lambda *args: next(choices))
        self.assertEqual((self.a.root / "code").read_text(), "left\n")
        self.assertFalse(integration.pending_path(self.a).exists())

    def test_conflict_binary_and_delete_choices(self):
        from luple import conflicts
        (self.a.root / "그림.bin").write_bytes(b"\x00base")
        self.save(self.a, "remove.txt", "base")
        (self.a.root / "그림.bin").write_bytes(b"\x00left")
        (self.a.root / "remove.txt").unlink()
        self.a.save("delete and binary")
        self.a.load("1")
        (self.a.root / "그림.bin").write_bytes(b"\x00right")
        self.save(self.a, "remove.txt", "changed")
        integration.prepare(self.a, identifier="2")
        self.assertEqual(set(conflicts.unresolved(self.a)), {"그림.bin", "remove.txt"})
        self.assertIn("바이너리", conflicts.compare(self.a, "그림.bin"))
        conflicts.resolve(self.a, "그림.bin", "ours")
        conflicts.resolve(self.a, "remove.txt", "theirs")
        self.assertEqual(conflicts.unresolved(self.a), [])
        integration.finish(self.a, resolved=True)
        self.assertEqual((self.a.root / "그림.bin").read_bytes(), b"\x00right")
        self.assertFalse((self.a.root / "remove.txt").exists())

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
