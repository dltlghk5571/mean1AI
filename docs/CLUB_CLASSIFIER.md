# 동아리 민원 분류 모델 연결

2026-09-12 · 전용 JSON 계약 v1 · `app/services/club_classifier.py`

`AI_PROVIDER=club`은 접수 후 분류를 동아리 서버에 요청하는 선택적 HTTP 어댑터다.
대화의 `CHAT_PROVIDER`와 사건 비교의 `INCIDENT_COMPARE_PROVIDER`는 별도 설정이다.
현재는 합성 HTTP 및 ASGI 내부 시험만 완료했으며 실제 모델 성능은 측정하지 않았다.

## 설정

웹 앱과 기존 로컬 워커가 같은 설정·DB·카탈로그를 사용해야 한다. 아래는 실제 서버 준비 뒤
커밋하지 않는 앱 `.env`에 설정할 예시이며, 이 URL에 서버가 제공되는 것은 아니다.

```dotenv
AI_PROVIDER=club
AI_DEFERRED_ENABLED=true
CLASSIFIER_ENDPOINT_URL=https://models.example.test/v1/classify
CLASSIFIER_MODEL_ID=your-reviewed-classifier-version
CLASSIFIER_API_KEY=replace-outside-git
CLASSIFIER_RESPONSE_MODE=model
CLASSIFIER_REQUEST_TIMEOUT_SECONDS=10
CLASSIFIER_MAX_CONCURRENT=2
```

주소·모델 ID·키·지연 처리 중 하나라도 빠지면 설정을 거부한다. OpenAI 키는 필요하지 않다.
기본값 `AI_PROVIDER=rules`는 계속 오프라인으로 동작한다. 원격은 HTTPS, 로컬 시험은
`localhost`·`127.0.0.1`·`::1`의 HTTP만 허용한다. URL의 계정·쿼리·fragment를 거부하고
Bearer 헤더로 인증한다. TLS 검증을 유지하며 환경 프록시·리다이렉트는 사용하지 않는다.

합성 게이트웨이의 분류 경로를 시험할 때만 모델 ID를 `synthetic-classifier-v1`, 응답 모드를
`synthetic`으로 명시한다. 응답 봉투의 모드가 설정과 다르면 실패한다. 합성 결과는
`club_synthetic`과 `[합성 응답 시연]`으로 기록한다. 기본 `model`에서 몰래 시연 결과로
대체하지 않는다. [모델 서버 골격과 나중에 실행하는 방법](MODEL_GATEWAY.md)을 참고한다.

## 접수부터 결과 검토까지

1. 시민의 최종 확인 뒤 앱이 민원과 규칙 기반 사전 검토·로컬 초안을 저장한다.
2. 긴급·민감 분야는 사람 검토로 보내고 외부 분류 큐를 건너뛴다. 그 외에는 같은 트랜잭션에
   `AIProcessingJob`을 추가하며, 시민 접수 요청은 모델 응답을 기다리지 않는다.
3. 기존 워커가 작업 선점과 감사를 커밋한 다음 비식별 민원을 HTTP로 전달한다.
   모델 호출 동안 DB 트랜잭션을 유지하지 않는다.
4. 요청·응답과 최신 민원·카탈로그·모델 설정·선점 상태를 검사하고 결과·감사·완료 상태를
   함께 커밋한다. 중간에 내용·모델·합성 모드가 바뀌거나 담당자가 검토하면 늦은 결과를 버린다.
5. 정상 결과도 항상 `needs_review`로 남는다. 신뢰도가 높아도 자동 배정·승인·종결하지 않는다.

워커는 별도로 수동 실행해야 하며 웹 앱이 자동 시작하지 않는다. 기존 명령
`python -m app.worker --once`는 준비된 시도 한 번만 처리한다. 지속 실행은 `--watch`이며
`Ctrl+C`로 종료한다. 실행 안내는 [README의 로컬 지연 처리 큐](../README.md#로컬-지연-처리-큐-m2)를
따른다. 이번 구현에서는 워커 프로세스도 시작하지 않고 `run_once()`를 내부 시험했다.

실패한 HTTP 교환 자체는 재시도하지 않는다. 기존 내구성 큐가 기본 최대 3회, 30초·60초 간격으로
다시 시도한다. 예약 상태는 `queued`, 소진하면 `failed`다. 기존 규칙 초안과 검토 상태를 유지하고
모델 완료로 표시하지 않는다. 개발·시험용 파일형 SQLite만 지원한다.

## 전달하는 JSON

[요청 Schema](contracts/club-classification-request.schema.json)와
[응답 Schema](contracts/club-classification-response.schema.json)를 모델 팀에 전달한다.

| 요청 필드 | 의미 |
| --- | --- |
| `schema_version`, `task` | `"1"`, `"classify"` |
| `request_id`, `input_hash` | 매 호출 UUID와 비식별 입력·카탈로그 계약 해시 |
| `model_id` | 앱이 설정한 모델 버전 |
| `complaint` | 재마스킹한 제목·본문·장소; 본문 최대 20,000자로 잘라 보내지 않음 |
| `catalog` | 버전·원본 SHA-256·합성 표시·보류 부서 ID·활성 부서 목록 |

부서 목록에는 ID·이름·분야·설명·관할 설명·허용 세부 유형만 넣는다. 규칙 키워드, 시민 계정,
민원 ID·접수번호·조회 코드·사진은 전달하지 않는다. 업무분장 ID는 결과 검증 뒤 앱이 현재
카탈로그에서 연결한다. `input_fingerprint()`는 이 계약의 정규화 JSON을 SHA-256으로 계산한다.
게이트웨이는 원본 카탈로그 파일이 없으므로 `source_sha256`을 직접 검증하지 않고 요청 해시를
검사한다. 원본 파일·유효일·활성 버전 검증은 앱과 워커가 담당한다.

응답은 `request_id`, `input_hash`, `model_id`, `catalog_version`, `catalog_sha256`,
`execution_mode`를 정확히 돌려주고 `proposal`에 아래 제안을 넣는다.

```json
{
  "abstained": false,
  "category": "streetlight",
  "subcategory": "가로등·보안등 고장",
  "urgency": "normal",
  "candidates": [
    {"department_id": "ROAD_LIGHTING", "confidence": 0.8, "reason": "합성 조명 고장 제보"}
  ],
  "missing_information": [],
  "evidence_summary": "합성 예시: 조명 고장 담당 업무의 검토가 필요합니다."
}
```

위 코드는 `proposal` 부분만의 합성 예시다. 후보는 최대 3개이고 중복·미등록 ID를 거부한다.
최고 신뢰도 후보의 분야·세부 유형이 카탈로그와 일치해야 하며 NaN·범위 밖 신뢰도·추가 필드·
직접 식별자 형식도 거부한다. 모델이 판단하지 못하면 `abstained=true`, `candidates=[]`,
`category=null`, `subcategory=null`로 반환한다. 앱이 신뢰도 0의 담당자 확인 경로로 바꾼다.

현재 허용 카탈로그는 기존 **합성 업무 그룹**이다. 조사·검수용 대표 업무 12개 서비스 목록과
같은 분류표가 아니며 실제 성남시 전체 조직을 반영하지 않는다. 파일 로더와 모델 계약의
`synthetic=true` 제한을 포함해 검수·매핑·화면을 함께 바꾸기 전에는 실자료로 교체하지 않는다.

## 전송 한도와 감사

- 요청 최대 200,000바이트, 응답 16,000바이트. 압축을 거부하고 실제 스트림 크기도 검사한다.
- 총 HTTP 예산 기본 10초, 1~30초. 동시 실행은 프로세스당 기본 2개, 1~4개다.
- 오류에는 고정 코드만 남긴다. 요청·응답 본문, 키, 엔드포인트, 예외 문자열은 감사하지 않는다.
- 큐가 모델 ID와 `club`/`club_synthetic` 모드·카탈로그 버전·입력 해시를 작업 메타데이터에
  고정한다. 작업 ID·상태는 기존 `ai_job_enqueued`, `ai_job_claimed`, 완료·실패 감사로 추적하고,
  적용된 분류 제공자·카탈로그 근거는 분류 감사에 남긴다.
- 직접 식별자 마스킹은 완전 익명화가 아니다. 지금의 검증 자료는 전부 합성이다.

실제 모델 호출은 `ModelBackend.classify()`에 추후 연결한다. 자유 입력의 안내/접수 목적과
답변 항목 추출은 접수 후 분류와 분리한 [대화 모델 추출·사용자 확인 계약](CHAT_EXTRACTION.md)으로
구현했다. 실제 추론과 평가는 두 역할 모두 아직 미연결이다.
