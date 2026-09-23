from .menu import choose
from .ui import report
from .core import LupleError
from . import remotes, integration


def destination(repo):
    default = repo.read()["config"].get("personal_remote", "")
    value = input(f"저장소 주소 (Enter: {default or '미설정'}): ").strip() or default
    return remotes.address(value)


def menu(repo):
    from .conflicts import review
    if integration.pending_path(repo).exists():
        return review(repo)
    while True:
        selected = choose("통합 · lu i", ["개인 원격 동기화 (받고 보내기)", "개인 원격에서 기록 받기", "다른 세계선과 합치기", "저장소에서 가져와 통합", "저장소로 보내기", "통합 미리보기 상태", "통합 확정", "통합 취소", "기존 개인 원격 작업과 통합", "돌아가기"])
        if selected is None or selected == 9: return
        try:
            if selected in (0, 1):
                result = remotes.sync(repo, push=selected == 0)
            elif selected == 2:
                identifier = input("통합할 저장점 (예: S1-v1.0001): ").strip()
                result = integration.prepare(repo, identifier=identifier)
            elif selected == 3:
                url = destination(repo)
                listing = remotes.run(repo, "ls-remote", "--heads", url)
                branches = [row.split("refs/heads/", 1)[1] for row in listing.splitlines() if "refs/heads/" in row]
                if not branches: raise LupleError("가져올 브랜치가 없습니다.")
                index = choose("가져올 브랜치", branches)
                if index is None: continue
                result = integration.prepare(repo, url, branches[index])
            elif selected == 4:
                url = destination(repo)
                branch = input("보낼 브랜치 (Enter: luple/submission): ").strip() or "luple/submission"
                state = repo.read()
                identifier = input("보낼 저장점 (Enter: 현재 저장): ").strip() or state["current"]["save"]
                n = repo.resolve(state, identifier)
                if choose("전송 내용 확인", ["보내기", "취소"], f"{url}\n브랜치: {branch}\n저장: {repo.label(state, n)}") != 0: continue
                result = remotes.send(repo, url, branch, n)
            elif selected == 5:
                result = integration.status(repo)
            elif selected == 6:
                result = review(repo)
            elif selected == 8:
                result = integration.connect(repo, input("통합할 세계선 (Enter: S0): ").strip() or "S0")
            else:
                result = integration.abort(repo)
            if selected in (2, 3, 8) and integration.pending_path(repo).exists():
                return review(repo)
            report("통합 결과", result)
        except remotes.IntegrationRequired as error:
            if choose("기존 작업과 통합 필요", ["통합 내용 확인하기", "나중에 하기"], str(error)) == 0:
                try:
                    integration.connect(repo)
                    return review(repo)
                except (LupleError, OSError) as problem:
                    report("통합 안내", str(problem))
        except (LupleError, OSError, ValueError) as error:
            report("통합 안내", str(error))
