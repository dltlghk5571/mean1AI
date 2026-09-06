# 동아리 대화 모델 HTTP 연결

2026-09-06 · 전용 JSON 계약 v1 · 내부 행동 계약 v2

`CHAT_PROVIDER=club`을 선택하면 시민 대화의 다음 행동을 동아리 서버에 요청한다. 서버는 도구
호출 또는 종료 행동을 제안하고 앱이 출처·단계·예산을 검증한다. 현재 실제 서버 주소·인증 정보는
제공되지 않아 합성 HTTP 응답으로 연결 코드를 검증했다. 기본값은 계속 `agent_demo`다.

이 전용 계약은 OpenAI 호환 API와 같지 않다. 모델 서빙 앞에 아래 JSON을 처리하는 엔드포인트가
필요하다. 이번 어댑터는 **대화 계획 모델**용이며 접수 후 분류기의 `AI_PROVIDER=rules|openai`는
독립적이다. 별도 동아리 분류 모델 어댑터는 후속 작업이다.

## 설정

커밋하지 않는 `.env`에 넣고 앱을 다시 시작한다. 아래 주소·키는 문법 예시다.

```dotenv
CHAT_PROVIDER=club
CHAT_ENDPOINT_URL=https://your-model-host.example/v1/agent/plan
CHAT_MODEL_ID=your-agent-model
CHAT_API_KEY=your-server-secret
CHAT_REQUEST_TIMEOUT_SECONDS=15
CHAT_TURN_TIMEOUT_SECONDS=30
CHAT_MAX_CONCURRENT=4
```

club 모드에는 주소·모델 ID·키가 모두 필요하다. 원격 주소는 HTTPS만 허용하고, 로컬 시험용
`localhost`, `127.0.0.1`, `::1`에 한해 HTTP를 허용한다. URL에 인증정보·쿼리·fragment를 넣을 수
없다. 인증은 `Authorization: Bearer ...` 헤더를 사용한다. TLS 검증은 유지하며 환경 프록시나
리디렉션을 사용하지 않는다. 인증키를 채팅·GitHub·브라우저 코드에 넣지 않는다.

## 요청과 응답

앱은 설정한 전체 URL로 POST한다. `Content-Type`/`Accept`는 `application/json`,
`X-Request-ID`는 호출마다 생성한 UUID다. 자동 재시도는 없다.

```json
{
  "schema_version": "1",
  "model_id": "your-agent-model",
  "context": {
    "schema_version": "2",
    "stage": "information",
    "draft": {"title": "가로등 문의", "content": "가로등", "location_text": ""},
    "messages": [{"role": "user", "text": "가로등"}],
    "observations": [],
    "remaining_tool_calls": 3,
    "time_budget_seconds": 29.8
  }
}
```

```json
{
  "schema_version": "1",
  "model_id": "your-agent-model",
  "step": {
    "kind": "tool",
    "call": {"name": "search_services", "call_id": "search_1", "query": "가로등", "limit": 3}
  }
}
```

성공은 HTTP 200, JSON이며 `model_id`가 요청과 일치해야 한다. 모델은 한 번에 행동 하나만
반환한다. 앱이 도구를 실행하고 그 결과를 다음 요청의 observations에 넣는다. 최종 답변은
`answer`와 검색 결과 ID, 질문은 `ask`와 허용된 필드 ID로 제안한다. 전체 허용 행동과 제한은
[에이전트 계약](AGENT_API.md)에 있다. 자유 문장·임의 URL·접수 명령은 응답 필드가 아니다.

`app/agent_schemas.py`의 `ClubPlanRequest.model_json_schema()`와
`ClubPlanResponse.model_json_schema()`로 정확한 JSON Schema를 얻을 수 있다.

## 자원·실패 처리

- 요청 직전에 알려진 식별자 형식을 다시 가리고 최근 대화 최대 12개와 현재 초안을 보낸다.
  요청은 최대 200KB, 응답은 최대 16KB다. 압축 응답은 받지 않는다.
- 한 요청의 HTTP 교환에는 asyncio timeout을 적용하고 읽기가 끝나지 않는 스트림도 취소한다.
  실패·취소 후 응답과 HTTP 클라이언트를 닫는다. OS/DNS 처리 등의 종료 지연은 실제 배포 환경에서
  별도로 검증해야 한다.
- 한 시민 행동은 기본 30초 예산·도구 최대 3회·모델 최대 4회다. 남은 시간을 다음 모델 호출에
  전달하며, 기한 뒤의 결과를 저장하지 않는다. 기본 동시 실행은 프로세스당 4개로 제한한다.
- 과부하·시간 초과·잘못된 JSON·모델 ID 불일치는 실패로 반환한다. 시연 응답으로 대체하지
  않으며, 시민 초안과 버전은 유지하고 감사 메타데이터만 기록한다.
- 임의의 동기 Python 제공자 전체를 강제 종료하지는 않는다. 외부 HTTP 어댑터의 시간 제한과
  실행기 결과 폐기, 실제 운영 환경의 동시성·DNS·서버 취소 동작을 구분해 검증한다.

## 모델 팀의 다음 전달물

1. 모델 ID, 전체 엔드포인트 URL, Bearer 인증 지원 여부, 위 JSON의 요청·응답 한 쌍.
2. 도구 결과를 받아 후속 행동을 선택하는지, 입력 길이 제한과 서버의 취소·오류 응답 방식.
3. 고정 평가셋 결과: 잘못된 도구, 허위 ID, 위험 요청, 정보 누락, 범위 밖 요청, 서버 장애.

기본 모델/파인튜닝 모델 선택, 모델 가중치·GPU 작업·실제 서버 배포는 이번 구현에 포함하지 않는다.
