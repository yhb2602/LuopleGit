"""Luple domain model. Labels and views are independent of Git storage."""
import getpass
import contextlib
import json
import os
from pathlib import Path
import re
import time

from .core import Repository as Storage, LupleError, atomic_json, git


def safe_text(value):
    return " ".join("".join(c if c.isprintable() else " " for c in str(value)).split())


class Repository(Storage):
    @contextlib.contextmanager
    def lock(self):
        # A short autosave must not make foreground actions fail immediately.
        deadline = time.monotonic() + 10
        with contextlib.ExitStack() as stack:
            while True:
                try:
                    stack.enter_context(super().lock())
                    break
                except LupleError as error:
                    if "Another lu operation" not in str(error) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            yield

    @classmethod
    def initialize(cls, cwd="."):
        repo = super().initialize(cwd)
        with repo.lock():
            repo.write(repo.read(), "initialize-config")
        return repo

    @classmethod
    def open(cls, cwd="."):
        path = Path(cwd).resolve()
        if not path.is_dir():
            raise LupleError("프로젝트 폴더가 없습니다: " + str(path))
        # Respect an existing enclosing repository, never create nested repos.
        root = git(path, "rev-parse", "--show-toplevel", check=False).decode().strip()
        if not root:
            return cls.initialize(path)
        repo = cls(root)
        if not repo.path.exists():
            return cls.initialize(root)
        return repo

    @staticmethod
    def user_key():
        return getpass.getuser()

    def defaults(self):
        name = git(self.root, "config", "user.name", check=False).decode().strip() or self.user_key()
        email = git(self.root, "config", "user.email", check=False).decode().strip() or "local@luple.invalid"
        branch = git(self.root, "symbolic-ref", "--short", "HEAD", check=False).decode().strip() or "main"
        return {"org": "Local", "project": self.root.name, "main_version": "v1.0",
                "branch": branch, "name": name, "initials": name, "email": email,
                "team": "", "role": "", "autosave_enabled": True,
                "autosave_interval": 60, "autosave_keep": 12}

    def read(self):
        state = super().read()
        version = state.get("schema", 1)
        if version > 3:
            raise LupleError("이 저장소는 더 새로운 루플 버전이 필요합니다.")
        # Migration is computed on read, committed atomically on next mutation.
        # Existing commit IDs and existing files are never rewritten.
        if "config" not in state:
            state["config"] = self.defaults()
        state.setdefault("favorites", {})
        state["config"].setdefault("personal_remote", "")
        state.setdefault("next_line", max(int(s[1:]) for s in state["lines"]) + 1)
        def stage(number):
            entry = state["saves"][number]
            if "step" not in entry:
                entry["step"] = stage(entry["parent"]) + 1 if entry["parent"] else -1
            return entry["step"]
        for number, entry in state["saves"].items():
            stage(number)
            entry.setdefault("deleted", False)
            entry.setdefault("purged", False)
            entry.setdefault("author", state["config"]["initials"])
        state["schema"] = 3
        return state

    def write(self, state, action, **details):
        if self.path.exists():
            old = json.loads(self.path.read_text(encoding="utf-8"))
            if old.get("schema", 1) < 3 and state.get("schema") == 3:
                backup = self.directory / "state.before-v3.json"
                if not backup.exists():
                    atomic_json(backup, old)
        super().write(state, action, **details)

    @staticmethod
    def label(state, number):
        entry = state["saves"][number]
        step = entry["step"]
        # Counter is an integer, not floating point. 10000 carries into v2.0000.
        return f"{entry['line']}-base" if step < 0 else f"{entry['line']}-v{1 + step // 10000}.{step % 10000:04d}"

    def resolve(self, state, identifier):
        identifier = str(identifier).lstrip("#")
        if identifier in state["saves"]:
            return identifier
        for number in state["saves"]:
            if self.label(state, number).lower() == identifier.lower():
                return number
        raise LupleError("저장점을 찾을 수 없습니다. lu h로 확인하세요.")

    def commit(self, tree, parent, message):
        if not self.path.exists():
            return super().commit(tree, parent, message)
        cfg = json.loads(self.path.read_text(encoding="utf-8")).get("config")
        if not cfg:
            return super().commit(tree, parent, message)
        env = {f"GIT_{role}_{key}": cfg[field] for role in ("AUTHOR", "COMMITTER")
               for key, field in (("NAME", "name"), ("EMAIL", "email"))}
        parents = parent if isinstance(parent, list) else [parent] if parent else []
        args = ["commit-tree", tree] + [arg for p in parents for arg in ("-p", p)]
        return git(self.root, *args, env=env, data=(message + "\n").encode()).decode().strip()

    def save(self, message):
        result = self._save_local(message)
        from .remotes import safe_sync
        return result.replace(" (원격 백업 미지원)", "") + "\n" + safe_sync(self)

    def _save_local(self, message, tree=None, merge_parent=None, expected_current=None, _locked=False):
        message = safe_text(message)
        if not message:
            raise LupleError("저장 설명을 입력하세요.")
        with contextlib.nullcontext() if _locked else self.lock():
            state = self.read()
            cfg = state["config"]
            current = state["current"]
            if expected_current is not None and current != expected_current:
                raise LupleError("통합 중 현재 위치가 변경되었습니다. 새 위치에서 다시 검토하세요.")
            parent = state["saves"][current["save"]]
            line = current["line"]
            fork = state["lines"][line]["head"] != current["save"]
            if fork:
                line = "S" + str(state["next_line"])
            number = str(state["next_save"])
            entry = {"line": line, "step": parent["step"] + 1, "parent": current["save"],
                     "message": message, "time": time.time(), "author": cfg["initials"],
                     "main_version": cfg["main_version"], "deleted": False, "purged": False}
            state["saves"][number] = entry
            label = self.label(state, number)
            prefix = "/".join(cfg[key] for key in ("org", "project", "main_version", "branch", "initials") if cfg[key])
            full = f"[{prefix}] ({label}) {message}"
            commit = self.snapshot(parent["commit"], full) if tree is None else self.commit(tree, [parent["commit"], merge_parent] if merge_parent else parent["commit"], full)
            if not merge_parent and not git(self.root, "diff-tree", "--no-commit-id", "-r", parent["commit"], commit):
                return "변경사항이 없습니다. 새 저장과 세계선을 만들지 않았습니다."
            if fork:
                state["lines"][line] = {"head": None, "fork": current["save"]}
                state["next_line"] += 1
            entry["commit"] = commit
            entry["full_message"] = full
            if merge_parent:
                entry["merge_parents"] = [parent["commit"], merge_parent]
            git(self.root, "update-ref", f"refs/luple/saves/{number}", commit)
            state["next_save"] += 1
            state["lines"][line]["head"] = number
            state["current"] = {"line": line, "save": number}
            self.write(state, "save", save=number, line=line)
            return f"저장 완료: {label} · {message}\n보존 위치: 내 PC (원격 백업 미지원)"

    def load(self, number, line=None):
        from .remotes import safe_sync
        status = safe_sync(self, push=False)
        return self._load_local(number, line) + "\n" + status

    def _load_local(self, number, line=None):
        with self.lock():
            state = self.read()
            number = self.resolve(state, number)
            entry = state["saves"][number]
            if entry["deleted"] or entry["purged"]:
                raise LupleError("삭제된 저장점입니다. Sys에서 먼저 복원하세요.")
            chosen = line or entry["line"]
            if chosen not in state["lines"] or number not in self.ancestry(state, chosen):
                raise LupleError("선택한 세계선에 없는 저장점입니다.")
            self.navigate(entry["commit"], {"line": chosen, "save": number}, state)
            return f"불러오기 완료: {self.label(state, number)}\n이동 직전 작업은 자동 복구 기록에 보존했습니다."

    def favorites(self, state):
        return state["favorites"].get(self.user_key(), [])

    def favorite(self, identifier, remove=False, replace=None):
        with self.lock():
            state = self.read()
            number = self.resolve(state, identifier)
            if state["saves"][number]["deleted"]:
                raise LupleError("삭제된 기록은 즐겨찾기로 등록할 수 없습니다.")
            items = state["favorites"].setdefault(self.user_key(), [])
            if remove:
                if number in items:
                    items.remove(number)
            elif number not in items:
                if len(items) >= 3:
                    if replace is None:
                        raise LupleError("즐겨찾기는 최대 3개입니다. 교체할 기록을 선택하세요.")
                    previous = self.resolve(state, replace)
                    if previous not in items:
                        raise LupleError("교체 대상이 즐겨찾기에 없습니다.")
                    items.remove(previous)
                items.append(number)
            self.write(state, "favorite", save=number, remove=remove)
            return "즐겨찾기를 변경했습니다."

    def markers(self, state, number):
        result = "▶" if number == state["current"]["save"] else ""
        if sum(e["parent"] == number for e in state["saves"].values()) > 1:
            result += "◆"
        if any(line["head"] == number for line in state["lines"].values()):
            result += "●"
        if number in self.favorites(state):
            result += "★"
        return result

    def history_ids(self, state, line=None, deleted=False, favorite=False):
        ids = self.ancestry(state, line) if line else list(state["saves"])
        ids = [n for n in ids if not state["saves"][n]["purged"]
               and (deleted or not state["saves"][n]["deleted"])
               and (not favorite or n in self.favorites(state))]
        return sorted(ids, key=lambda n: (state["saves"][n]["time"], int(n)), reverse=True)

    def history(self, page=1, line=None, deleted=False, favorite=False, temps=False):
        from datetime import datetime
        state = self.read()
        if line and line not in state["lines"]:
            raise LupleError("세계선을 찾을 수 없습니다.")
        ids = self.history_ids(state, line, deleted, favorite)
        records = [(state["saves"][n]["time"], n, False) for n in ids]
        if temps and not favorite:
            records += [(t["time"], n, True) for n, t in state["temps"].items()
                        if not line or t["base"]["line"] == line]
        records.sort(reverse=True)
        pages = max(1, (len(records) + 9) // 10)
        if page < 1 or page > pages:
            raise LupleError(f"페이지는 1~{pages} 범위입니다.")
        rows = [f"History | 현재 {self.label(state, state['current']['save'])} | {page}/{pages}"]
        for stamp, n, temporary in records[(page - 1) * 10:page * 10]:
            date = datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M")
            if temporary:
                rows.append(f"{date}  [자동 복구] {n} · {safe_text(state['temps'][n]['message'])}")
            else:
                entry = state["saves"][n]
                rows.append(f"{date}  {safe_text(entry['message'])}  {self.label(state, n)} {self.markers(state, n)}" + (" [삭제됨]" if entry["deleted"] else ""))
        return "\n".join(rows)

    def observe(self, identifier):
        state = self.read()
        number = identifier if identifier in state["temps"] else self.resolve(state, identifier)
        if number in state["saves"] and state["saves"][number]["purged"]:
            raise LupleError("완전 삭제된 기록은 관측할 수 없습니다.")
        return super().observe(number)

    def configure(self, key=None, value=None):
        if key == "personal_remote":
            from .remotes import configure
            return configure(self, value)
        if key is None:
            return json.dumps(self.read()["config"], ensure_ascii=False, indent=2)
        with self.lock():
            state = self.read()
            if key not in state["config"]:
                raise LupleError("알 수 없는 설정 이름입니다.")
            if key in ("autosave_interval", "autosave_keep"):
                value = int(value)
                if value < 1:
                    raise LupleError("1 이상의 값을 입력하세요.")
            elif key == "autosave_enabled":
                if str(value).lower() not in ("true", "false", "on", "off"):
                    raise LupleError("on 또는 off를 입력하세요.")
                value = str(value).lower() in ("true", "on")
            else:
                value = safe_text(value)
                if key not in ("org", "team", "role") and not value:
                    raise LupleError("빈 값으로 설정할 수 없습니다.")
                if any(c in value for c in "[]/\\<>"):
                    raise LupleError("설정에는 [], /, \\, <, > 문자를 사용할 수 없습니다.")
            state["config"][key] = value
            self.write(state, "config", key=key)
            return f"설정 완료: {key} = {value}"

    def trash(self, identifier, action="delete", confirmation=None):
        with self.lock():
            state = self.read()
            number = self.resolve(state, identifier)
            entry = state["saves"][number]
            if action not in ("delete", "restore", "purge"):
                raise LupleError("알 수 없는 삭제 작업입니다.")
            if entry["purged"]:
                raise LupleError("완전 삭제된 기록입니다.")
            if action == "restore":
                entry["deleted"] = False
            else:
                if number == "0" or number == state["current"]["save"]:
                    raise LupleError("초기 기준점과 현재 작업 위치는 삭제할 수 없습니다. 다른 저장점을 먼저 불러오세요.")
                if action == "purge":
                    if not entry["deleted"]:
                        raise LupleError("먼저 일반 삭제를 하세요.")
                    if confirmation != self.label(state, number):
                        raise LupleError("완전 삭제하려면 확인란에 저장점 이름을 정확히 입력하세요.")
                    # A minimal tombstone retains lineage. No Git history rewrite/GC.
                    entry["purged"] = True
                    entry["commit"] = None
                    entry["message"] = "완전 삭제된 기록"
                    entry.pop("full_message", None)
                entry["deleted"] = True
                for items in state["favorites"].values():
                    if number in items:
                        items.remove(number)
            self.write(state, action, save=number)
            if action == "purge":
                git(self.root, "update-ref", "-d", f"refs/luple/saves/{number}")
            return "복원 완료" if action == "restore" else "삭제 완료" + (" (루플 복원 불가; Git 데이터의 물리적 소거는 아님)" if action == "purge" else " (Sys에서 복원 가능)")

    def recover_temp(self, number):
        state = self.read()
        if number not in state["temps"]:
            raise LupleError("자동 복구 기록을 찾을 수 없습니다.")
        base = state["temps"][number]["base"]["save"]
        if state["saves"][base]["deleted"]:
            raise LupleError("기준 저장점이 삭제되었습니다. 먼저 기준 저장점을 복원하세요.")
        return super().recover_temp(number)

    def autosave(self):
        cfg = self.read()["config"]
        if not cfg["autosave_enabled"]:
            return None
        result = super().autosave()
        if result:
            # Never prune safety backups or manual temporary saves automatically.
            with self.lock():
                state = self.read()
                line = state["current"]["line"]
                entries = [n for n, e in state["temps"].items() if e["base"]["line"] == line and e["message"] == "Autosave"]
                removed = entries[:-cfg["autosave_keep"]]
                for n in removed:
                    del state["temps"][n]
                self.write(state, "autosave-prune", removed=removed)
                for n in removed:
                    git(self.root, "update-ref", "-d", f"refs/luple/temps/{n}")
        return result
