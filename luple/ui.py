from datetime import datetime

from .menu import choose
from .model import safe_text, Repository
from .core import LupleError


def report(title, content, picker=choose):
    picker(title, ["돌아가기"], "\n".join(safe_text(row) for row in content.splitlines()))


def save_row(repo, state, number):
    entry = state["saves"][number]
    stamp = datetime.fromtimestamp(entry["time"]).strftime("%m-%d %H:%M")
    marks = repo.markers(state, number)
    prefix = "■ " if "●" in marks else "  "
    suffix = " ".join(mark for mark in ("◆", "★") if mark in marks)
    return (f"{prefix}{safe_text(entry['message'])} · {repo.label(state, number)} · {stamp}"
            + ("  " + suffix if suffix else "") + (" [삭제됨]" if entry["deleted"] else ""))


def browse(repo, line=None, page=None, picker=None, history=False, deleted=False, favorite=False):
    picker = picker or choose
    if not history:
        from .remotes import safe_sync
        status = safe_sync(repo, push=False)
        if "대기" in status:
            report("원격 동기화", status, picker)
    state = repo.read()
    line = line or (None if history else state["current"]["line"])
    if line is not None and line not in state["lines"]:
        raise LupleError("세계선을 찾을 수 없습니다.")
    ids = repo.history_ids(state, line, deleted, favorite)
    initial = max(0, (page or 1) - 1)
    if not history and page is None and state["current"]["save"] in ids:
        initial = ids.index(state["current"]["save"]) // 10
    stack = [("list", line, initial, None)]
    while stack:
        state = repo.read()
        view, line, value, anchor = stack[-1]
        position = f"{'─' * 56}\n현재 위치: ◎ {repo.label(state, state['current']['save'])}\n탐색 중 파일 변경 없음"
        labels, actions = [], []
        if view == "list":
            ids = repo.history_ids(state, line, deleted, favorite)
            if anchor is not None:
                route = repo.ancestry(state, line)
                allowed = route[route.index(anchor):]
                ids = [n for n in ids if n in allowed]
            pages = max(1, (len(ids) + 9) // 10)
            index = min(value, pages - 1)
            sections = {}
            if len(stack) > 1:
                sections[len(labels)] = "이전으로"
                previous = stack[-2]
                previous_name = (safe_text(state["saves"][previous[2]]["message"]) + " · " + repo.label(state, previous[2])) if previous[0] == "detail" else "저장 목록"
                labels.append("● 이전으로 · " + previous_name)
                actions.append(("back", None))
            # Favorites are shortcuts; actual save records remain unique in history.
            if not history:
                shortcuts = [n for n in repo.favorites(state) if not state["saves"][n]["deleted"]]
                if shortcuts:
                    sections[len(labels)] = "바로가기"
                for shortcut_index, n in enumerate(shortcuts, 1):
                    if not state["saves"][n]["deleted"]:
                        labels.append(f"바로가기 {shortcut_index}: ★ " + safe_text(state["saves"][n]["message"]) + " · " + repo.label(state, n))
                        actions.append(("detail", (n, state["saves"][n]["line"])))
            sections[len(labels)] = f"저장 목록 ({index + 1}/{pages} 페이지)"
            for n in ids[index * 10:(index + 1) * 10]:
                entry = state["saves"][n]
                stamp = datetime.fromtimestamp(entry["time"]).strftime("%m-%d %H:%M")
                labels.append(save_row(repo, state, n))
                actions.append(("detail", (n, line or entry["line"])))
            if index > 0:
                labels.append("이전 페이지")
                actions.append(("page", index - 1))
            if index + 1 < pages:
                labels.append("다음 페이지")
                actions.append(("page", index + 1))
            labels.append("뒤로" if len(stack) > 1 else "닫기")
            actions.append(("back", None))
            cursor = next((i for i, (a, t) in enumerate(actions) if a == "detail" and t[0] == state["current"]["save"]), 0)
            title = f"{'History' if history else 'Load'} · {line or '모든 세계선'} · {index + 1}/{pages}"
            selected = picker(title, labels, position, initial=cursor, sections=sections) if picker is choose else picker(title, labels, position)
            action, target = actions[selected] if selected is not None else ("back", None)
            if action == "detail":
                n, route = target
                stack.append(("detail", route, n, None))
            elif action == "page":
                stack[-1] = ("list", line, target, anchor)
            else:
                stack.pop()
        else:
            n = value
            entry = state["saves"][n]
            if not entry["deleted"]:
                labels.append("Load로 열기" if history else "이 저장 불러오기 · 이동 전 작업 자동 보존")
                actions.append(("open-load" if history else "load", None))
            labels.append("변경 내용 관측")
            actions.append(("observe", None))
            children = [(i, e) for i, e in state["saves"].items() if e["parent"] == n]
            if len(children) > 1:
                for i, child in children:
                    labels.append(f"다른 진행 {child['line']} 열기 · {safe_text(child['message'])}")
                    actions.append(("future", (child["line"], i)))
            if not entry["deleted"]:
                labels.append("즐겨찾기 해제" if n in repo.favorites(state) else "즐겨찾기 등록")
                actions.append(("star", None))
            labels.append("● 이전으로 · 저장 목록")
            actions.append(("back", None))
            detail_title = save_row(repo, state, n) + "\n작성자: " + safe_text(entry['author'])
            detail_sections = {0: "저장점 작업", len(labels) - 1: "이전으로"}
            selected = picker(detail_title, labels, position, sections=detail_sections) if picker is choose else picker(detail_title, labels, position)
            action, target = actions[selected] if selected is not None else ("back", None)
            if action == "load":
                result = repo.load(n, line)
                from .autosave import start
                start(repo)
                return result
            if action == "open-load":
                confirmation = picker("Load · " + repo.label(state, n), ["이 저장 불러오기 · 이동 전 작업 자동 보존", "돌아가기"], position)
                if confirmation == 0:
                    result = repo.load(n, line)
                    from .autosave import start
                    start(repo)
                    return result
            elif action == "observe":
                report("변경 내용", repo.observe(n), picker)
            elif action == "future":
                new_line, child = target
                stack.append(("list", new_line, 0, child))
            elif action == "star":
                items = repo.favorites(state)
                replacement = None
                if n not in items and len(items) == 3:
                    chosen = picker("즐겨찾기 3개 중 교체할 기록", [repo.label(state, i) + " · " + safe_text(state["saves"][i]["message"]) for i in items], "Esc: 취소")
                    if chosen is None:
                        continue
                    replacement = items[chosen]
                repo.favorite(n, remove=n in items, replace=replacement)
            elif action == "back":
                stack.pop()
    return "목록을 닫았습니다. 작업 파일은 변경하지 않았습니다."


def settings(repo):
    while True:
        selected = choose("Sys", ["사용자·프로젝트 설정", "자동 저장 설정", "자동 복구 기록", "저장 기록 삭제", "삭제된 기록", "중단된 Load 복구", "다른 프로젝트 열기", "세계선 브랜치 이름", "돌아가기"])
        if selected is None or selected == 8:
            return
        try:
            state = repo.read() if selected != 5 else None
            if selected == 0:
                keys = [k for k in state["config"] if not k.startswith("autosave")]
                index = choose("설정할 항목", [f"{k}: {state['config'][k]}" for k in keys])
                if index is not None:
                    print(repo.configure(keys[index], input("새 값: ")))
            elif selected == 1:
                action = choose("자동 저장", ["켜기", "끄기", "주기(초) 설정", "보관 개수 설정"], str({k: v for k, v in state["config"].items() if k.startswith("autosave")}))
                if action in (0, 1):
                    repo.configure("autosave_enabled", "on" if action == 0 else "off")
                    if action == 0:
                        from .autosave import start
                        start(repo)
                elif action in (2, 3):
                    repo.configure("autosave_interval" if action == 2 else "autosave_keep", input("값: "))
            elif selected == 2:
                ids = list(reversed(state["temps"]))
                if not ids:
                    report("자동 복구", "복구 기록이 없습니다.")
                    continue
                index = choose("자동 복구 기록", [n + " · " + state["temps"][n]["message"] for n in ids])
                if index is not None and choose("작업 상태 복구", ["복구하기", "돌아가기"], ids[index]) == 0:
                    report("복구", repo.recover_temp(ids[index]))
            elif selected in (3, 4):
                ids = [n for n, e in state["saves"].items() if not e["purged"] and e["deleted"] == (selected == 4) and n != "0" and n != state["current"]["save"]]
                if not ids:
                    report("삭제 관리", "대상 기록이 없습니다.")
                    continue
                index = choose("삭제된 기록" if selected == 4 else "삭제할 기록", [repo.label(state, n) + " · " + safe_text(state["saves"][n]["message"]) for n in ids])
                if index is None:
                    continue
                n = ids[index]
                action = choose("삭제 관리 · " + repo.label(state, n), ["복원", "완전 삭제", "돌아가기"] if selected == 4 else ["Load에서 제외 (복원 가능)", "돌아가기"])
                if selected == 3 and action == 0:
                    report("삭제", repo.trash(n))
                elif selected == 4 and action == 0:
                    report("복원", repo.trash(n, "restore"))
                elif selected == 4 and action == 1:
                    print("루플에서 복원 불가. Git 데이터의 물리적 소거는 아닙니다.")
                    report("완전 삭제", repo.trash(n, "purge", input("확인할 저장점 이름: ")))
            elif selected == 5:
                report("중단된 Load 복구", repo.recover())
            elif selected == 7:
                from .remotes import configure_branch
                print(configure_branch(repo))
                print(configure_branch(repo, input("세계선 (예: S0): ").strip(), input("새 브랜치 이름: ").strip()))
            elif selected == 6:
                return home(Repository.open(input("프로젝트 폴더: ").strip().strip('"')))
        except (LupleError, ValueError) as error:
            report("안내", str(error))


def home(repo):
    from .autosave import start
    start(repo)
    while True:
        selection = choose("루플 Git · " + repo.root.name, ["Save · 저장하기", "Load · 불러오기", "History · 전체 이력", "Sys · 설정·복구·삭제", "통합 · 가져오기·보내기", "종료"], str(repo.root))
        if selection is None or selection == 5:
            return
        try:
            if selection == 0:
                report("Save", repo.save(input("저장 설명: ")))
            elif selection in (1, 2):
                report("결과", browse(repo, history=selection == 2))
            elif selection == 3:
                settings(repo)
            elif selection == 4:
                from .integration_ui import menu
                menu(repo)
        except (LupleError, ValueError) as error:
            report("안내", str(error))
