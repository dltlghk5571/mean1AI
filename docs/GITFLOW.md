# mean1AI Git Flow

적용 기준: 2026-09-05 · 공동 저장소: [dltlghk5571/mean1AI](https://github.com/dltlghk5571/mean1AI)

4명은 작업 단위로 브랜치를 만들고 `develop`에서 다음 버전의 기능을 통합한다. `main`은 검증된
시연·릴리스 기준을 보관한다. 이 저장소의 릴리스는 프로토타입 버전이며 실제 행정 서비스 배포를 뜻하지 않는다.

## 브랜치와 PR 대상

| 브랜치 | 시작 지점 | PR 대상 | 용도 |
| --- | --- | --- | --- |
| `main` | 기존 이력 유지 | 필요 시 `develop` 동기화 | 검증된 릴리스 기준, 기본 브랜치 |
| `develop` | `main` | 릴리스 브랜치 생성 | 다음 버전 통합 |
| `feature/<작업명>` | 최신 `develop` | `develop` | UI·서버·모델·데이터·문서 작업과 일반 수정 |
| `release/<버전>` | 준비된 `develop` | `main`, `develop` | 버전·문서·마지막 오류 수정 |
| `hotfix/<작업명>` | 최신 `main` | `main`, `develop`, 진행 중인 `release/*` | 공개한 시연·릴리스의 긴급 수정 |

담당자별 영구 브랜치는 만들지 않는다. `feature/chatbot-ui`, `feature/service-taxonomy`,
`feature/agent-api`, `feature/llm-classifier`처럼 독립적으로 검토·병합할 작업마다 만든다.
`release/*`에는 다음 버전의 새 기능을 추가하지 않는다. 일반 버그 수정과 문서 변경도 `feature/*`를 사용한다.

PR은 다른 팀원 한 명의 검토와 `test`, `gitflow` 통과 후 **Create a merge commit**으로 병합한다.
공유 브랜치 강제 푸시는 하지 않는다. 릴리스와 핫픽스는 모든 대상 브랜치에 반영했는지 확인한 뒤
작업 브랜치를 삭제한다. `main`과 `develop`은 유지한다.

## 평소 작업

아래 작업명은 예시다. 실제 작업에 맞는 이름으로 바꾼다. 최초 한 번 원격 `develop`을 추적한다.

```bash
git fetch origin
git switch --track origin/develop
```

이미 로컬 `develop`이 있으면 `git switch develop`을 사용한다. 이후 작업마다:

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/chatbot-ui
# 작업, 테스트, 파일 검토와 커밋
git push -u origin feature/chatbot-ui
gh pr create --base develop --head feature/chatbot-ui
```

GitHub에서 PR을 만들 때도 대상이 `develop`인지 확인한다. 기본 브랜치는 `main`이므로 자동 선택된
대상을 그대로 사용하면 안 된다. 원격 변경을 합칠 때 충돌은 작업 브랜치에서 해결하고 다시 검증한다.
JSON 계약 변경은 관련 UI·서버·모델 담당자가 함께 검토하며 호환되지 않는 변경은 문서에 명시한다.

## 릴리스와 긴급 수정

릴리스 담당자는 `develop`의 준비된 커밋에서 `release/0.2.0` 같은 브랜치를 만든다. 버전 번호는
예시이며 실제 릴리스 시 결정한다. 릴리스 브랜치에서 검증을 끝내고 `main` 대상 PR을 병합한다.
그 병합 커밋에 합의한 `v0.2.0` 같은 태그를 달고, 같은 릴리스 브랜치의 변경을 `develop`에도 PR로
반영한다. 팀원들이 보는 `pyproject.toml` 버전·문서·태그의 버전도 맞춘다. 태그 생성이 자동 배포를 실행하지는 않는다.

핫픽스는 `main`에서 시작해 `main`과 `develop` 각각에 PR로 반영한다. 진행 중인 `release/*`가 있으면
그 릴리스에도 반영해 다음 버전에서 수정이 사라지지 않게 한다. 수정된 `main`의 병합 커밋에 새 패치
태그를 붙인다. `main → develop` 동기화 PR도 같은 저장소 안에서 허용한다.

릴리스·핫픽스 PR 본문에는 후속 반영 PR을 연결한다. 이 단계에서 확정된 출시 버전이나 릴리스
브랜치·태그를 미리 만들지는 않는다. 필요할 때 실제 작업과 함께 생성한다.

## 자동 검사

`ci` 워크플로는 푸시와 PR에서 실행한다. PR 대상 변경(`edited`)도 재검사한다.

- `test`: Ruff, mypy, pytest, 분류 평가, 검색 평가.
- `gitflow`: PR의 브랜치 이름과 대상 조합 검사. 잘못된 `feature/* → main` 등은 실패한다.

경로 검사 코드는 PR **대상 브랜치의 커밋**에서 읽는다. PR이 검사 스크립트를 함께 수정해도 그 변경으로
자기 경로를 통과시키지 않는다. 이벤트의 브랜치 이름은 JSON 데이터로 읽으며 셸 명령으로 실행하지 않는다.
검사는 PR 경로를 확인하는 것으로, 실제 분기 시점·릴리스 내용·사람의 검토를 대신하지 않는다.
푸시 이벤트의 `gitflow` 성공은 경로 검사 대상이 아니라는 뜻이며 직접 푸시를 차단했다는 뜻이 아니다.

## 관리자에게 남은 설정

확인 당시 현재 계정은 저장소 쓰기 권한만 있고 관리 권한이 없다. **브랜치 보호와 병합 방식 제한은
아직 원격에 적용되지 않았다.** CI 경로 검사만으로 직접 푸시나 미검토 병합이 강제 차단되지는 않는다.

저장소 소유자/관리자는 `main`, `develop`에 다음 보호 설정을 적용한다.

- PR과 다른 팀원 1명의 승인 필요. 새 커밋은 기존 승인을 해제.
- 최신 대상 브랜치 기준 `test`, `gitflow` 검사 통과 필요.
- 대화 해결 필요, 관리자도 보호 규칙 적용, 강제 푸시·브랜치 삭제 금지.
- Merge commit 허용. 선형 이력 강제는 끄기.

적용할 값은 [.github/branch-protection.json](../.github/branch-protection.json)에 준비했다.
두 검사 이름이 GitHub에서 한 번 실행된 뒤, 저장소 루트에서 관리 권한이 있는 계정으로 실행한다.
이미 다른 보호 정책이 추가됐다면 먼저 비교해 보존할 정책을 함께 반영한다.

```bash
gh api --method PUT repos/dltlghk5571/mean1AI/branches/main/protection --input .github/branch-protection.json
gh api --method PUT repos/dltlghk5571/mean1AI/branches/develop/protection --input .github/branch-protection.json
```

Settings → General의 Pull Requests에서 Squash merge와 Rebase merge를 끄고 Merge commit을 사용한다.
자동 브랜치 삭제는 꺼 둔다. 특히 릴리스·핫픽스는 다른 대상에 반영하기 전에 삭제하지 않는다.
확인 당시 Merge commit은 허용되고 자동 삭제는 꺼져 있으며, Squash/Rebase 제한은 관리자 작업으로 남아 있다.

## 최초 설정 범위

이번 Git Flow 초기화는 기존 `main` 이력을 보존하고, 전략 문서·PR 템플릿·CI 설정을 `main`과 새
`develop`에 같은 기준으로 반영한다. 이 최초 설정 이후 작업은 위 PR 흐름을 따른다. 개인별 빈 작업
브랜치, 출시 태그, Git Flow 확장 프로그램은 설치·생성하지 않는다. 일반 Git 명령만으로 사용할 수 있다.

참고: [Git Flow 원안](https://nvie.com/posts/a-successful-git-branching-model/),
[GitHub 브랜치 보호 API](https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection),
[GitHub PR 이벤트](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request).
