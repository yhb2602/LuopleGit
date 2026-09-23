"""Review and resolve files in an isolated integration preview."""
import difflib
import json
import os
from pathlib import Path
import re
import subprocess

from .core import LupleError, atomic_json, git
from . import integration


def load(repo):
    path = integration.pending_path(repo)
    if not path.exists():
        raise LupleError("진행 중인 통합이 없습니다.")
    data = json.loads(path.read_text(encoding="utf-8"))
    folder = Path(data["folder"]).resolve()
    if not folder.is_relative_to((repo.directory / "integrations").resolve()) or not folder.is_dir():
        raise LupleError("통합 검토 폴더를 찾을 수 없습니다.")
    return data, folder


def records(repo, data):
    raw = git(repo.root, "merge-tree", "--write-tree", "-z", "--allow-unrelated-histories",
              data["base_commit"], data["target"], check=False)
    result = {}
    for record in raw.split(b"\0")[1:]:
        if not record:
            break
        match = re.fullmatch(rb"([0-7]+) ([0-9a-f]+) ([123])\t(.+)", record, re.S)
        if match:
            mode, oid, stage, name = match.groups()
            result.setdefault(os.fsdecode(name), {})[int(stage)] = (mode.decode(), oid.decode())
    if data["conflicts"] and not result:
        raise LupleError("충돌 파일 목록을 해석하지 못했습니다. 검토 폴더에서 직접 확인하세요.")
    return result


def target_path(folder, name):
    path = folder / name
    if path.is_symlink() or not path.resolve().is_relative_to(folder):
        raise LupleError("심볼릭 링크 또는 폴더 밖의 파일은 자동 해결할 수 없습니다.")
    return path


def has_markers(path):
    if not path.exists():
        return False
    with path.open("rb") as stream:
        return any(row.startswith((b"<<<<<<< ", b">>>>>>> ")) for row in stream)


def unresolved(repo):
    data, folder = load(repo)
    files = records(repo, data) if data["conflicts"] else {}
    done = data.get("resolutions", {})
    return [name for name in files if name not in done or has_markers(target_path(folder, name))]


def compare(repo, name):
    data, _ = load(repo)
    stages = records(repo, data)[name]
    sides = []
    for stage in (2, 3):
        entry = stages.get(stage)
        blob = git(repo.root, "cat-file", "blob", entry[1]) if entry else b""
        if b"\0" in blob:
            return "바이너리 파일입니다. 내 PC 또는 가져온 파일을 선택하거나 외부 도구에서 검토하세요."
        sides.append(blob.decode("utf-8", "replace").splitlines(keepends=True))
    return "".join(difflib.unified_diff(*sides, fromfile="내 PC", tofile="가져온 작업")) or "텍스트 차이가 없거나 삭제·이름 변경 충돌입니다."


def resolve(repo, name, choice):
    with repo.lock():
        data, folder = load(repo)
        files = records(repo, data)
        if name not in files or choice not in ("ours", "theirs", "manual"):
            raise LupleError("올바른 충돌 파일과 처리 방법을 선택하세요.")
        path = target_path(folder, name)
        if choice != "manual":
            entry = files[name].get(2 if choice == "ours" else 3)
            if entry and entry[0] not in ("100644", "100755"):
                raise LupleError("특수 파일은 직접 수정으로 해결하세요.")
            if path.is_dir():
                raise LupleError("파일·폴더 충돌은 검토 폴더에서 직접 해결하세요.")
            if entry:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(git(repo.root, "cat-file", "blob", entry[1]))
            else:
                path.unlink(missing_ok=True)
        if has_markers(path):
            raise LupleError("충돌 표시가 남아 있습니다. 수정하고 저장한 뒤 다시 확인하세요.")
        data.setdefault("resolutions", {})[name] = choice
        atomic_json(integration.pending_path(repo), data)


def open_editor(repo, name):
    _, folder = load(repo)
    path = target_path(folder, name)
    if os.name == "nt":
        if path.is_file() and b"\0" not in path.read_bytes():
            subprocess.Popen(["notepad.exe", str(path)])
        else:
            os.startfile(str(folder))
    else:
        raise LupleError("이 파일을 편집기로 열어 수정하세요: " + str(path))


def review(repo, picker=None):
    from .menu import choose
    from .ui import report
    from .model import safe_text
    picker = picker or choose
    while True:
        data, _ = load(repo)
        names = list(records(repo, data)) if data["conflicts"] else []
        pending = unresolved(repo)
        options = [("○ " if n in pending else "✓ ") + safe_text(n) for n in names]
        options += ["통합하고 저장하기", "나중에 하기"]
        summary = git(repo.root, "diff", "--stat", data["base_commit"], data["tree"]).decode("utf-8", "replace")
        selected = picker("통합 검토", options, f"충돌 {len(names)}개 중 {len(names) - len(pending)}개 해결\n" + safe_text(summary))
        if selected is None or selected == len(names) + 1:
            return "통합 검토를 보존했습니다. lu i에서 이어서 해결할 수 있습니다."
        if selected == len(names):
            if pending:
                report("안내", "남은 충돌 파일을 먼저 해결하세요.", picker)
                continue
            if picker("통합 저장", ["확인 · 통합하고 저장", "돌아가기"], "선택한 결과를 작업 파일에 반영하고 새 Save를 만듭니다.") == 0:
                return integration.finish(repo, "검토한 작업 통합", resolved=True)
            continue
        name = names[selected]
        action = picker(safe_text(name), ["두 내용 비교하기", "내 PC 파일 사용", "가져온 파일 사용", "직접 수정하기", "수정 완료 확인", "돌아가기"], "파일 전체를 선택합니다. 선택한 쪽에 파일이 없으면 삭제됩니다.")
        try:
            if action == 0:
                diff = compare(repo, name).splitlines()
                for offset in range(0, len(diff), 35):
                    report("차이 보기", "\n".join(diff[offset:offset + 35]), picker)
            elif action in (1, 2):
                if picker("선택 확인", ["이 파일에 적용", "돌아가기"], safe_text(name) + " · " + ("내 PC" if action == 1 else "가져온 작업") + "의 파일 전체를 사용합니다.") == 0:
                    resolve(repo, name, "ours" if action == 1 else "theirs")
            elif action == 3:
                open_editor(repo, name)
                report("직접 수정", "열린 편집기에서 수정하고 저장한 뒤 '수정 완료 확인'을 선택하세요.", picker)
            elif action == 4:
                resolve(repo, name, "manual")
        except (LupleError, OSError) as error:
            report("안내", str(error), picker)
