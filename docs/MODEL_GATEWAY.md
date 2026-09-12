# 동아리 모델 API 서버 골격

2026-09-12 · 구현: `app/model_gateway.py` · 실제 모델·GPU·서버 주소는 미정

대화 모델과 분류 모델 앞에서 앱의 JSON을 받는 FastAPI 팩터리다. 세 개의 경로를 제공하지만
모델은 대화용·분류용 두 역할이며, 사건 비교는 분류 모델의 별도 작업이다. 현재 구현에는
가중치 로딩·추론 엔진·학습 코드가 없다. 기본값은 `disabled`이고, 연결 시험용 `synthetic`은
고정된 합성 응답만 반환한다. 이번 구현 검증에서는 서버를 시작하지 않았다.

## 요청 경로와 소유권

| 경로 | 입력 → 출력 | 앱 쪽 연결부 |
| --- | --- | --- |
| `POST /v1/agent/plan` | 비식별 초안·대화·도구 결과 → 다음 행동 하나 | `ClubPlanner`, `CHAT_PROVIDER=club` |
| `POST /v1/classify` | 비식별 민원·버전 고정 부서 목록 → 분류 후보 또는 보류 | `ClubClassifier`, `AI_PROVIDER=club`, 지연 처리 필수 |
| `POST /v1/incident/compare` | 선택된 두 기록 → 관계·입력 인용·차이·확인 질문 | `ClubIncidentComparator`, `INCIDENT_COMPARE_PROVIDER=club` |
| `GET /health` | `mode`, `backend_configured` | 모델 호출 없는 설정 상태 확인 |

`backend_configured`는 실제 백엔드 객체를 주입했는지만 뜻한다. GPU 상태나 추론 성공을
확인하는 준비 상태 검사는 아직 없다. API 문서와 OpenAPI 경로는 기본 공개하지 않는다.

앱이 시민 세션·초안 버전·도구 권한·자료 출처·접수 확인·감사를 관리한다. 게이트웨이는
DB에 접근하지 않고 제안만 반환한다. 모델은 임의 SQL, 외부 URL, 접수·배정·공개·사건 연결
명령을 실행할 수 없다. 대화 도구의 실제 실행과 반복 호출도 기존 앱 실행기가 맡는다.
MCP를 추론 엔진과 직접 연결하는 작업은 별도다.

## 팀에 전달할 JSON Schema

- 대화: [요청](contracts/club-plan-request.schema.json), [응답](contracts/club-plan-response.schema.json), [행동 계약](AGENT_API.md)
- 분류: [요청](contracts/club-classification-request.schema.json), [응답](contracts/club-classification-response.schema.json), [설정·처리 흐름](CLUB_CLASSIFIER.md)
- 비교: [요청](contracts/incident-comparison-request.schema.json), [응답](contracts/incident-comparison-response.schema.json), [인용·검토 계약](INCIDENT_COMPARISON.md)

원본은 `app/agent_schemas.py`, `app/classifier_schemas.py`, `app/incident_compare_schemas.py`다.
내보낸 Schema와 원본의 일치는 테스트로 검사한다. 모든 응답은 JSON 객체 하나이며 Markdown,
토큰 스트리밍, 모델의 내부 추론 문장은 받지 않는다. Schema 검증 외에도 요청 일치·허용 ID·
세부 유형·정확한 인용 검사를 통과해야 한다.

## 합성 모드에서 확인할 수 있는 것

- 대화는 기존 `DemoToolPlanner`의 제한된 시연 행동을 반환한다.
- 분류는 항상 `abstained=true`, 빈 후보, `category/subcategory=null`을 반환한다.
- 사건 비교는 항상 `uncertain`과 확인 질문을 반환한다.
- 성공 응답은 모두 `X-Model-Execution: synthetic`이다. 분류 JSON에는
  `execution_mode: synthetic`도 필수로 들어간다. 모델 ID는 `synthetic-`로 시작해야 한다.
- 분류 앱에서 시험하려면 `CLASSIFIER_RESPONSE_MODE=synthetic`을 명시해야 한다.
  결과는 `club_synthetic`으로 저장하고 합성 표시와 담당자 검토를 유지한다.
- 기존 대화·비교 `club` 어댑터는 합성 헤더를 거부한다. UI 시연에는 각각 로컬
  `CHAT_PROVIDER=agent_demo`, `INCIDENT_COMPARE_PROVIDER=demo`를 사용한다. 합성 게이트웨이의
  대화·비교 경로는 ASGI 내부 시험으로 계약을 확인하며, 실제 모델처럼 연결했다고 표시하지 않는다.

이 모드는 키워드 분류기나 가짜 정확도 평가가 아니다. 분류·비교 판단을 수행하지 않는다.

## 게이트웨이 설정과 나중에 수동 실행하는 방법

게이트웨이는 웹 앱의 `.env`를 읽지 않는다. 별도 프로세스의 환경변수 또는 `GatewaySettings`
인자로만 설정한다. 따라서 앱 `.env`에 `MODEL_GATEWAY_*`를 추가하는 것만으로 켜지지 않는다.

| 환경변수 | 기본값 / 허용 범위 |
| --- | --- |
| `MODEL_GATEWAY_MODE` | `disabled`; `synthetic`, `backend` 선택 가능 |
| `MODEL_GATEWAY_API_KEY` | 없음; 활성 모드에 필수 |
| `MODEL_GATEWAY_AGENT_MODEL_ID` | `synthetic-agent-v1` |
| `MODEL_GATEWAY_CLASSIFIER_MODEL_ID` | `synthetic-classifier-v1`; 비교에도 사용 |
| `MODEL_GATEWAY_REQUEST_TIMEOUT_SECONDS` | 10초; 전체 업로드·백엔드 대기 1~30초 |
| `MODEL_GATEWAY_MAX_CONCURRENT` | 프로세스당 2개; 1~4개 |

아래는 **사용자가 서버 실행을 원할 때 이후에 사용할 명령**이다. 저장소 루트의 별도 터미널에서
실행하고 `Ctrl+C`로 종료한다. 다른 작업의 Python 프로세스를 일괄 종료하지 않는다.

```powershell
$Host.UI.RawUI.WindowTitle = 'Seongnam - synthetic model API'
$env:MODEL_GATEWAY_MODE = 'synthetic'
$env:MODEL_GATEWAY_API_KEY = 'local-test-secret-change-before-use'
.venv\Scripts\python.exe -m uvicorn app.model_gateway:serve_factory --factory --host 127.0.0.1 --port 8001 --no-access-log
```

키는 문법 확인용 예시다. 실제 환경의 키는 별도 비밀값으로 주입한다. `serve_factory`는
Windows 콘솔 제목도 지정하며, import 또는 `create_gateway()` 호출 자체는 포트·워커·모델을
시작하지 않는다. 원격 서비스의 HTTPS 종료, 네트워크 접근 제한과 프로세스 관리자는 추후
배포 환경에서 구성한다. 현재 시민 앱의 데모 로그인은 인터넷 공개 운영용이 아니다.

## 실제 추론을 넣을 위치

`ModelBackend`의 아래 세 비동기 메서드에 실제 모델 호출을 구현하고,
`create_gateway(settings, backend=구현한_백엔드)`로 주입한다. `settings.mode`는 `backend`,
백엔드의 `execution_mode`는 `model`이어야 한다. 환경변수만 `backend`로 바꾸면 시작을 거부한다.

```python
async def plan(request: ClubPlanRequest) -> AgentStep: ...
async def classify(request: ClubClassifyRequest) -> ClassificationProposal: ...
async def compare(request: ComparisonRequest) -> ComparisonProposal: ...
```

백엔드에는 요청 사본을 전달한다. 응답의 요청 ID·해시·카탈로그 버전은 게이트웨이가 원본에서
붙이고 제안 객체를 재검증한다. 모델에게 응답 봉투 전체를 생성시키지 않는다. 요청의 임의
모델 경로나 프롬프트를 그대로 실행하지 않고 서버가 설정한 두 모델 별칭만 받는다.

실제 추론은 취소 가능한 비동기 HTTP 클라이언트 등을 사용해야 한다. 동기 GPU 연산을
이벤트 루프에서 돌리면 시간 제한이 연산을 강제 종료하지 못한다. vLLM/Ollama 등 엔진 선택,
모델 컨텍스트·토큰 한도, GPU 메모리, 취소, 프로세스별·전체 동시성은 장비 확정 뒤 검증한다.
모델·프롬프트·가중치가 바뀌면 모델 ID도 새 버전으로 갱신한다.

## 오류와 전송 제한

인증은 `Authorization: Bearer …`이며 본문을 읽기 전에 검사한다. 알려진 직접 식별자 형식을
검출하면 입력을 거부한다. 앱의 사전 마스킹을 대체하는 완전 익명화 기능은 아니다.

| 조건 | HTTP / 오류 코드 예 |
| --- | --- |
| 기본 비활성 / 인증 실패 | 503 `gateway_disabled` / 401 `unauthorized` |
| 동시 처리 한도 초과 | 429 `gateway_busy` |
| 미등록 모델 / 잘못된 JSON·추가 필드 | 404 `unknown_model_id` / 422 `invalid_request` |
| 식별자 / 분류 입력 해시 불일치 | 422 `unredacted_input_rejected` / `input_hash_mismatch` |
| 본문 초과 / JSON 외 형식·압축 | 413 `request_too_large` / 415 |
| 전체 기한 초과 / 잘못된 백엔드 결과 | 504 `gateway_timeout` / 502 `invalid_backend_result` |

대화·분류 요청 최대 200,000바이트, 응답 16,000바이트다. 비교는 요청 32,000바이트,
응답 12,000바이트다. 선언 길이와 실제 스트림을 모두 검사한다. 응답과 오류는 `no-store`이고
민원 본문·키·예외 문자열을 로깅하거나 오류 JSON에 싣지 않는다. 게이트웨이에 감사 DB를
복제하지 않으며, 앱의 요청 전 감사와 결과 검증·저장 계약을 유지한다.

## 4명 역할별 다음 전달물

| 담당 | 서버 사양 확정 전 할 일 | 연결할 때 필요한 전달물 |
| --- | --- | --- |
| A 기획·데이터 | 대표 업무·부서·관할 검수, 개인정보 없는 평가 사례와 정답 | 검수한 카탈로그 버전, 모델 보류·추가 질문 기준 |
| B UI·접근성 | 기존 시연으로 접수·질문·실패 화면 사용성 평가 | 자유 입력 추출의 사용자 확인 흐름, 실제 평가 기록 |
| C 서버 | 이 JSON 계약·인증·오류·큐 설정 검토 | 전체 URL, HTTPS, 비밀값 전달 방식, 타임아웃·취소 검증 |
| D 모델 | 두 역할의 프롬프트·Schema 출력·고정 평가셋 준비 | 모델 ID, `ModelBackend` 구현, 사양·지연·평가 결과 |

다음 개발은 자유 문장에서 목적·장소·질문 답변을 **제안**하는 추출 계약과 사용자 확인이다.
현재 `ask`는 내용·장소 필드만 제안하며, 이 게이트웨이가 자유 문장을 자동 접수 데이터로
변환하는 기능까지 구현한 것은 아니다.
