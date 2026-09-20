"""Interactive save browser. Browsing never mutates repository state."""
from datetime import datetime
import os
import sys

from .core import LupleError


def render_options(options, selected=None, sections=None, numbered=False):
    lines = []
    for index, label in enumerate(options):
        if sections and index in sections:
            lines.extend(["─" * 56, sections[index], ""])
        prefix = f"  {index + 1}. " if numbered else (" > " if index == selected else "   ")
        lines.append(prefix + label)
    if sections:
        lines.extend(["─" * 56, "■ 마지막 저장   ◆ 갈림길   ★ 즐겨찾기"])
    return "\n".join(lines)


def choose(title, options, subtitle="", initial=0, sections=None):
    """Windows arrow-key picker; portable numbered fallback for other terminals."""
    if os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty():
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)) and kernel.SetConsoleMode(handle, mode.value | 4):
            selected = min(max(initial, 0), len(options) - 1)
            try:
                while True:
                    print("\033[2J\033[H" + title + "\n" + subtitle + "\n")
                    print(render_options(options, selected, sections))
                    print("\n위/아래: 선택  Enter: 열기  Esc: 뒤로")
                    key = msvcrt.getwch()
                    if key in ("\x00", "\xe0"):
                        key = msvcrt.getwch()
                        if key == "H":
                            selected = (selected - 1) % len(options)
                        elif key == "P":
                            selected = (selected + 1) % len(options)
                    elif key == "\r":
                        return selected
                    elif key in ("\x1b", "q", "Q"):
                        return None
                    elif key == "\x03":
                        raise KeyboardInterrupt
            finally:
                kernel.SetConsoleMode(handle, mode.value)
    while True:
        print("\n" + title + "\n" + subtitle)
        print(render_options(options, sections=sections, numbered=True))
        try:
            value = input("선택 번호 (Enter/q: 뒤로): ").strip()
        except EOFError:
            return None
        if not value or value.lower() == "q":
            return None
        if value.isdigit() and 1 <= int(value) <= len(options):
            return int(value) - 1
        print("목록에 있는 번호를 선택하세요.")


def text(value):
    # Save messages may contain terminal control sequences or embedded newlines.
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())


def browse(repo, line=None, page=1, picker=None):
    picker = picker or choose
    state = repo.read()
    line = line or state["current"]["line"]
    if line not in state["lines"]:
        raise LupleError("Unknown worldline.")
    stack = [("list", line, max(0, page - 1), None)]
    while stack:
        state = repo.read()
        view, line, value, anchor = stack[-1]
        current = state["current"]
        position = f"실제 작업 위치: {current['line']} / 저장 #{current['save']} | 탐색 중에는 파일이 바뀌지 않습니다."
        if view == "list":
            ids = repo.ancestry(state, line)
            if anchor is not None:
                ids = ids[ids.index(anchor):]
            ids = ids[::-1]
            page_index = min(value, (len(ids) - 1) // 10)
            visible = ids[page_index * 10:(page_index + 1) * 10]
            labels = []
            actions = []
            for number in visible:
                save = state["saves"][number]
                fork = sum(s["parent"] == number for s in state["saves"].values()) > 1
                marker = "[현재] " if number == current["save"] and line == current["line"] else ""
                stamp = datetime.fromtimestamp(save["time"]).strftime("%m-%d %H:%M")
                labels.append(f"{marker}저장 #{number}  {text(save['message'])}  | {stamp}" + ("  [갈림길]" if fork else ""))
                actions.append(("detail", number))
            if page_index > 0:
                labels.append("이전 페이지")
                actions.append(("page", page_index - 1))
            if (page_index + 1) * 10 < len(ids):
                labels.append("다음 페이지")
                actions.append(("page", page_index + 1))
            labels.append("뒤로" if len(stack) > 1 else "닫기")
            actions.append(("back", None))
            selected = picker(f"루플 | 저장 목록 | {line} | {page_index + 1}/{(len(ids) + 9) // 10}", labels, position)
            action, target = actions[selected] if selected is not None else ("back", None)
            if action == "back":
                stack.pop()
            elif action == "page":
                stack[-1] = ("list", line, target, anchor)
            else:
                stack.append(("detail", line, target, None))
        else:
            number = value
            save = state["saves"][number]
            labels = ["이 저장점 로드 — 현재 작업을 자동 보존한 뒤 이동", "변경 내용 관측"]
            actions = [("load", None), ("observe", None)]
            children = [(n, s) for n, s in state["saves"].items() if s["parent"] == number]
            if len(children) > 1:
                for child, entry in children:
                    labels.append(f"이어지는 미래 {entry['line']} 열기 — #{child} {text(entry['message'])}")
                    actions.append(("future", (entry["line"], child)))
            labels.append("저장 목록으로 돌아가기")
            actions.append(("back", None))
            stamp = datetime.fromtimestamp(save["time"]).strftime("%Y-%m-%d %H:%M:%S")
            selected = picker(f"저장 #{number} | {text(save['message'])}\n{stamp} | 탐색 세계선 {line}", labels, position)
            action, target = actions[selected] if selected is not None else ("back", None)
            if action == "load":
                return repo.load(number, line)
            if action == "observe":
                # Render through the same picker so arrow-mode redraw does not
                # immediately hide the observed result.
                report = "\n".join(text(row) for row in repo.observe(number).splitlines())
                picker("변경 내용 관측", ["돌아가기"], report)
            elif action == "future":
                new_line, child = target
                stack.append(("list", new_line, 0, child))
            elif action == "back":
                stack.pop()
    return "저장 목록을 닫았습니다. 작업 파일은 변경하지 않았습니다."
