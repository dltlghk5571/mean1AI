# 민원 공개 자료 MCP 서버

2026-09-12 · 도구 계약 v1 · Python MCP SDK 2.2 계열

검수한 생활민원·복지 자료를 MCP 클라이언트에서 검색하고 필요한 입력 항목을 조회한다.
LLM이나 API 키 없이 실행할 수 있다. 현재 전송 방식은 같은 기기에서 자식 프로세스를 실행하는
`stdio`다. 모델 팀은 MCP 클라이언트를 자신의 에이전트에 연결해 이 도구를 사용할 수 있다.

## 제공하는 도구

| 도구 | 입력 | 결과 |
| --- | --- | --- |
| `search_services` | `query`: 최대 2,000자, `limit`: 정수 1~3, 기본 3 | 유효한 승인 자료의 카드·출처·합성 여부·자료 버전 |
| `get_required_information` | 인자 없음, 또는 검색 결과의 `service_id`와 `catalog` | 공통 질문, 또는 해당 업무의 질문과 출처 카드 |

빈 검색어는 유효한 자료 중 최대 3개를 반환한다. 검색은 기존 제목·설명의 어휘 일치 기준선으로,
LLM 분류나 부서 배정이 아니다. 서비스별 질문은 승인된 공개 카탈로그에서만 읽는다.
검색 결과가 없거나 승인 자료가 없으면 그 상태를 반환한다. 조사 후보나 합성 예시를 자동 승인하지 않는다.

개인 민원·사진·시민 세션은 도구에서 제공하지 않는다. 최종 접수와 개인별 판단은 기존 시민
확인·담당자 검토 흐름에 있다. 본인 민원 조회 MCP는 호출자 인증과 민원별 권한을 연결한 후 추가한다.

## 설치와 DB 준비

저장소 루트에서 설치한다. 앱의 기본 설치와 별개인 선택 의존성이며 개발용 `.[dev]`에도
프로토콜 테스트를 위해 같은 SDK가 들어 있다. 기존 HTTP SDK와 함께 설치할 수 있다.

```powershell
.venv\Scripts\python.exe -m pip install -e ".[mcp]"
```

최신 앱을 한 번 시작하면 기존 SQLite DB에 `mcp_tool_audit_events`와 추가 전용 제약을 준비한다.
앱을 이미 실행 중이었다면 최신 코드로 재시작한다. 기존 실행 환경의 `DATABASE_URL`이 가리키는
SQLite 파일을 사용하고, MCP에는 그 파일을 `--database`로 명시한다.

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

합성 자료로 검색을 시연하려면 담당자 화면에서 `app/data/service_catalog_demo.json`을 등록하고
검토자가 직접 승인한다. [카탈로그 검수 방법](SERVICE_DATA_PIPELINE.md)을 따른다. 자료 승인 없이도
연결 검사와 공통 질문 조회는 가능하다. 승인되지 않은 자료는 검색되지 않는 것이 정상이다.

MCP는 `.env`, 모델 키, 담당자 로그인 설정을 읽지 않는다. 파일이 없거나 최신 앱의 카탈로그·
감사 테이블과 감사 보호 제약이 없으면 시작을 거부한다. DB를 자동 생성하거나 경로를 추측하지 않는다.
현재 SQLite만 지원하며 PostgreSQL 연결은 별도 작업이다.

## 연결 검사

별도 터미널에서 실제 사용하는 DB 경로를 지정한다. 아래 기본 경로는 예시다.

```powershell
.venv\Scripts\python.exe -X utf8 -m app.mcp_check --database ./civic_ai.db
```

검사는 MCP 서버를 자식 프로세스로 실행해 도구 목록, `가로등` 검색, 필요 항목 조회까지 수행한 뒤
서버를 종료한다. 전체 호출 제한은 30초이며 SDK의 종료·강제 종료 대기만 정리 단계에 추가될 수 있다.
연결 성공과 자료 유무를 구분하는 결과 예시는 다음과 같다.

```json
{
  "connection": "ok",
  "tools": ["get_required_information", "search_services"],
  "catalog_status": "catalog_unavailable",
  "service_count": 0,
  "question_count": 2,
  "synthetic": false
}
```

직접 서버를 실행하면 stdin으로 MCP 요청을 기다린다. 일반 텍스트를 입력하는 챗봇 터미널이 아니다.
표준 출력은 MCP 통신 전용이며, 종료는 연결한 호스트가 stdin을 닫거나 사용자가 Ctrl+C를 누른다.

```powershell
.venv\Scripts\python.exe -X utf8 -m app.mcp_server --database ./civic_ai.db
```

프로세스 명령줄은 `app.mcp_server`로 식별할 수 있고 Windows 콘솔 제목은
`Seongnam - public services MCP server`다. 연결 검사는 별도의 제목을 사용한다.

## 호스트 연결 예시

호스트가 `command`와 `args` 방식의 stdio 설정을 받는 경우 사용할 구성이다. 설정 파일 위치와
등록 UI는 호스트에 따라 다르다. 실제 Python 실행 파일과 DB의 **절대 경로**로 바꾼다.

```json
{
  "mcpServers": {
    "seongnam-public-services": {
      "command": "C:/project/seongnam-minwon-ai/.venv/Scripts/python.exe",
      "args": [
        "-X", "utf8", "-m", "app.mcp_server",
        "--database", "C:/project/seongnam-minwon-ai/civic_ai.db"
      ]
    }
  }
}
```

모델 팀의 Python 에이전트는 [app/mcp_check.py](../app/mcp_check.py)의 `Client`와
`StdioServerParameters` 사용을 참고한다. 서버와 DB를 에이전트 호스트에서 접근 가능한 환경에
두어야 한다. 이 변경으로 인터넷에 MCP 주소를 공개하거나 사용자 앱 설정에 자동 등록하지 않는다.
동아리 서버 간 원격 연결은 주소·TLS·인증·운영 계정이 준비된 뒤 Streamable HTTP로 확장한다.

## JSON 결과와 자료 변경

MCP의 `structuredContent`와 텍스트 `content`에 동일한 JSON을 반환한다.
계약 버전은 `schema_version="1"`이며 카드의 `source_url`, `source_title`, `synthetic`,
`requires_human_review`를 답변에서 함께 고려한다. 합성 자료를 실제 제도로 설명하지 않는다.

검색 결과의 `catalog`는 다음 항목을 포함한다. 실제 값을 그대로 다음 조회에 전달한다.

```json
{
  "service_id": "DEMO-LIGHT",
  "catalog": {
    "version": "검색 결과의 version",
    "review_id": 123,
    "content_hash": "검색 결과의 64자리 content_hash"
  }
}
```

위 JSON은 필드 설명용이며 실제 해시로 바꾸기 전에는 유효한 요청이 아니다. `catalog`는 비밀값이나
인증 토큰이 아닌 공개 자료 버전 참조다. 내용이 같아도 재승인하면 `review_id`가 달라져 이전 참조를
거부한다. MCP의 업무 조회는 현재 공개 업무 ID와 버전을 확인한다. 시민 웹 에이전트의 기존
“같은 대화 요청에서 검색한 후보 ID만 사용” 검사는 별도로 유지된다.

| `status` | 의미 | MCP `isError` |
| --- | --- | --- |
| `ok` | 검색 또는 질문 조회 완료. 검색 결과는 0개일 수도 있음 | false |
| `catalog_unavailable` | 승인 없음·철회·검수 기한 만료 | false |
| `stale_catalog` | 자료 버전·검토 이력이 변경됨. 다시 검색 필요 | true |
| `not_found` | 요청한 업무가 현재 유효한 자료에 없음 | true |
| `invalid_input` | 미지원 도구, 잘못된 필드·타입·길이·자료 참조 | true |
| `error` | DB·자료 무결성·감사 기록 등 확인 실패. 결과를 공개하지 않음 | true |

## 기록과 실행 경계

호출마다 SQLite 트랜잭션에서 승인 자료를 읽고 `MCPToolAuditEvent`를 기록한다. 기록 항목은
도구 이름, 결과 상태, 자료 버전·검토 ID, 반환한 업무 ID, 시각이다. 검색어·개인정보·세션·
쿠키·모델 추론·호출자의 임의 도구 이름은 기록하지 않는다. 미지원 이름은 `unknown`으로 기록한다.
이 감사 테이블 외에 민원·대화·카탈로그·승인 이력을 생성하거나 변경하지 않는다.

읽기와 감사 저장 사이의 승인·철회를 SQLite `BEGIN IMMEDIATE`로 직렬화한다. 감사 저장에 실패하면
자료를 반환하지 않는다. 이미 반환한 자료를 원격으로 회수하는 기능은 없으므로 에이전트는 오래된
결과를 재사용하지 않고 실제 답변 시점에 필요한 검증을 해야 한다. 입력 검증 오류와 실행 오류는
일반화된 메시지로 반환한다. 동시 도구 실행은 최대 4개이며 DB 잠금 대기는 연결 기본 제한을 따른다.

현재 웹 챗봇은 공유 검색 함수를 직접 사용한다. MCP 서버는 별도 진입점이며 기존 `/plan` 계약을
자동 변경하지 않는다. 모델 호스트의 도구 선택·호출 예산·대화 감사·사용자 확인 흐름은
[에이전트 계약](AGENT_API.md)을 기준으로 연결하고 검증한다.

## 검증

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest tests/test_mcp_tools.py
```

임시 앱·DB·합성 자료로 최신 discovery와 legacy handshake의 실제 stdio 통신, 도구 목록,
구조화 출력, 검색→질문 조회, 실행 중 철회, 재승인, 유효기간, 입력·개인정보 경계,
감사 실패 시 결과 차단과 추가 전용 제약을 검사한다. 외부 HTTP나 실제 모델은 사용하지 않는다.

MCP 구현은 [공식 Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[서버 도구 문서](https://py.sdk.modelcontextprotocol.io/servers/tools/),
[MCP 구조](https://modelcontextprotocol.io/docs/learn/architecture)를 따른다.
