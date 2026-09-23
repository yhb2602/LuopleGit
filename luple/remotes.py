"""Personal history transport; never rewinds remote branches or edits work files."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
import time
from urllib.parse import urlsplit

from .core import LupleError, git, git_executable

BRANCH = "refs/heads/luple/state"
CACHE = "refs/luple/remote/state"


class IntegrationRequired(LupleError):
    pass


def remote_heads(repo, url):
    listing = run(repo, "ls-remote", "--symref", url, "HEAD", "refs/heads/*")
    heads, default = {}, None
    for row in listing.splitlines():
        value, ref = row.split("\t", 1)
        if value.startswith("ref: refs/heads/") and ref == "HEAD":
            default = value[len("ref: refs/heads/"):]
        elif ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = value
    if default == "luple/state" or default not in heads:
        candidates = [name for name in heads if name != "luple/state"]
        default = "main" if "main" in candidates else candidates[0] if len(candidates) == 1 else None
    if default is None and any(name != "luple/state" for name in heads):
        raise LupleError("원격 기본 브랜치를 확인할 수 없습니다. 원격 저장소에서 기본 브랜치를 설정하세요.")
    return default or "main", heads


def includes(repo, descendant, ancestor):
    base = git(repo.root, "merge-base", descendant, ancestor, check=False).decode().strip()
    return base == ancestor


def integration_message(line, branch):
    return (f"기존 원격 작업과 통합이 필요합니다: {line} → {branch}\n"
            f"lu i connect --line {line} 로 통합 미리보기를 여세요.\n"
            "검토 후 lu i finish로 확정하세요. 충돌이 있으면 해결 후 --resolved를 추가하세요.")


def address(value):
    value = value.strip()
    if not value or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise LupleError("유효한 저장소 주소를 입력하세요.")
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "ssh", "file"):
            raise LupleError("HTTPS, SSH 또는 로컬 저장소 주소를 사용하세요.")
        if parsed.password or (parsed.scheme == "https" and parsed.username):
            raise LupleError("주소에 인증 정보를 넣지 마세요. Git 자격 증명 관리자나 SSH를 사용하세요.")
    elif "::" in value:
        raise LupleError("외부 전송 도우미 주소는 지원하지 않습니다.")
    return value


def run(repo, *args, timeout=120):
    args = list(args)
    # Git's default local transport invokes sh on Windows. Invoke the native
    # Git upload/receive service directly instead; only generated local URLs
    # get ext permission, never a user-supplied helper command.
    prefix = []
    if os.name == "nt" and args and args[0] in ("ls-remote", "fetch", "push"):
        for index in range(1, len(args)):
            raw = args[index]
            if raw.startswith("-") or "://" in raw: continue
            candidate = Path(raw)
            if not candidate.is_absolute(): candidate = repo.root / candidate
            if candidate.is_dir():
                escape = lambda value: value.replace("%", "%%").replace(" ", "% ")
                service = "receive-pack" if args[0] == "push" else "upload-pack"
                args[index] = "ext::" + escape(Path(git_executable()).as_posix()) + " " + service + " " + escape(candidate.resolve().as_posix())
                prefix = ["-c", "protocol.ext.allow=always"]
                break
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run([git_executable(), "-C", str(repo.root), *prefix, *args],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise LupleError("원격 작업 시간 초과. 로컬 저장은 보존되어 있습니다.")
    if p.returncode:
        raise LupleError(p.stderr.decode("utf-8", "replace").strip() or p.stdout.decode("utf-8", "replace").strip())
    return p.stdout.decode("utf-8", "replace").strip()


def configure(repo, url):
    if url != "off": url = address(url)
    with repo.lock():
        state = repo.read()
        state["config"]["personal_remote"] = "" if url == "off" else url
        state.pop("remote_status", None)
        repo.write(state, "personal-remote")
    if url == "off":
        return "개인 원격 연결을 해제했습니다. 로컬 기록은 보존됩니다."
    try:
        return check_connection(repo, url)
    except (LupleError, OSError) as error:
        return "주소 저장 완료 · 연결 확인 대기: " + str(error) + "\nlu i sync로 연결을 다시 확인하세요."


def check_connection(repo, url):
    # Network inspection happens before taking the local state lock.
    default, heads = remote_heads(repo, url)
    target = fetch_target(repo, url, default) if default in heads else None
    with repo.lock():
        state = repo.read()
        if state["config"].get("personal_remote") != url:
            raise LupleError("확인 중 원격 주소가 변경되었습니다. 다시 연결하세요.")
        ensure_uids(state)
        ensure_branches(repo, state)
        old = state.get("remote_connection", {})
        if old.get("url") != url:
            if any(k != "S0" and v["branch"] == default for k, v in state["lines"].items()):
                raise LupleError("원격 기본 브랜치를 다른 세계선이 사용 중입니다. lu sys branch로 확인하세요.")
            state["lines"]["S0"].update(branch=default, branch_changed=time.time_ns())
        branch = state["lines"]["S0"]["branch"]
        state["remote_connection"] = {"url": url, "default": default}
        head = state["saves"][state["lines"]["S0"]["head"]]["commit"]
        needs_merge = branch == default and target and not includes(repo, head, target)
        state["remote_status"] = "통합 필요" if needs_merge else "연결 확인 완료"
        repo.write(state, "remote-connect", branch=branch)
    if needs_merge:
        return integration_message("S0", branch)
    return f"개인 원격 연결 확인 완료: S0 → {branch}\nlu i sync로 저장 기록을 동기화하세요."


def ensure_uids(state):
    for entry in state["lines"].values():
        entry.setdefault("uid", uuid.uuid4().hex)
    for entry in state["saves"].values():
        entry.setdefault("uid", uuid.uuid4().hex)


def ensure_branches(repo, state):
    used = {v["branch"] for v in state["lines"].values() if v.get("branch")}
    for key, line in state["lines"].items():
        if not line.get("branch"):
            name = state["config"].get("branch", "main") if key == "S0" else "luple/" + key
            if name in used or name == "luple/state":
                name = "luple/" + key + "-" + line.get("uid", uuid.uuid4().hex)[:8]
            line["branch"] = name
            used.add(name)
        git(repo.root, "check-ref-format", "refs/heads/" + line["branch"])
        if line["branch"] == "luple/state":
            raise LupleError("luple/state는 내부 기록용 이름입니다.")
    names = [v["branch"] for v in state["lines"].values()]
    if len(names) != len(set(names)):
        raise LupleError("세계선의 브랜치 이름이 중복됩니다. lu sys branch로 변경하세요.")


def configure_branch(repo, line=None, name=None):
    with repo.lock():
        state = repo.read()
        ensure_uids(state)
        ensure_branches(repo, state)
        if name is None:
            return "\n".join(k + " → " + v["branch"] for k, v in state["lines"].items())
        if line not in state["lines"]:
            raise LupleError("세계선을 찾을 수 없습니다.")
        if not name or name.startswith("-"):
            raise LupleError("올바른 브랜치 이름을 입력하세요.")
        git(repo.root, "check-ref-format", "refs/heads/" + name)
        if name == "luple/state" or any(k != line and v["branch"] == name for k,v in state["lines"].items()):
            raise LupleError("이미 사용 중이거나 예약된 브랜치 이름입니다.")
        state["lines"][line]["branch"] = name
        state["lines"][line]["branch_changed"] = time.time_ns()
        repo.write(state, "branch-name", line=line, branch=name)
    return "브랜치 이름 설정 완료: " + line + " → " + name + "\nlu i sync로 반영하세요. 기존 원격 이름은 안전을 위해 보존합니다."


def validate(repo, data):
    if data.get("format") != 1 or not isinstance(data.get("saves"), dict) or not isinstance(data.get("lines"), dict):
        raise LupleError("지원하지 않는 루플 원격 기록입니다.")
    saves, lines = data["saves"], data["lines"]
    if len(saves) > 100000:
        raise LupleError("원격 저장 기록이 너무 큽니다.")
    for key, entry in saves.items():
        if not re.fullmatch(r"[0-9]+", key) or not re.fullmatch(r"[0-9a-f]{32}", str(entry.get("uid", ""))):
            raise LupleError("잘못된 원격 저장 식별자입니다.")
        if not isinstance(entry, dict) or not isinstance(entry.get("step"), int) or entry["step"] < -1:
            raise LupleError("잘못된 원격 저장 단계입니다.")
        if entry.get("line") not in lines or (entry.get("parent") is not None and entry["parent"] not in saves):
            raise LupleError("원격 기록의 연결이 올바르지 않습니다.")
        if not isinstance(entry.get("message"), str) or not isinstance(entry.get("time"), (int, float)):
            raise LupleError("잘못된 원격 저장 정보입니다.")
        commit = entry.get("commit")
        if commit is not None:
            if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                raise LupleError("잘못된 원격 커밋입니다.")
            git(repo.root, "cat-file", "-e", commit + "^{commit}")
        elif not entry.get("purged"):
            raise LupleError("원격 저장점의 커밋이 없습니다.")
    for key, line in lines.items():
        if not re.fullmatch(r"S[0-9]+", key) or line.get("head") not in saves or not re.fullmatch(r"[0-9a-f]{32}", str(line.get("uid", ""))):
            raise LupleError("잘못된 원격 세계선입니다.")
    for key in saves:
        seen = set()
        while key is not None:
            if key in seen: raise LupleError("원격 기록에 순환 연결이 있습니다.")
            seen.add(key)
            key = saves[key]["parent"]


def import_graph(repo, state, data):
    validate(repo, data)
    incoming = data["saves"]
    # A pristine empty project can adopt remote naming without touching files.
    if len(state["saves"]) == 1 and not state["temps"] and not repo.files(state["saves"]["0"]["commit"]) and not git(repo.root, "ls-files", "--others", "--exclude-standard"):
        roots = [n for n, e in incoming.items() if e["parent"] is None and e.get("commit") and not repo.files(e["commit"])]
        if roots:
            old_config, favorites = state["config"], state["favorites"]
            state["saves"] = copy.deepcopy(incoming)
            state["lines"] = copy.deepcopy(data["lines"])
            root = roots[0]
            state["current"] = {"line": incoming[root]["line"], "save": root}
            state["next_save"] = max(int(n) for n in incoming) + 1
            state["next_line"] = max(int(n[1:]) for n in state["lines"]) + 1
            return
    existing = {e["commit"]: n for n, e in state["saves"].items() if e.get("commit")}
    known_uid = {e.get("uid"): n for n, e in state["saves"].items()}
    ids = {}
    for n, entry in incoming.items():
        if entry.get("uid") in known_uid:
            ids[n] = known_uid[entry["uid"]]
        elif entry.get("commit") in existing:
            ids[n] = existing[entry["commit"]]
        else:
            ids[n] = str(state["next_save"])
            state["next_save"] += 1
    local_uids = {v["uid"]: k for k, v in state["lines"].items()}
    mapping = {}
    for name, remote_line in data["lines"].items():
        local = local_uids.get(remote_line["uid"])
        remote_head = ids[remote_line["head"]]
        if local is None:
            local = next((k for k, v in state["lines"].items() if v["head"] == remote_head), None)
        if local is not None:
            local_head = state["lines"][local]["head"]
            # Divergent heads become separate worldlines, never overwrite one.
            remote_ancestors = []
            cursor = remote_line["head"]
            while cursor is not None:
                remote_ancestors.append(ids[cursor]); cursor = incoming[cursor]["parent"]
            if local_head not in remote_ancestors and remote_head not in repo.ancestry(state, local):
                local = next((k for k, v in state["lines"].items() if v["head"] == remote_head), None)
        if local is None:
            owner = local_uids.get(remote_line["uid"])
            if owner is not None and remote_line.get("branch"):
                state["lines"][owner]["branch"] = "luple/diverged-" + state["lines"][owner]["uid"][:8]
            local = "S" + str(state["next_line"]); state["next_line"] += 1
            state["lines"][local] = {"head": remote_head, "fork": ids.get(remote_line.get("fork")), "uid": remote_line["uid"] if remote_line["uid"] not in local_uids else uuid.uuid4().hex}
        elif remote_head not in repo.ancestry(state, local):
            state["lines"][local]["head"] = remote_head
        target = state["lines"][local]
        if remote_line.get("branch") and (not target.get("branch") or target["uid"] != remote_line["uid"] or remote_line.get("branch_changed", 0) > target.get("branch_changed", 0)):
            target["branch"] = remote_line["branch"]
            target["branch_changed"] = remote_line.get("branch_changed", 0)
        mapping[name] = local
    for n, entry in incoming.items():
        if ids[n] in state["saves"]:
            continue
        copied = copy.deepcopy(entry)
        copied["parent"] = ids.get(entry["parent"])
        copied["line"] = mapping[entry["line"]]
        state["saves"][ids[n]] = copied


def sync(repo, push=True):
    initial = repo.read()
    url = initial["config"].get("personal_remote", "")
    if not url: return "개인 원격 미설정 · 내 PC에 보존"
    if initial.get("remote_connection", {}).get("url") != url:
        check_connection(repo, url)
    with repo.lock():
        state = repo.read()
        url = state["config"].get("personal_remote", "")
        if not url: return "개인 원격 미설정 · 내 PC에 보존"
        address(url)
        ensure_uids(state)
        repo.write(state, "sync-start")
        listing = run(repo, "ls-remote", "--refs", url, BRANCH)
        remote_head = None
        if listing:
            run(repo, "fetch", "--no-tags", url, "+" + BRANCH + ":" + CACHE)
            remote_head = git(repo.root, "rev-parse", CACHE).decode().strip()
            payload = git(repo.root, "show", remote_head + ":state.json")
            if len(payload) > 32 * 1024 * 1024: raise LupleError("원격 메타데이터가 너무 큽니다.")
            try: data = json.loads(payload)
            except ValueError: raise LupleError("원격 메타데이터를 읽을 수 없습니다.")
            import_graph(repo, state, data)
        for n, e in state["saves"].items():
            if e.get("commit"):
                git(repo.root, "update-ref", "refs/luple/saves/" + n, e["commit"])
        ensure_branches(repo, state)
        repo.write(state, "remote-fetch")
        if not push:
            return "개인 원격 기록을 가져왔습니다. 현재 작업 파일은 그대로입니다."
        # Check code history too: the manifest alone cannot detect ordinary Git pushes.
        _, heads = remote_heads(repo, url)
        for name, line in state["lines"].items():
            if line["branch"] not in heads:
                continue
            target = fetch_target(repo, url, line["branch"])
            head = state["saves"][line["head"]].get("commit")
            if head and not includes(repo, head, target):
                state["remote_status"] = "통합 필요"
                repo.write(state, "remote-integration-required", line=name)
                raise IntegrationRequired(integration_message(name, line["branch"]))
        payload = {"format": 1, "saves": state["saves"], "lines": state["lines"]}
        blob = git(repo.root, "hash-object", "-w", "--stdin", data=json.dumps(payload, ensure_ascii=False).encode()).decode().strip()
        tree = git(repo.root, "mktree", data=f"100644 blob {blob}\tstate.json\n".encode()).decode().strip()
        # Reachability anchors retain every snapshot without changing Git main.
        parents = list(dict.fromkeys(e["commit"] for e in state["saves"].values() if e.get("commit")))
        anchor = None
        for offset in range(0, len(parents), 64):
            anchor = repo.commit(tree, ([anchor] if anchor else []) + parents[offset:offset + 64], "Luople snapshot anchors")
        commit = repo.commit(tree, list(dict.fromkeys(p for p in (remote_head, anchor) if p)), "Luople personal history sync")
        # Normal fast-forward push: concurrent remote updates are rejected.
        specs = [commit + ":" + BRANCH]
        for line in state["lines"].values():
            head = state["saves"][line["head"]]
            if head.get("commit") and not head.get("purged"):
                specs.append(head["commit"] + ":refs/heads/" + line["branch"])
        try:
            run(repo, "push", "--atomic", url, *specs)
        except LupleError as error:
            if any(word in str(error) for word in ("fetch first", "non-fast-forward", "stale info")):
                raise IntegrationRequired("전송 중 원격 작업이 변경되었습니다. lu i pull로 기록을 확인하고 lu i connect로 통합하세요.") from error
            raise
        state["remote_status"] = "동기화 완료"
        repo.write(state, "remote-push", commit=commit)
        return "개인 원격 동기화 완료"


def safe_sync(repo, push=True):
    try:
        return sync(repo, push)
    except IntegrationRequired as error:
        return "로컬 기록 보존 · 원격 통합 필요\n" + str(error) + "\nlu i에서 충돌 해결 메뉴를 열 수 있습니다."
    except (LupleError, OSError, ValueError, KeyError, TypeError) as error:
        message = str(error)
        try:
            with repo.lock():
                state = repo.read()
                state["remote_status"] = "동기화 대기"
                repo.write(state, "remote-pending")
        except (LupleError, OSError, ValueError):
            pass
        return "로컬 기록 유지 · 원격 동기화 대기: " + message + "\nlu i sync로 다시 시도하세요."


def fetch_target(repo, url, branch):
    address(url)
    if not branch or branch.startswith("-"):
        raise LupleError("가져올 브랜치를 선택하세요.")
    git(repo.root, "check-ref-format", "refs/heads/" + branch)
    ref = "refs/luple/incoming/" + uuid.uuid4().hex
    run(repo, "fetch", "--no-tags", url, "refs/heads/" + branch + ":" + ref)
    return git(repo.root, "rev-parse", ref).decode().strip()


def send(repo, url, branch, identifier):
    address(url)
    git(repo.root, "check-ref-format", "refs/heads/" + branch)
    with repo.lock():
        state = repo.read()
        n = repo.resolve(state, identifier or state["current"]["save"])
        entry = state["saves"][n]
        if entry["deleted"] or not entry.get("commit"): raise LupleError("삭제된 저장점은 보낼 수 없습니다.")
        run(repo, "push", url, entry["commit"] + ":refs/heads/" + branch)
        return "전송 완료: " + branch + " (상대 Main 자동 병합 없음)"
