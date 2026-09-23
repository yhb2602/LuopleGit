from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
import uuid


class LupleError(Exception):
    pass


def git_executable():
    explicit = os.environ.get("LUPLE_GIT")
    if explicit:
        candidate = shutil.which(explicit)
        if candidate:
            return candidate
        raise LupleError("LUPLE_GIT points to a missing Git executable: " + explicit)
    bundled = Path(__file__).resolve().parents[1] / "runtime/git/cmd/git.exe"
    if bundled.is_file():
        return str(bundled)
    installed = shutil.which("git")
    if installed:
        return installed
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe",
        Path.home() / "AppData/Local/Programs/Git/cmd/git.exe",
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise LupleError("Git executable not found. Install Git, reopen PowerShell, or set LUPLE_GIT to the full git.exe path.")


def git(cwd, *args, env=None, data=None, check=True):
    clean_env = {k: v for k, v in os.environ.items() if k not in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR")}
    process = subprocess.run(
        [git_executable(), "-C", str(cwd), *args], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env={**clean_env, **(env or {})},
    )
    if check and process.returncode:
        raise LupleError(process.stderr.decode("utf-8", "replace").strip())
    return process.stdout


def atomic_json(path, value):
    staging = path.with_suffix(".new")
    with staging.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staging, path)


class Repository:
    def __init__(self, cwd="."):
        self.root = Path(git(cwd, "rev-parse", "--show-toplevel").decode().strip())
        gitdir = Path(git(self.root, "rev-parse", "--absolute-git-dir").decode().strip())
        if git(self.root, "rev-parse", "--show-prefix").strip():
            raise LupleError("Cannot determine repository root")
        self.directory = gitdir / "luple"
        self.path = self.directory / "state.json"
        self.pending = self.directory / "pending.json"
        if git(self.root, "config", "--bool", "core.sparseCheckout", check=False).strip() == b"true":
            raise LupleError("Sparse checkout is not supported. Use a complete working tree.")

    @classmethod
    def initialize(cls, cwd="."):
        cwd = Path(cwd).resolve()
        if not (cwd / ".git").exists():
            git(cwd, "init", "--initial-branch=main")
        repo = cls(cwd)
        # Worktrees share refs but require an explicit cross-worktree protocol.
        common = git(repo.root, "rev-parse", "--git-common-dir").decode().strip()
        common_path = (repo.root / common).resolve()
        actual = Path(git(repo.root, "rev-parse", "--absolute-git-dir").decode().strip()).resolve()
        if common_path != actual:
            raise LupleError("Linked Git worktrees are not supported in v0.1.")
        repo.directory.mkdir(exist_ok=True)
        with repo.lock():
            if repo.path.exists():
                raise LupleError("Luople is already initialized.")
            if git(repo.root, "ls-files", "--stage").find(b"160000 ") >= 0:
                raise LupleError("Submodules are not supported in v0.1.")
            head = git(repo.root, "rev-parse", "--verify", "HEAD", check=False).decode().strip()
            if not head:
                tree = git(repo.root, "hash-object", "-t", "tree", "-w", "--stdin", data=b"").decode().strip()
                head = repo.commit(tree, None, "Luople initial empty state")
            git(repo.root, "update-ref", "refs/luple/saves/0", head)
            state = {"schema": 1, "next_save": 1, "next_temp": 1,
                     "current": {"line": "S0", "save": "0"},
                     "lines": {"S0": {"head": "0", "fork": None}},
                     "saves": {"0": {"commit": head, "parent": None, "line": "S0",
                                      "message": "Initial state", "time": time.time()}},
                     "temps": {}, "events": []}
            atomic_json(repo.path, state)
        return repo

    @contextlib.contextmanager
    def lock(self):
        if not self.directory.exists():
            raise LupleError("Run lu init first.")
        lock_path = self.directory / "lock"
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LupleError("Another lu operation is running. If it crashed, verify no lu process remains, then remove .git/luple/lock.")
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            lock_path.unlink(missing_ok=True)

    def read(self):
        if not self.path.exists():
            raise LupleError("Run lu init first.")
        if self.pending.exists():
            raise LupleError("An interrupted load needs recovery. Run lu recover.")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, state, action, **details):
        state["events"].append({"action": action, "time": time.time(), **details})
        atomic_json(self.path, state)

    @contextlib.contextmanager
    def index(self, base):
        path = self.directory / ("index-" + uuid.uuid4().hex)
        env = {"GIT_INDEX_FILE": str(path)}
        try:
            git(self.root, "read-tree", base, env=env)
            yield env
        finally:
            path.unlink(missing_ok=True)
            Path(str(path) + ".lock").unlink(missing_ok=True)

    def commit(self, tree, parent, message):
        env = {}
        for role in ("AUTHOR", "COMMITTER"):
            name = git(self.root, "config", "user.name", check=False).decode().strip() or "Luople Local"
            email = git(self.root, "config", "user.email", check=False).decode().strip() or "local@luople.invalid"
            env[f"GIT_{role}_NAME"] = name
            env[f"GIT_{role}_EMAIL"] = email
        args = ["commit-tree", tree]
        if parent:
            args += ["-p", parent]
        return git(self.root, *args, env=env, data=(message + "\n").encode()).decode().strip()

    def snapshot(self, parent, message):
        self.check_git_idle()
        with self.index(parent) as env:
            git(self.root, "add", "-A", "--", ".", env=env)
            tree = git(self.root, "write-tree", env=env).decode().strip()
        if b"160000 " in git(self.root, "ls-tree", "-r", tree):
            raise LupleError("Nested repositories/submodules are not supported.")
        return self.commit(tree, parent, message)

    def check_git_idle(self):
        gitdir = self.directory.parent
        if any((gitdir / name).exists() for name in (
            "index.lock", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"
        )) or git(self.root, "ls-files", "--unmerged"):
            raise LupleError("Finish the active Git operation/conflict before using Luople.")

    def save(self, message):
        with self.lock():
            state = self.read()
            current = state["current"]
            parent = state["saves"][current["save"]]["commit"]
            commit = self.snapshot(parent, message)
            if not git(self.root, "diff-tree", "--no-commit-id", "-r", parent, commit):
                return "No changes to save."
            line = current["line"]
            if state["lines"][line]["head"] != current["save"]:
                line = "S" + str(len(state["lines"]))
                state["lines"][line] = {"head": None, "fork": current["save"]}
            number = str(state["next_save"])
            git(self.root, "update-ref", f"refs/luple/saves/{number}", commit)
            state["saves"][number] = {"commit": commit, "parent": current["save"],
                                      "line": line, "message": message, "time": time.time()}
            state["next_save"] += 1
            state["lines"][line]["head"] = number
            state["current"] = {"line": line, "save": number}
            self.write(state, "save", save=number, line=line)
            return f"Saved #{number} [{line}]: {message}"

    def add_temp(self, state, message):
        base = state["current"].copy()
        parent = state["saves"][base["save"]]["commit"]
        commit = self.snapshot(parent, message)
        number = "T" + str(state["next_temp"])
        git(self.root, "update-ref", f"refs/luple/temps/{number}", commit)
        state["temps"][number] = {"commit": commit, "base": base,
                                  "message": message, "time": time.time()}
        state["next_temp"] += 1
        self.write(state, "temp", temp=number)
        return number

    def temp(self, message="Temporary save"):
        with self.lock():
            state = self.read()
            return self.add_temp(state, message)

    def status(self):
        with self.lock():
            state = self.read()
            current = state["current"]
            base = state["saves"][current["save"]]["commit"]
            with self.index(base) as env:
                git(self.root, "add", "-A", "--", ".", env=env)
                tree = git(self.root, "write-tree", env=env).decode().strip()
            summary = git(self.root, "diff", "--name-status", base, tree).decode("utf-8", "replace")
            return f"Current #{current['save']} [{current['line']}]\n" + (summary or "No unsaved changes.\n")

    def autosave(self):
        with self.lock():
            state = self.read()
            current = state["current"]
            base = state["saves"][current["save"]]["commit"]
            previous = next((t["commit"] for t in reversed(list(state["temps"].values())) if t["base"] == current), base)
            commit = self.snapshot(base, "Autosave")
            if not git(self.root, "diff-tree", "--no-commit-id", "-r", previous, commit):
                return None
            number = "T" + str(state["next_temp"])
            git(self.root, "update-ref", f"refs/luple/temps/{number}", commit)
            state["temps"][number] = {"commit": commit, "base": current.copy(), "message": "Autosave", "time": time.time()}
            state["next_temp"] += 1
            self.write(state, "autosave", temp=number)
            return number

    def files(self, commit):
        raw = git(self.root, "ls-tree", "-r", "--name-only", "-z", commit)
        return set(os.fsdecode(p) for p in raw.split(b"\0") if p)

    def validate_restore(self, source, target):
        before, after = self.files(source), self.files(target)
        for name in after:
            path = self.root / name
            # Never overwrite ignored files or files outside the captured snapshot.
            if name not in before and (path.exists() or path.is_symlink()):
                raise LupleError(f"Protected path blocks load: {name}")
            for parent in path.parents:
                if parent == self.root:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise LupleError(f"File/directory collision blocks load: {name}")
        # v0.1 deliberately refuses directory/file replacements.
        for name in before - after:
            path = self.root / name
            if path.is_dir() and not path.is_symlink():
                raise LupleError(f"Directory blocks load: {name}")

    def restore(self, source, target):
        self.validate_restore(source, target)
        with self.index(source) as env:
            git(self.root, "read-tree", "--reset", "-u", target, env=env)

    def navigate(self, target, position, state):
        recovery = self.add_temp(state, "Automatic backup before load")
        source = state["temps"][recovery]["commit"]
        self.validate_restore(source, target)
        atomic_json(self.pending, {"source": source, "target": target,
                                   "recovery": recovery, "state": state})
        try:
            self.restore(source, target)
            state["current"] = position
            self.write(state, "load", position=position, recovery=recovery)
            self.pending.unlink()
        except Exception:
            # Keep the durable journal. Explicit recovery is safer than guessing
            # which files a failed filesystem operation already changed.
            raise LupleError(f"Load interrupted. Backup {recovery} is preserved; run lu recover.")
        return f"Loaded #{position['save']} [{position['line']}]. Previous work: {recovery}"

    def load(self, number, line=None):
        with self.lock():
            state = self.read()
            if number not in state["saves"]:
                raise LupleError("Unknown save. Use lu h.")
            chosen = line or state["current"]["line"]
            if chosen not in state["lines"] or number not in self.ancestry(state, chosen):
                raise LupleError("Save is not on this worldline. Use lu m or lu l ID --line S1.")
            return self.navigate(state["saves"][number]["commit"], {"line": chosen, "save": number}, state)

    def recover_temp(self, number):
        with self.lock():
            state = self.read()
            if number not in state["temps"]:
                raise LupleError("Unknown temporary save.")
            temp = state["temps"][number]
            return self.navigate(temp["commit"], temp["base"].copy(), state)

    def recover(self):
        with self.lock():
            if not self.pending.exists():
                return "No interrupted operation."
            journal = json.loads(self.pending.read_text(encoding="utf-8"))
            # Capture the actual mixed working tree first; keep it as a rescue ref.
            actual = self.snapshot(journal["target"], "Interrupted load rescue")
            git(self.root, "update-ref", "refs/luple/rescue/" + uuid.uuid4().hex, actual)
            self.restore(actual, journal["source"])
            atomic_json(self.path, journal["state"])
            self.pending.unlink()
            return "Recovered the work and position from before the interrupted load."

    @staticmethod
    def ancestry(state, line):
        result = []
        number = state["lines"][line]["head"]
        while number is not None:
            result.append(number)
            number = state["saves"][number]["parent"]
        return result[::-1]

    def history(self, page=1):
        state = self.read()
        line = state["current"]["line"]
        ids = self.ancestry(state, line)
        rows = [f"Worldline {line} | current #{state['current']['save']} | page {page}"]
        for number in ids[(page - 1) * 10:page * 10]:
            save = state["saves"][number]
            children = sum(s["parent"] == number for s in state["saves"].values())
            marker = "*" if number == state["current"]["save"] else " "
            fork = " [FORK]" if children > 1 else ""
            rows.append(f"{marker} #{number}{fork} {save['message']}")
        return "\n".join(rows)

    def futures(self, number):
        state = self.read()
        if number not in state["saves"]:
            raise LupleError("Unknown save.")
        rows = [f"Futures from #{number} (preview only)"]
        for line in state["lines"]:
            path = self.ancestry(state, line)
            if number in path and path.index(number) < len(path) - 1:
                future = path[path.index(number) + 1:]
                rows.append(f"{line}: " + " -> ".join("#" + n for n in future))
        return "\n".join(rows)

    def observe(self, number):
        state = self.read()
        entry = state["temps"].get(number) if number.startswith("T") else state["saves"].get(number)
        if entry is None:
            raise LupleError("Unknown save/temp.")
        commit = entry["commit"]
        return git(self.root, "show", "--format=fuller", "--stat", "--no-renames", commit).decode("utf-8", "replace")

    def clean(self, keep=12):
        with self.lock():
            state = self.read()
            line = state["current"]["line"]
            ids = [n for n, t in state["temps"].items() if t["base"]["line"] == line]
            removed = ids[:-keep] if keep else ids
            for number in removed:
                del state["temps"][number]
            self.write(state, "clean", removed=removed)
            for number in removed:
                git(self.root, "update-ref", "-d", f"refs/luple/temps/{number}")
            return f"Removed {len(removed)} temporary save references; retained latest {keep} on {line}."
