# 성남 민원 AI 코파일럿 — MVP

한국어 생활민원을 접수해 개인정보를 마스킹하고, 긴급도를 탐지하고, 민원 유형과 담당 **데모 업무 그룹**을 추천하며, 승인된 지식 문서를 근거로 답변 초안을 만드는 인간검토형 프로토타입입니다.

> 주의: 이 저장소의 부서명·업무분장·처리 지침과 계정은 시연용 데이터입니다. 실제 성남시 조직·정책·국민신문고·인증 시스템과 연결되어 있지 않으며, 자동 처분·자동 종결 기능도 없습니다. 로컬 역할 로그인은 데모 경계일 뿐 운영 보안이나 SSO를 대체하지 않으므로 인터넷에 공개 배포하지 마세요.

## 지금 되는 것

- 시민 메인(`/`), 대화형 민원 접수(`/minwon/new`), 직접 작성(`/minwon/form`), 비공개 내 민원 조회
- 시연 챗봇의 내용·장소 질문, 수정 가능한 접수 요약, 최종 확인 후 기존 민원 접수로 연결
- 비식별 대화 이어보기, 초안 버전 검사, 동시 접수·재시도 중복 저장 방지
- 출처·분류·조직·업무·필요 질문 JSON, 본문 추출 CLI와 검수 대기 등록·공개·철회 API
- 검수 자료 검색과 필요 정보 조회 도구, 최대 3회 실행 제한, 출처 카드와 변경 시 결과 폐기
- 담당자 자료 검수 화면(`/staff/service-catalogs`), 공개 목록 조사 후보 12개와 조직 표시 검토
- 선택적 동아리 대화 모델 HTTP 연결, 응답·실행 시간·동시 실행·크기 제한
- 별도 담당자 대시보드(`/staff`)와 담당자용 접수 모달
- 접수번호·조회 코드, 중복 제출 방지, 담당자가 명시적으로 공개한 답변 조회
- 서명 세션, CSRF 검증, 분류 담당·검토 승인·감사 조회 역할 구분
- 주민등록번호·전화번호·이메일 마스킹
- 화재·가스·붕괴·침수 등 긴급 안전 표현 탐지
- API 키 없는 규칙 기반 분류 또는 선택적 OpenAI 구조화 출력 분류
- 신뢰도·민감 분야 규칙에 따른 자동 배정/사람 검토 분기
- SHA-256과 유효일을 검증하는 버전 고정형 합성 부서·업무분장 카탈로그
- 외부 지도 없이 위치 문구 정규화 및 담당자 확인
- 같은 분야·위치·30일 범위의 유사 민원 후보와 점수 근거 제시
- 승인·유효기간·대체관계를 검사하는 로컬 Markdown 지식 검색
- 근거 문서 ID를 문장별로 강제하고 미지원 문장을 제외하는 답변 초안
- 담당자의 배정·초안 승인
- 모든 주요 상태 변화의 감사 로그와 추가만 가능한 검토 승인 이력
- SQLite 기반 로컬 실행과 Docker 실행

## 개발 계획과 팀 협업

공동 저장소: [dltlghk5571/mean1AI](https://github.com/dltlghk5571/mean1AI)

- [대화형 민원 에이전트 개발 계획](docs/CITIZEN_AGENT_ROADMAP.md): 대화 모델·LLM 분류,
  챗봇 UI, 중복 민원과 현장 업무 연결, 4명 역할 분담과 일정.
- [실제 분류·API·데이터 수집 조사](docs/SERVICE_TAXONOMY_AND_SOURCES.md): 성남시 분류,
  담당 부서 사례, 복지 조회 API와 크롤링·검수 방법.
- [성남시 업무 프로세스 조사](docs/SEONGNAM_MINWON_WORKFLOW_RESEARCH.md): 접수 창구,
  담당자 처리 흐름과 추가 확인할 사항.

기본 `CHAT_PROVIDER=agent_demo`는 검수된 로컬 자료를 검색하고 필요 정보를 조회하는 시연
에이전트입니다. `demo`는 기존 고정 질문 제공자, `unavailable`은 미연결 오류 확인용입니다.
`club`은 별도 설정을 요구하는 전용 HTTP 대화 모델 어댑터입니다. 실제 동아리 서버는 미연결이며
자유 입력 의도 분류·공식 데이터 최종 검수·사진·기관 접수는 후속 범위입니다.
출처 레지스트리는 실제 선택자·이용 조건 검수가 끝날 때까지 네트워크 수집을
시작하지 않습니다. 합성 데이터도 자동 공개하지 않습니다.

- [시민 챗봇 HTTP 계약](docs/CHAT_API.md)
- [에이전트 도구 JSON v2와 모델 팀 연결 작업](docs/AGENT_API.md)
- [수집·검수 JSON과 합성 자료 등록·승인 실행 방법](docs/SERVICE_DATA_PIPELINE.md)
- [담당자 화면에서 검수할 성남시 공개 목록 후보](docs/SEONGNAM_REVIEW_CANDIDATES.md)
- [동아리 대화 모델 서버 연결 설정과 전용 JSON 계약](docs/CLUB_MODEL_SERVER.md)

4명은 기획·업무 및 데이터 검수, 시민 UI·접근성, 서버·수집·API 연계, 모델·평가를 나눠 맡습니다.
**Git Flow**를 사용합니다. 작업을 Issue로 정리하고 `develop`에서 만든 `feature/*` 브랜치의 변경을
다른 팀원 한 명이 검토한 뒤 `develop`에 병합합니다. 릴리스는 `release/*`, 긴급 수정은 `hotfix/*`를
통해 `main`과 `develop`에 반영합니다. [브랜치·PR·릴리스 절차와 관리자 설정](docs/GITFLOW.md)을 따르세요.
대화·분류 결과의 JSON 명세를 먼저 합의해 각 영역의 연결 기준으로
사용합니다. API 키·실제 민원 원문·모델 가중치는 저장소에 포함하지 않습니다.

## 1. 가장 빠른 실행

Python 3.11 이상이 필요합니다.

```bash
git clone https://github.com/dltlghk5571/mean1AI.git
cd mean1AI
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
copy .env.example .env
uvicorn app.main:app --reload
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 열면 시민 메인이 나옵니다. 회원가입 없이 민원을
작성할 수 있습니다. 담당자 업무는 `/staff` 또는 `/login`에서 아래 합성 계정으로
로그인합니다. API 문서는 `/docs`, 상태 확인은 `/health`입니다.

| 역할 | 아이디 | 비밀번호 | 허용 작업 |
|---|---|---|---|
| 분류 담당 | `triage.demo` | `triage-demo-2026` | 접수, 재분석, 위치·중복 검토 |
| 검토 승인 | `review.demo` | `review-demo-2026` | 분류 담당 권한 + 내부 검토 승인·시민 답변 공개 |
| 감사 조회 | `audit.demo` | `audit-demo-2026` | 민원·감사·승인 이력 읽기 전용 |

계정과 비밀번호는 실제 비밀정보가 아니라 저장소에 공개된 시연 값입니다. 개발 모드는 서버를
재시작하면 담당자 세션이 무효화되는 임시 서명 키를 만듭니다. 고정된 로컬 세션이 필요하면 커밋하지
않는 `.env`에 충분히 긴 `SESSION_SECRET`을 설정하세요. `APP_ENV=production`은 이 값이 없으면
시작을 거부하지만, 이 프로토타입 자체는 운영 배포 대상이 아닙니다.

## 2. 프로토타입 화면 둘러보기

1. 시민 메인에서 `대화로 민원 시작하기`를 누르고 “가상 데모공원 가로등이 어제부터 꺼졌어요”를
   입력합니다. `민원으로 접수할게요`를 선택한 뒤 발생 장소를 알려 줍니다.
2. 접수 요약을 확인·수정한 뒤 데모 안내에 동의하고 접수합니다. 대화는 식별자 형식을 가려
   로컬 DB에 저장하며, 최종 확인 전에는 민원 기록을 만들지 않습니다. 직접 작성 화면의
   미리보기는 저장하지 않고, 그 양식의 원문은 최종 접수 때 로컬 DB에 저장됩니다.
3. 접수번호와 조회 코드를 보관합니다. 같은 브라우저에서는 `내 민원 조회`에 바로 표시되고,
   다른 브라우저에서는 번호와 코드를 함께 입력해야 해당 민원에 접근할 수 있습니다.
4. `/staff`에서 검토 승인 계정으로 로그인해 접수된 민원을 엽니다. 비식별 내용, 긴급도,
   분류 근거, 위치·유사 민원 후보를 확인하고 업무 그룹과 답변을 내부 검토합니다.
5. `내부 검토 완료`는 시민에게 답변을 공개하지 않습니다. 별도 `시민에게 답변 공개` 패널에서
   저장된 답변을 확인하고 동의한 후 `시민 화면에 답변 공개`를 누릅니다.
6. 시민의 내 민원 상세를 새로고침하면 공개한 답변이 표시됩니다. 이후 내부 초안을 변경하거나
   재분석해도 이전 공개 답변은 유지되고, 다시 명시적으로 공개해야 새 버전으로 바뀝니다.

시민 세션은 쿠키 생성 시점부터 30일간 유효합니다. 원래 브라우저의 유효한 세션에서는
접수 확인서를 다시 열어 조회 코드를 볼 수 있습니다. 코드와 해당 세션을 모두 잃으면 이
데모에서 복구할 수 없습니다. 작성 내용은 URL이나 브라우저 영구 저장소에 저장하지 않습니다.

기존 담당자용 접수 모달과 대기열 상태 필터·검색·정렬도 `/staff`에서 계속 사용할 수 있습니다.

목록 검색은 제목·위치·분야에 적용됩니다. 입력란 밖에서 `/`를 누르면 검색창으로 이동하고,
검색창에서 `Esc`나 지우기 버튼을 누르면 검색을 초기화합니다. 여러 검색어는 모두 포함된
민원을 찾습니다. 최신순·오래된순·검토 우선순 정렬은 **선택한 상태의 최근 30건** 안에서만
적용되며 전체 DB 검색이나 배정 변경이 아닙니다. 검색어는 URL·브라우저 저장소에 저장하지 않습니다.

상세 화면의 고정 구간 메뉴로 접수 내용, 위치·유사 민원, 분류 근거, 답변 초안, 담당자 검토,
처리 이력을 바로 열 수 있습니다. 좁은 화면의 메뉴는 키보드 포커스를 메뉴 안에 유지하며
`Esc`로 닫힙니다. 화면 검증 절차와 범위는 [docs/UI.md](docs/UI.md)에 정리했습니다.

행위자 ID는 폼 입력값을 신뢰하지 않고 로그인 세션에서 결정합니다. 검토 승인은 별도
`ReviewDecision` 행으로 계속 추가되며 기존 승인·감사 행은 SQLite 트리거가 수정과 삭제를
차단합니다. 역할·세션·CSRF·이력의 상세 범위는 `docs/AUTH_AND_AUDIT.md`에 있습니다.

내부 승인과 시민 답변 공개는 로컬 SQLite 상태만 변경합니다. 공개한 답변은 해당 민원의
접근 권한이 있는 브라우저에서만 조회하며, 문자·이메일 발송이나 실제 행정 시스템 변경은
없습니다. 예시 외 실제 민원·개인정보를 입력하면 안 됩니다.

### 위치·유사 민원 후보의 의미

위치는 입력된 비식별 문구에 유니코드·공백·문장부호 정규화만 적용합니다. 주소 진위, 관할,
좌표 또는 거리는 확인하지 않습니다. 유사 민원 후보는 다음 네 요소를 로컬에서 계산합니다.

- 분야 일치 30%, 정규화 위치 일치 40%, 30일 내 시간 근접도 15%, 비식별 문구 2-gram
  Jaccard 유사도 15%
- 같은 분야, 같은 정규화 위치, 30일 이내, 총점 70% 이상을 모두 만족할 때만 최대 5건 제시
- 위치가 다르거나 누락되면 문구가 같더라도 후보에서 제외

확인·거절은 후보 관계와 담당자 감사 이벤트만 저장합니다. 원본 민원의 상태나 배정을 바꾸거나
두 민원을 병합·종결하지 않습니다. 외부 지도·정부 시스템·메시징 서비스는 호출하지 않습니다.

### 데모 업무분장 기준

규칙 분류의 키워드, 세부 유형과 업무 ID는 `app/data/departments.json`의 버전 고정형 합성
카탈로그에서 읽습니다. 같은 버전의 파일 내용이 바뀌면 SHA-256 불일치로 시작을 거부하며,
새 버전의 부서·업무·규칙 스냅샷과 변경 요약은 SQLite 추가형 이력으로 보존됩니다. 민원 후보와
분류·담당자 승인 감사 이벤트에도 적용된 카탈로그 버전과 업무 ID가 남습니다.

이는 실제 성남시 조직·업무 자료나 관할 판정이 아닙니다. 스키마, 버전 변경 절차와 검증 명령은
`docs/DEPARTMENT_CATALOG.md`에 있습니다.

## 3. OpenAI 분류기 켜기

`.env`를 다음처럼 수정합니다.

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-5.6
```

모델에 전달되는 본문은 직접 식별자를 마스킹한 뒤의 텍스트입니다. 모델 호출에 실패하면 해당 요청을 자동 처리하지 않고 사람 검토 상태로 남깁니다.

### 로컬 지연 처리 큐 (M2)

비싼 분류·초안 처리를 요청 처리와 분리하려면 위 OpenAI 설정에 다음을 추가합니다.
기본값은 `false`이며, `AI_PROVIDER=rules`는 이 옵션을 켜도 계속 동기·오프라인으로 작동합니다.

```dotenv
AI_DEFERRED_ENABLED=true
AI_QUEUE_MAX_ATTEMPTS=3
AI_QUEUE_RETRY_SECONDS=30
AI_QUEUE_LEASE_SECONDS=120
```

웹 서버와 워커는 **같은 저장소 디렉터리에서 같은 `.env`와 SQLite 파일**을 사용해야 합니다.
지연 모드는 `development`/`test` 환경의 로컬 파일형 SQLite만 허용하며, API 키 누락은 시작 시
오류로 처리합니다. 큐용 서버·브로커·외부 저장소는 추가하지 않습니다. OpenAI를 명시적으로
설정한 경우에만 기존 분류 제공자를 호출하며 실제 정부 시스템·메시징 연계는 없습니다.

```bash
# 준비된 작업의 한 시도만 처리한 뒤 종료 (없으면 idle)
python -m app.worker --once
# 별도 로컬 터미널에서 계속 처리, Ctrl+C로 종료
python -m app.worker --watch --poll-seconds 2
```

접수 시 비식별화, 긴급·민감 여부 확인, 규칙 분류와 로컬 근거 초안은 즉시 제공됩니다.
긴급·민감 민원은 비싼 AI 큐를 건너뛰고 바로 사람 검토로 넘어갑니다. 일반 민원도 큐에 들어가면
담당자 검토 상태를 유지하며 AI 완료만으로 승인·종결·외부 발송하지 않습니다.

목록·상세 화면과 API의 `ai_processing.state`는 `queued`, `processing`, `completed`, `failed`를
구분합니다. 상세 화면의 **상태 새로고침**으로 갱신하며 작업 시도 횟수와 최대 횟수도 표시합니다.
큐를 사용하지 않은 기존·규칙 처리 민원은 `ai_processing: null`입니다. 작업 이력은
`GET /api/v1/complaints/{id}/ai-processing`에서 최신순으로 조회합니다.

실패는 기본 30초, 60초 간격으로 재시도하고 세 번 소진하면 규칙 초안을 유지한 채 사람 검토로
남습니다. 중단된 워커의 선점은 기본 120초 후 만료되며 다음 워커 실행이 복구합니다. 만료된
시도도 횟수에 포함됩니다. 워커 실행 없이 큐가 스스로 처리되지는 않습니다. `--once`의 종료 코드는
정상/대기/재시도 예약 0, 최종 실패 1, 설정 오류 2입니다.

`POST /api/v1/complaints/{id}/reprocess`는 활성 작업이 있으면 그 작업을 반환합니다. 완료·실패 후
명시적으로 새 분석을 요청할 때 API는 UUID 형식의 `Idempotency-Key` 헤더를 사용할 수 있습니다.
같은 민원·키의 재전송은 완료 후에도 작업·시도·감사 기록을 추가하지 않습니다. 키를 생략한 요청은
활성 작업이 없을 때 새 분석으로 취급합니다. HTML 폼은 이 키를 자동 생성합니다. 새 키로 다시
분석하면 새 작업의 제한 횟수가 적용되며 과거 작업 이력은 남습니다.

대기·처리 중에도 검토 승인 계정으로 직접 검토할 수 있습니다. 승인하면 해당 AI 작업은
`failed`/`human_review_superseded`로 종료하고 늦게 도착한 결과는 버립니다. 큐의 실패 상태와
민원의 검토 완료 상태는 별도로 표시됩니다. 선점·실패·완료와 담당자 결과 보호의 상세 설계는
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)에 있습니다.

## 4. 테스트와 정적 검사

```bash
pytest
ruff check .
ruff format --check .
mypy app evals tests scripts/check_gitflow.py
python -m evals.run
python -m evals.run --format markdown
python -m evals.rag_run
python -m evals.rag_run --format markdown
```

`python -m evals.run`은 네트워크나 API 키 없이 `rules` 제공자만 사용합니다. 버전이 고정된
합성 JSONL 평가셋에서 라우팅 Top-1/Top-3, 긴급 탐지, 개인정보 마스킹, 사람검토 회피 여부를
측정하며 안전 게이트가 하나라도 실패하면 종료 코드 1을 반환합니다. 카테고리별 최소 표본 수와
정확도 기준도 각각 적용하므로 전체 평균이 개별 분야의 회귀를 숨길 수 없습니다.
`--format markdown`은 기준값 표와 기대 분야→예측 분야 혼동 행렬을 출력합니다. 지표 정의와
현재 게이트는 `docs/EVALS.md`에 있습니다.

`python -m evals.rag_run`은 36개 합성 검색 사례로 분야-only 기준, 엄격 어휘 기준선,
오프라인 개념 하이브리드를 비교합니다. 하이브리드는 고정 평가셋에서 정밀도·재현율·변형 문장
재현율·무관 문서 기권율이 모두 100%이고, 한 단어만 겹치는 단일 신호 함정 사례 8건도 모두 거절합니다.
학습 모델이나 임베딩 서비스는 사용하지 않습니다. 지표 정의와 한계, 문장별 인용 검증 규칙은
`docs/RAG.md`에 있습니다.

## 5. 주요 API

```text
POST /api/v1/complaints
GET  /api/v1/complaints
GET  /api/v1/complaints/{complaint_id}
GET  /api/v1/complaints/{complaint_id}/grounding
GET  /api/v1/complaints/{complaint_id}/reviews
GET  /api/v1/complaints/{complaint_id}/ai-processing
POST /api/v1/complaints/{complaint_id}/reprocess
POST /api/v1/complaints/{complaint_id}/approve
GET  /api/v1/complaints/{complaint_id}/location
POST /api/v1/complaints/{complaint_id}/location/confirm
GET  /api/v1/complaints/{complaint_id}/duplicate-candidates
POST /api/v1/complaints/{complaint_id}/duplicate-candidates/{candidate_id}/decision
GET  /api/v1/departments
GET  /api/v1/departments/catalog
GET  /api/v1/session
GET  /api/v1/service-catalogs
POST /api/v1/service-catalogs
GET  /api/v1/service-catalogs/{version}
GET  /api/v1/service-catalogs/candidates/{name}
POST /api/v1/service-catalogs/{version}/review
```

민원 API는 로그인 쿠키와 변경 요청의 CSRF 헤더가 필요합니다. PowerShell 예시:

```powershell
$demoSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8000/login `
  -WebSession $demoSession -Body @{username='review.demo'; password='review-demo-2026'}
$demoIdentity = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/session -WebSession $demoSession
$demoHeaders = @{'X-CSRF-Token'=$demoIdentity.csrf_token}
$demoBody = @{title='합성 가로등 고장'; content='가상 시험동 가로등 점등 불량'; `
  location_text='가상 시험동 1번 위치'; channel='web'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/complaints `
  -WebSession $demoSession -Headers $demoHeaders -ContentType 'application/json' -Body $demoBody
```

## 6. Codex로 이어서 개발하기

이 저장소 루트에서 Codex를 실행하면 `AGENTS.md`의 안전·검증 규칙을 자동으로 읽습니다. 첫 작업은 `codex-prompts/01-evaluation-harness.md`를 그대로 붙여 넣는 것을 권장합니다.

```bash
codex
```

한 번에 전체 서비스를 맡기기보다 평가셋 → 분류 개선 → 지식 검색 → 관리자 화면 → 외부 연계 순서로 작은 PR을 반복하는 편이 안전합니다. 자세한 단계는 `TASKS.md`에 있습니다.
