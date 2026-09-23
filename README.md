# Luople Git

Luople Git은 Git의 저장·되돌리기·분기 개념을 게임의 Save와 Load 방식으로 보여주는 Windows CLI입니다. 처음 쓰는 사람도 `Save → Load → History` 흐름으로 시작할 수 있게 만드는 것이 목표입니다.

## 빠른 시작

1. `LuopleGit-Setup-0.5.3.exe`를 실행합니다.
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
$env:Path += ";$env:LOCALAPPDATA\Programs\LuopleGit"
```

기존 설치를 업데이트한 경우에는 `Programs\LupleGit` 경로를 그대로 사용합니다. 이 경우 위 명령의 `LuopleGit`을 `LupleGit`으로 바꾸세요. 제품 영문 이름은 **Luople Git**, 명령어는 계속 `lu`입니다. 기존 저장 기록의 호환성을 위해 `.git/luple`, `luple/state`와 내부 Python 모듈 이름은 유지합니다. 이 경로를 수동으로 바꾸지 마세요.

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

Load와 History의 목록은 좌표를 먼저 보여 줍니다.

```text
■ S0-v1.0004 (09-20/21:50) | ☞ README 업데이트
  S0-v1.0003 (09-20/21:43) | ☞ 기존 main과 루플 작업 통합
```

`lu l`은 현재 세계선의 정식 Save를 선택해 Load합니다. `lu h`는 모든 세계선의 Save 이력을 봅니다.

## 즐겨찾기

Load 화면에서 Save를 선택한 뒤 `즐겨찾기 등록`을 선택하면 됩니다. 즐겨찾기는 사용자별로 최대 3개까지 등록할 수 있고, Load 목록 맨 위의 `바로가기`에 `★`로 표시됩니다.

명령어로도 관리할 수 있습니다.

```powershell
lu h --star S0-v1.0002
lu h --unstar S0-v1.0002
```

## 개인 GitHub 동기화

개인 원격 저장소를 연결합니다.

```powershell
lu sys remote "https://github.com/사용자/저장소.git"
lu i sync
```

0.5.3부터 연결 시 원격 기본 브랜치와 저장 이력을 확인합니다. 빈 원격은 S0을 `main`에 연결하고, 기존 원격은 실제 기본 브랜치(예: `main`, `trunk`)에 연결합니다. 기본 브랜치가 불명확하면 확인을 요청합니다. 연결 확인은 파일을 바꾸거나 원격에 전송하지 않습니다.

기존 0.5.2 프로젝트도 다음 `lu i sync`에서 최초 연결 확인을 수행합니다. 기존 `master` 브랜치를 삭제하지 않고 S0의 전송 대상을 원격 기본 브랜치로 맞춥니다. 연결 후 `lu sys branch`로 지정한 사용자 이름은 유지합니다. 연결 실패 시 주소와 로컬 기록은 보존되며 다음 동기화 때 재확인합니다.

Save 또는 `lu i sync`는 다음을 함께 전송합니다(기본 브랜치가 `main`인 경우).

| 원격 브랜치 | 내용 |
| --- | --- |
| `main` | S0 세계선의 마지막 코드 |
| `luple/S1` 등 | 분기된 세계선의 마지막 코드 |
| `luple/state` | Save 목록과 세계선 복원 정보 |

원격 `main`에 기존 이력이 있으면 자동 덮어쓰지 않습니다. 먼저 아래처럼 통합 미리보기를 만들고 확인한 뒤 확정합니다.

```powershell
lu i connect
lu i finish --message "기존 main과 루플 작업 통합"
lu i sync
```

`lu i connect`는 연결된 개인 원격의 S0 대상 브랜치를 가져와 통합 미리보기를 만듭니다. 먼저 현재 변경사항을 Save하고 S0의 마지막 저장에서 실행하세요. 다른 세계선은 `lu i connect --line S1`처럼 지정할 수 있습니다. 메뉴에서는 `lu i` → `기존 개인 원격 작업과 통합`을 선택합니다. 취소는 `lu i abort`입니다.

기존 원격 기록을 포함하지 않는 전송은 중단하고 `통합 필요`로 안내합니다. 단순히 `sync`를 반복해서는 해결되지 않습니다. 통합 미리보기를 검토하고 확정하면 새 Save에 양쪽 이력이 연결됩니다. 원격이 전송 직전에 바뀌는 경우에도 강제 덮어쓰지는 않습니다.

충돌이 나면 루플이 알려준 검토 폴더에서 충돌 표식 `<<<<<<<`, `=======`, `>>>>>>>`을 모두 정리하고 저장한 뒤 실행합니다.

```powershell
lu i finish --resolved --message "충돌 해결 및 통합"
lu i sync
```

## 임시 저장

`lu t`는 작업 중간 상태를 내 PC에만 남깁니다. 원격 GitHub, 정식 Save 목록, 세계선에는 반영하지 않습니다.

```powershell
lu t "결제 화면 작업 중"
lu t --list
lu t --recover T12
lu t --clean
```

`--clean`은 현재 세계선의 임시 저장 중 최근 12개만 유지합니다.

임시 저장 번호는 `T1`, `T2`, `T3`처럼 자동으로 증가하며, 정리 후에도 번호를 다시 쓰지 않습니다. 별도 최대 번호는 없습니다.

`lu l`은 Temp를 표시하거나 Load하지 않습니다. Temp는 `lu t --recover T번호`로만 파일을 복구합니다. 이 복구는 현재 세계선 좌표를 움직이지 않으므로, 복구한 작업을 정식 기록으로 남기려면 다시 Save합니다.

```powershell
lu t --recover T12
lu s "복구한 작업을 정식 저장"
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
