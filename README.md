# 성남 민원 AI 코파일럿 — MVP

한국어 생활민원을 접수해 개인정보를 마스킹하고, 긴급도를 탐지하고, 민원 유형과 담당 **데모 업무 그룹**을 추천하며, 승인된 지식 문서를 근거로 답변 초안을 만드는 인간검토형 프로토타입입니다.

> 주의: 이 저장소의 부서명·업무분장·처리 지침은 시연용 데이터입니다. 실제 성남시 조직·정책·국민신문고와 연결되어 있지 않으며, 자동 처분·자동 종결 기능도 없습니다. 로그인과 운영 보안이 없는 로컬 데모이므로 인터넷에 공개 배포하지 마세요.

## 지금 되는 것

- 상태별 업무 대시보드와 상호작용형 시민 접수 모달
- 주민등록번호·전화번호·이메일 마스킹
- 화재·가스·붕괴·침수 등 긴급 안전 표현 탐지
- API 키 없는 규칙 기반 분류 또는 선택적 OpenAI 구조화 출력 분류
- 신뢰도·민감 분야 규칙에 따른 자동 배정/사람 검토 분기
- 외부 지도 없이 위치 문구 정규화 및 담당자 확인
- 같은 분야·위치·30일 범위의 유사 민원 후보와 점수 근거 제시
- 로컬 Markdown 지식 검색과 답변 초안
- 담당자의 배정·초안 승인
- 모든 주요 상태 변화의 감사 로그
- SQLite 기반 로컬 실행과 Docker 실행

## 1. 가장 빠른 실행

Python 3.11 이상이 필요합니다.

```bash
cd seongnam-minwon-ai
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

브라우저에서 `http://127.0.0.1:8000`을 엽니다. API 문서는 `/docs`, 상태 확인은 `/health`입니다.

## 2. 프로토타입 화면 둘러보기

1. 업무 홈의 `새 민원 접수 시연`을 누릅니다.
2. 가로등·포트홀·복지 문의·긴급 안전 중 합성 예시를 불러와 `안전 분석 시작`을 누릅니다.
3. 상세 화면에서 비식별 결과, 긴급도, 분류 근거, 위치 정규화 결과와 유사 민원 후보를 확인합니다.
4. 유사 민원 후보는 점수 근거를 살핀 뒤 `중복 후보로 확인` 또는 `서로 다른 민원`으로 기록합니다.
5. 담당 후보를 선택하고 `시민 화면 미리보기`를 확인한 뒤 `내부 검토 완료`로 로컬 감사 로그를 남깁니다.
6. 좌측 메뉴나 대기열 탭에서 검토 필요·긴급·배정 완료·검토 완료 상태를 필터링할 수 있습니다.

화면의 승인 동작은 로컬 SQLite 상태만 변경합니다. 시민에게 답변을 보내거나 실제 행정
시스템을 변경하지 않으며, 예시 외 실제 민원·개인정보를 입력하면 안 됩니다.

### 위치·유사 민원 후보의 의미

위치는 입력된 비식별 문구에 유니코드·공백·문장부호 정규화만 적용합니다. 주소 진위, 관할,
좌표 또는 거리는 확인하지 않습니다. 유사 민원 후보는 다음 네 요소를 로컬에서 계산합니다.

- 분야 일치 30%, 정규화 위치 일치 40%, 30일 내 시간 근접도 15%, 비식별 문구 2-gram
  Jaccard 유사도 15%
- 같은 분야, 같은 정규화 위치, 30일 이내, 총점 70% 이상을 모두 만족할 때만 최대 5건 제시
- 위치가 다르거나 누락되면 문구가 같더라도 후보에서 제외

확인·거절은 후보 관계와 담당자 감사 이벤트만 저장합니다. 원본 민원의 상태나 배정을 바꾸거나
두 민원을 병합·종결하지 않습니다. 외부 지도·정부 시스템·메시징 서비스는 호출하지 않습니다.

## 3. OpenAI 분류기 켜기

`.env`를 다음처럼 수정합니다.

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-5.6
```

모델에 전달되는 본문은 직접 식별자를 마스킹한 뒤의 텍스트입니다. 모델 호출에 실패하면 해당 요청을 자동 처리하지 않고 사람 검토 상태로 남깁니다.

## 4. 테스트와 정적 검사

```bash
pytest
ruff check .
ruff format --check .
mypy app evals tests
python -m evals.run
python -m evals.run --format markdown
```

`python -m evals.run`은 네트워크나 API 키 없이 `rules` 제공자만 사용합니다. 버전이 고정된
합성 JSONL 평가셋에서 라우팅 Top-1/Top-3, 긴급 탐지, 개인정보 마스킹, 사람검토 회피 여부를
측정하며 안전 게이트가 하나라도 실패하면 종료 코드 1을 반환합니다. 카테고리별 최소 표본 수와
정확도 기준도 각각 적용하므로 전체 평균이 개별 분야의 회귀를 숨길 수 없습니다.
`--format markdown`은 기준값 표와 기대 분야→예측 분야 혼동 행렬을 출력합니다. 지표 정의와
현재 게이트는 `docs/EVALS.md`에 있습니다.

## 5. 주요 API

```text
POST /api/v1/complaints
GET  /api/v1/complaints
GET  /api/v1/complaints/{complaint_id}
POST /api/v1/complaints/{complaint_id}/reprocess
POST /api/v1/complaints/{complaint_id}/approve
GET  /api/v1/complaints/{complaint_id}/location
POST /api/v1/complaints/{complaint_id}/location/confirm
GET  /api/v1/complaints/{complaint_id}/duplicate-candidates
POST /api/v1/complaints/{complaint_id}/duplicate-candidates/{candidate_id}/decision
GET  /api/v1/departments
```

예시:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/complaints \
  -H "Content-Type: application/json" \
  -d '{
    "title": "가로등 고장",
    "content": "정자동 공원 입구 가로등 두 개가 꺼져 밤에 위험합니다. 연락처는 010-1234-5678입니다.",
    "location_text": "정자동 공원 입구",
    "channel": "web"
  }'
```

## 6. Codex로 이어서 개발하기

이 저장소 루트에서 Codex를 실행하면 `AGENTS.md`의 안전·검증 규칙을 자동으로 읽습니다. 첫 작업은 `codex-prompts/01-evaluation-harness.md`를 그대로 붙여 넣는 것을 권장합니다.

```bash
codex
```

한 번에 전체 서비스를 맡기기보다 평가셋 → 분류 개선 → 지식 검색 → 관리자 화면 → 외부 연계 순서로 작은 PR을 반복하는 편이 안전합니다. 자세한 단계는 `TASKS.md`에 있습니다.
