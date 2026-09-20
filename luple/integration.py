"""Isolated merge previews. Working files change only on explicit finish."""
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from .core import LupleError, atomic_json, git, git_executable
from .remotes import fetch_target, safe_sync


def pending_path(repo):
    return repo.directory / "integration.json"


def prepare(repo, url=None, branch=None, identifier=None):
    with repo.lock():
        if pending_path(repo).exists():
            raise LupleError("진행 중인 통합이 있습니다. lu i status / finish / abort를 사용하세요.")
        state = repo.read()
        current = state["current"].copy()
        base = state["saves"][current["save"]]["commit"]
        captured = repo.snapshot(base, "Integration preflight")
        if git(repo.root, "diff-tree", "--no-commit-id", "-r", base, captured):
            raise LupleError("현재 변경사항을 먼저 Save한 뒤 통합하세요.")
        if identifier:
            n = repo.resolve(state, identifier)
            entry = state["saves"][n]
            if entry["deleted"] or not entry.get("commit"):
                raise LupleError("삭제된 저장점은 통합할 수 없습니다.")
            target = entry["commit"]
        else:
            target = fetch_target(repo, url, branch)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        p = subprocess.run([git_executable(), "-C", str(repo.root), "merge-tree", "--write-tree", "--allow-unrelated-histories", base, target], capture_output=True, env=env)
        output = p.stdout.decode("utf-8", "replace")
        tree = output.splitlines()[0] if output else ""
        if p.returncode not in (0, 1) or not re.fullmatch(r"[0-9a-f]{40,64}", tree):
            raise LupleError(p.stderr.decode("utf-8", "replace") or "통합 결과를 만들 수 없습니다.")
        if b"160000 " in git(repo.root, "ls-tree", "-r", tree):
            raise LupleError("서브모듈이 포함된 통합은 지원하지 않습니다.")
        folder = repo.directory / "integrations" / uuid.uuid4().hex
        folder.mkdir(parents=True)
        with repo.index(tree) as index:
            git(repo.root, "checkout-index", "--all", "--prefix=" + folder.as_posix() + "/", env=index)
        data = {"base": current, "base_commit": base, "target": target, "tree": tree,
                "folder": str(folder), "conflicts": p.returncode == 1,
                "description": identifier or f"{url} [{branch}]"}
        atomic_json(pending_path(repo), data)
        diff = git(repo.root, "diff", "--stat", base, tree).decode("utf-8", "replace")
        return ("충돌 확인 필요" if data["conflicts"] else "통합 미리보기 준비 완료") + "\n작업 파일은 변경하지 않았습니다.\n검토 폴더: " + str(folder) + "\n" + diff + ("\n충돌 파일을 수정하고 lu i finish --resolved로 확정하세요.\n" + output if data["conflicts"] else "\nlu i finish로 새 Save를 만드세요.")


def status(repo):
    if not pending_path(repo).exists():
        return "진행 중인 통합이 없습니다."
    return json.dumps(json.loads(pending_path(repo).read_text(encoding="utf-8")), ensure_ascii=False, indent=2)


def abort(repo):
    with repo.lock():
        pending_path(repo).unlink(missing_ok=True)
    return "통합을 취소했습니다. 작업 파일은 그대로이며 검토 폴더는 보존합니다."


def finish(repo, message="작업 통합", resolved=False):
    with repo.lock():
        if not pending_path(repo).exists(): raise LupleError("진행 중인 통합이 없습니다.")
        data = json.loads(pending_path(repo).read_text(encoding="utf-8"))
        state = repo.read()
        if state["current"] != data["base"]:
            raise LupleError("현재 위치가 바뀌었습니다. 통합을 취소하고 새 위치에서 다시 시작하세요.")
        captured = repo.snapshot(data["base_commit"], "Integration final preflight")
        if git(repo.root, "diff-tree", "--no-commit-id", "-r", data["base_commit"], captured):
            raise LupleError("검토 중 작업 파일이 바뀌었습니다. 먼저 Save하고 통합을 다시 시작하세요.")
        if data["conflicts"] and not resolved:
            raise LupleError("충돌 해결 후 --resolved를 지정하세요. 바이너리 충돌도 직접 확인해야 합니다.")
        folder = Path(data["folder"]).resolve()
        if not folder.is_relative_to((repo.directory / "integrations").resolve()):
            raise LupleError("잘못된 통합 검토 폴더입니다.")
        with repo.index(data["tree"]) as index:
            git(repo.root, "--work-tree=" + str(folder), "add", "-A", "--", ".", env=index)
            tree = git(repo.root, "write-tree", env=index).decode().strip()
        listing = git(repo.root, "ls-tree", "-r", tree)
        if b"160000 " in listing: raise LupleError("중첩 저장소는 통합할 수 없습니다.")
        if data["conflicts"]:
            for name in repo.files(tree):
                path = folder / name
                if path.is_file() and not path.is_symlink():
                    with path.open("rb") as stream:
                        if any(line.startswith((b"<<<<<<< ", b">>>>>>> ")) for line in stream):
                            raise LupleError("충돌 표시가 남아 있습니다: " + name)
        preview = repo.commit(tree, data["base_commit"], "Integration approved preview")
        repo.navigate(preview, data["base"].copy(), state)
        # Keep the lock through restoration and Save allocation.
        result = repo._save_local(message, tree=tree, merge_parent=data["target"], expected_current=data["base"], _locked=True)
        pending_path(repo).unlink(missing_ok=True)
    return result.replace(" (원격 백업 미지원)", "") + "\n" + safe_sync(repo)
