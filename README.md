# Luple Git

Luple Git은 Git의 저장·되돌리기·분기 개념을 게임의 Save와 Load 방식으로 보여주는 Windows CLI입니다. 처음 쓰는 사람도 `Save → Load → History` 흐름으로 시작할 수 있게 만드는 것이 목표입니다.

## 빠른 시작

1. `LupleGit-Setup-0.5.0.exe`를 실행합니다.
2. 새 PowerShell 또는 VS Code 터미널을 열고 프로젝트 폴더로 이동합니다.
3. 아래처럼 저장합니다.

```powershell
cd C:\project
lu s "결제 화면 완성"
lu l
lu h
```

처음 Save하면 필요한 Git 저장소와 루플 설정이 자동으로 만들어집니다. `lu`가 인식되지 않으면 새 터미널을 열거나 현재 터미널에서 다음을 한 번 실행합니다.

```powershell
$env:Path += ";$env:LOCALAPPDATA\Programs\LupleGit"
```

## Save, Load, 세계선

첫 저장은 `S0-v1.0000`입니다. 같은 세계선의 끝에서 계속 Save하면 순서대로 이어집니다. 과거 저장점으로 Load한 뒤 새로 Save하면 기존 미래는 그대로 두고 새 세계선이 만들어집니다.

```text
main / S0
S0-v1.0000 ─ S0-v1.0001 ─ S0-v1.0002 ■
                  └─ S1-v1.0001 ─ S1-v1.0002 ■
```

- `◆`: 이 저장점에서 다른 미래가 갈라짐
- `■`: 세계선의 마지막 저장
- `★`: 즐겨찾기, 사용자별 최대 3개
- `◎`: 현재 Load 위치

Load는 파일과 현재 위치만 바꿉니다. 기존 저장점과 미래 기록은 지우지 않습니다.

## 개인 GitHub 동기화

개인 원격 저장소를 연결합니다.

```powershell
lu sys remote "https://github.com/사용자/저장소.git"
lu sys branch S0 main
lu i sync
```

새 프로젝트는 S0을 `main`에 연결합니다. Save 또는 `lu i sync`는 다음을 함께 전송합니다.

| 원격 브랜치 | 내용 |
| --- | --- |
| `main` | S0 세계선의 마지막 코드 |
| `luple/S1` 등 | 분기된 세계선의 마지막 코드 |
| `luple/state` | Save 목록과 세계선 복원 정보 |

원격 `main`에 기존 이력이 있으면 자동 덮어쓰지 않습니다. 먼저 아래처럼 통합 미리보기를 만들고 확인한 뒤 확정합니다.

```powershell
lu i import "https://github.com/사용자/저장소.git" main
lu i finish --message "기존 main과 루플 작업 통합"
lu i sync
```

충돌이 나면 루플이 알려준 검토 폴더에서 충돌 표식 `<<<<<<<`, `=======`, `>>>>>>>`을 모두 정리하고 저장한 뒤 실행합니다.

```powershell
lu i finish --resolved --message "충돌 해결 및 통합"
lu i sync
```

## 세계선 브랜치 이름

세계선 번호는 바뀌지 않는 내부 식별자입니다. GitHub에서 보이는 브랜치 이름은 바꿀 수 있습니다.

```powershell
lu sys branch
lu sys branch S1 payment
lu i sync
```

기존 원격 브랜치는 안전을 위해 자동 삭제하지 않습니다.

## 통합과 전달

다른 프로젝트나 팀원의 브랜치를 가져와 검토합니다.

```powershell
lu i import "https://github.com/사용자/다른프로젝트.git" main
lu i finish --message "모듈 통합"
```

선택한 Save를 다른 원격 브랜치로 전달할 수도 있습니다.

```powershell
lu i send "https://github.com/사용자/전달저장소.git" submission --save S0-v1.0001
```

통합 미리보기는 현재 작업 파일을 바꾸지 않습니다. `lu i finish`를 실행할 때만 새 Save가 만들어집니다.

## 현재 범위

현재 버전은 개인 저장, 세계선, 개인 원격 동기화, 통합 미리보기와 수동 충돌 해결을 제공합니다. 팀장 승인 흐름, Pull Request 자동 생성, AI 검수는 다음 단계의 기능입니다.

