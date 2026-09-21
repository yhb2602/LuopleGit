import argparse
import json
import sys

from .core import LupleError
from .model import Repository


def parser():
    cli = argparse.ArgumentParser(prog="lu", description="루플 Git · 저장하고, 골라서 돌아오기")
    cli.add_argument("--version", action="version", version="Luple Git 0.5.2")
    cli.add_argument("-C", default=".", metavar="폴더", help="프로젝트 폴더 (기본: 현재 폴더)")
    commands = cli.add_subparsers(dest="command")
    save = commands.add_parser("s", aliases=["save"], help="현재 작업 저장")
    save.add_argument("message", nargs="?")
    temp = commands.add_parser("t", aliases=["temp"], help="로컬 임시 저장 · 원격에 동기화하지 않음")
    temp.add_argument("message", nargs="?", default="임시 저장")
    temp.add_argument("--clean", action="store_true", help="현재 세계선의 임시 저장은 최근 12개만 유지")
    temp.add_argument("--list", action="store_true", help="현재 세계선의 임시 저장 목록")
    temp.add_argument("--recover", metavar="T번호", help="임시 저장을 작업 파일로 복구")
    for name, alias, description in (("l", "load", "저장 목록에서 불러오기"), ("h", "history", "모든 세계선 이력")):
        cmd = commands.add_parser(name, aliases=[alias], help=description)
        if name == "l":
            cmd.add_argument("id", nargs="?")
        else:
            cmd.add_argument("--deleted", action="store_true")
            cmd.add_argument("--temps", action="store_true")
            cmd.add_argument("--favorites", action="store_true")
            cmd.add_argument("--show", metavar="저장점")
            cmd.add_argument("--star", metavar="저장점")
            cmd.add_argument("--unstar", metavar="저장점")
            cmd.add_argument("--replace", metavar="저장점")
        cmd.add_argument("--line", metavar="S0")
        cmd.add_argument("--page", type=int, default=None)
        mode = cmd.add_mutually_exclusive_group()
        mode.add_argument("--list", action="store_true", help="텍스트 목록만 출력")
        mode.add_argument("--menu", action="store_true", help="선택 메뉴 열기")
    integrate = commands.add_parser("i", aliases=["integrate"], help="개인 동기화 · 가져오기 · 통합 · 보내기")
    operations = integrate.add_subparsers(dest="operation")
    for name in ("sync", "pull", "status", "abort"):
        operations.add_parser(name)
    receive = operations.add_parser("import")
    receive.add_argument("url")
    receive.add_argument("branch")
    merge = operations.add_parser("merge")
    merge.add_argument("id")
    send = operations.add_parser("send")
    send.add_argument("url")
    send.add_argument("branch")
    send.add_argument("--save", dest="save_id")
    finish = operations.add_parser("finish")
    finish.add_argument("--message", default="작업 통합")
    finish.add_argument("--resolved", action="store_true")
    commands.add_parser("review", help="AI 검수 (추후 예정)")
    settings = commands.add_parser("sys", help="설정 · 자동 복구 · 삭제 관리")
    sub = settings.add_subparsers(dest="setting")
    config = sub.add_parser("config")
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    branch = sub.add_parser("branch", help="세계선별 코드 브랜치 이름")
    branch.add_argument("line", nargs="?")
    branch.add_argument("name", nargs="?")
    remote = sub.add_parser("remote", help="개인 원격 저장소 주소 설정 / off로 해제")
    remote.add_argument("url", nargs="?")
    for name in ("init", "status", "recover"):
        sub.add_parser(name)
    auto = sub.add_parser("autosave")
    auto.add_argument("action", choices=["status", "on", "off", "start", "run", "once", "list", "recover"], nargs="?", default="status")
    auto.add_argument("id", nargs="?")
    auto.add_argument("--interval", type=int)
    auto.add_argument("--keep", type=int)
    delete = sub.add_parser("delete")
    delete.add_argument("id", nargs="?")
    group = delete.add_mutually_exclusive_group()
    group.add_argument("--restore", action="store_true")
    group.add_argument("--purge", action="store_true")
    delete.add_argument("--confirm", help="완전 삭제 대상 저장점 이름")
    for name in ("init", "status", "recover"):
        commands.add_parser(name, help="이전 버전 호환 명령")
    return cli


def interactive(args):
    return args.menu or (not args.list and sys.stdin.isatty() and sys.stdout.isatty())


def dispatch(args):
    if args.command == "review":
        return "AI 검수는 추후 개발 단계입니다."
    repo = Repository.open(args.C)
    if args.command in ("i", "integrate"):
        from . import remotes, integration
        if args.operation is None:
            from .integration_ui import menu
            return menu(repo)
        if args.operation in ("sync", "pull"):
            return remotes.sync(repo, push=args.operation == "sync")
        if args.operation == "import":
            return integration.prepare(repo, args.url, args.branch)
        if args.operation == "merge":
            return integration.prepare(repo, identifier=args.id)
        if args.operation == "send":
            return remotes.send(repo, args.url, args.branch, args.save_id)
        if args.operation == "finish":
            return integration.finish(repo, args.message, args.resolved)
        return getattr(integration, args.operation)(repo)
    if args.command == "init" or (args.command == "sys" and args.setting == "init"):
        return "프로젝트 준비 완료: " + str(repo.root)
    if args.command is None:
        from .ui import home
        return home(repo)
    if args.command in ("s", "save"):
        message = args.message
        if message is None and sys.stdin.isatty():
            message = input("저장 설명: ")
        if not message:
            raise LupleError('lu s "저장 설명" 형태로 입력하세요.')
        result = repo.save(message)
        from .autosave import start
        start(repo)
        return result
    if args.command in ("t", "temp"):
        if args.clean:
            return repo.clean_temps()
        if args.recover:
            return repo.recover_temp(args.recover)
        if args.list:
            state = repo.read()
            line = state["current"]["line"]
            rows = [(number, entry) for number, entry in state["temps"].items() if entry["base"]["line"] == line]
            rows.sort(key=lambda item: item[1]["time"], reverse=True)
            return "\n".join(f"{number} · {entry['message']}" for number, entry in rows) or "현재 세계선에 임시 저장이 없습니다."
        return repo.temp(args.message)
    if args.command in ("l", "load", "h", "history"):
        if args.page is not None and args.page < 1:
            raise LupleError("페이지는 1 이상입니다.")
        is_history = args.command in ("h", "history")
        if is_history:
            if args.star or args.unstar:
                return repo.favorite(args.star or args.unstar, remove=bool(args.unstar), replace=args.replace)
            if args.show:
                return repo.observe(args.show)
        elif args.id:
            result = repo.load(args.id, args.line)
            from .autosave import start
            start(repo)
            return result
        elif not is_history and not interactive(args):
            from .remotes import safe_sync
            result = safe_sync(repo, push=False)
            if "대기" in result:
                print(result)
        if interactive(args) and not (is_history and args.temps):
            from .ui import browse
            return browse(repo, line=args.line, page=args.page, history=is_history,
                          deleted=getattr(args, "deleted", False), favorite=getattr(args, "favorites", False))
        line = args.line if is_history else args.line or repo.read()["current"]["line"]
        return repo.history(args.page or 1, line=line, deleted=getattr(args, "deleted", False),
                            favorite=getattr(args, "favorites", False), temps=getattr(args, "temps", False))
    if args.command in ("status", "recover"):
        return getattr(repo, args.command)()
    if args.command == "sys":
        if args.setting == "branch":
            from .remotes import configure_branch
            return configure_branch(repo, args.line, args.name)
        if args.setting == "remote":
            from .remotes import configure
            return configure(repo, args.url) if args.url else repo.read()["config"].get("personal_remote", "") or "개인 원격 저장소 미설정"
        if args.setting is None:
            from .ui import settings
            return settings(repo)
        if args.setting == "config":
            if args.key and args.value is None:
                raise LupleError("설정값도 함께 입력하세요.")
            return repo.configure(args.key, args.value)
        if args.setting in ("status", "recover"):
            return getattr(repo, args.setting)()
        if args.setting == "delete":
            if not args.id:
                return repo.history(deleted=True)
            action = "purge" if args.purge else "restore" if args.restore else "delete"
            return repo.trash(args.id, action, args.confirm)
        if args.setting == "autosave":
            if args.interval is not None:
                repo.configure("autosave_interval", args.interval)
            if args.keep is not None:
                repo.configure("autosave_keep", args.keep)
            if args.action in ("on", "off"):
                result = repo.configure("autosave_enabled", args.action)
                if args.action == "on":
                    from .autosave import start
                    start(repo)
                return result
            if args.action == "start":
                repo.configure("autosave_enabled", "on")
                from .autosave import start
                start(repo)
                return "백그라운드 자동 저장을 시작했습니다."
            if args.action == "run":
                from .autosave import run
                return run(repo)
            if args.action == "once":
                return repo.autosave() or "변경사항 없음 또는 자동 저장 꺼짐"
            if args.action == "recover":
                if not args.id:
                    raise LupleError("복구할 T 번호를 입력하세요.")
                return repo.recover_temp(args.id)
            if args.action == "list":
                return "\n".join(f"{n} · {e['base']['line']} · {e['message']}" for n, e in repo.read()["temps"].items()) or "자동 복구 기록이 없습니다."
            cfg = repo.read()["config"]
            status = repo.directory / "autosave.status"
            return json.dumps({k: v for k, v in cfg.items() if k.startswith("autosave")}, ensure_ascii=False) + ("\n최근 처리: " + status.read_text(encoding="utf-8") if status.exists() else "\n아직 자동 저장 실행 기록이 없습니다.")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    if args.command is None and not sys.stdin.isatty():
        parser().print_help()
        return
    try:
        result = dispatch(args)
        if result:
            print(result)
    except (KeyboardInterrupt, EOFError):
        print("\n취소했습니다.")
    except (LupleError, OSError, ValueError) as error:
        print(f"lu: {error}", file=sys.stderr)
        raise SystemExit(1)

