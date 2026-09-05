# 데모 부서·업무분장 카탈로그

## 범위와 출처

`app/data/departments.json`은 실제 성남시 조직도나 업무분장표를 옮긴 자료가 아니라 이
저장소만을 위해 작성한 완전 합성 데이터입니다. `synthetic`은 반드시 `true`,
`approval_status`는 반드시 `approved`여야 로더가 수락합니다. 네트워크, 정부 시스템,
지도 또는 외부 데이터 소스는 가져오기와 분류 과정에서 호출하지 않습니다.

현재 버전은 `demo-2026-09-04.v1`입니다. 파일의 SHA-256은 시작할 때 계산되며, API와
불변 가져오기 이력에 함께 저장됩니다. 같은 `catalog_version`에 다른 바이트 내용이 들어오면
애플리케이션은 시작을 중단합니다. 내용을 바꿀 때는 버전도 반드시 올려야 합니다.

## 데이터 구조

카탈로그 최상위 필드는 다음과 같습니다.

- `catalog_version`: 변경 불가능한 버전 ID
- `supersedes`: 후속 버전이 대체하는 현재 버전 ID. 최초 버전은 생략 또는 `null`
- `effective_from`, `effective_until`: 포함 범위의 유효일
- `approval_status`: 이 MVP에서는 `approved`만 허용
- `source_label`: 합성 출처 설명
- `synthetic`: 실제 데이터 유입을 막기 위한 필수 `true` 표지
- `fallback_department_id`: 규칙 근거가 없을 때 사람 검토로 보내는 그룹
- `departments`: 현재 및 비활성 데모 업무 그룹의 전체 스냅샷

각 그룹은 안정적인 ID, 표시명, 분야, 설명, 관할 표지, 활성 상태와 다음 두 목록을 가집니다.

- `work_assignments`: 담당자가 확인할 합성 업무의 안정적인 ID·제목·설명
- `routing_rules`: 세부 유형, 로컬 키워드, 위치 필요 여부, 연결된 업무 ID

부서·업무·규칙 ID는 중복될 수 없습니다. 라우팅 규칙이 같은 그룹에 없는 업무 ID를 참조하거나
비활성 그룹에 규칙이 남아 있으면 시작 단계에서 실패합니다. `SAFETY_DUTY`는 긴급 신호를
표시하는 설명용 그룹일 뿐 일반 키워드 자동 배정 규칙이 없습니다. 긴급·민감 민원의 사람 검토
강제는 카탈로그보다 뒤의 독립 정책 계층에서 계속 적용됩니다.

문자열로 된 불리언, 숫자로 된 날짜, 빈 표시명·출처, 미승인·비합성 자료와 유효일 밖의
카탈로그도 거부합니다. 전체 이력에 걸쳐 같은 부서 ID의 분야, 같은 업무·규칙 ID의 소속 부서를
바꿀 수 없습니다. 삭제·비활성화한 부서와 제거한 업무·규칙 ID는 다시 활성화할 수 없으며,
의미나 소속이 달라진 업무에는 새 ID를 사용해야 합니다. 표시명·설명·키워드는 새 버전에서
변경할 수 있지만, 자유문 설명의 의미까지 기계적으로 검증하지는 않습니다.

## 실행 시 흐름과 감사

규칙 분류기는 코드에 내장된 부서별 키워드 표가 아니라 이 카탈로그의 `routing_rules`를
사용합니다. 모든 후보 스냅샷에는 `catalog_version`과 `work_assignment_ids`가 붙습니다.
`triage_completed` 감사 이벤트에는 최상위 후보의 버전과 업무 ID가, 담당자 승인 이벤트에는
승인 시점의 버전과 선택 그룹의 업무 ID가 기록됩니다.

알 수 없거나 비활성인 부서 후보를 제거해도 남은 후보를 자동 배정하지 않습니다. 제공자가
다른 버전이나 잘못된 업무 ID를 제시하면 현재 카탈로그의 유효한 참고 후보로 정리하되 사람
검토를 강제합니다. 제공자가 버전·업무 ID를 생략하는 기존 인터페이스는 계속 허용하며,
유효한 부서·세부 업무를 확인한 뒤 출처를 붙입니다. 분야 불일치, 확인되지 않은 세부 업무,
누락 정보, 위치 필수 규칙과 조정용 fallback 그룹도 자동 배정을 막습니다.

부서 후보 두 개의 점수 차가 0.05 이하이거나 둘 다 0.90 이상이면 검토 대상입니다. 같은
부서에서 서로 다른 업무 집합을 지지하는 규칙 점수 차가 0.10 이하인 경우도 검토합니다.
민감 분야 후보는 순위에 관계없이 독립 정책을 적용합니다. 모델 신뢰도가 높아도 긴급·민감
규칙을 넘을 수 없습니다. `routing_review_required`와 `triage_completed`에 검토 사유 코드,
카탈로그 버전과 SHA-256을 남깁니다.

SQLite에는 다음 추가형 테이블을 사용합니다.

- `department_catalog_versions`: 버전·유효일·출처 SHA-256·항목 수
- `department_catalog_entries`: 버전별 부서·업무·규칙 전체 스냅샷
- `catalog_import_events`: 이전 버전과 비교한 추가·변경·비활성 그룹 ID
- `departments`: 화면과 외래 키가 사용하는 현재 상태 투영

앞의 세 이력 테이블은 SQLite 트리거가 `UPDATE`와 `DELETE`를 거부합니다. 새 버전을 가져올 때
기존 스냅샷은 유지되고 `departments` 투영만 새 버전에 맞춰 갱신됩니다. 이 구조는 현재 MVP의
추가형 호환 방식이며 정식 마이그레이션 시스템을 대신하지 않습니다.

가져오기 로직은 `app/services/department_catalog.py`에 있습니다. 전용 SQLAlchemy 세션에서
검증·스냅샷·투영·감사 변경을 한 번에 커밋하며 중간 실패는 전부 롤백합니다. 거부된 입력은
가져오기 이력에 성공한 버전으로 남지 않습니다. `app.seed.import_department_catalog`와
`seed_departments`는 기존 스크립트·평가 코드용 호환 진입점으로 유지합니다.

현재 버전은 `catalog_import_events.id`의 추가 순서로 결정합니다. 버전 문자열이나 시스템
시간을 정렬해 최신성을 추측하지 않습니다. 동일 바이트의 **현재 버전** 재실행만 멱등이며,
이미 대체된 버전은 바이트가 같아도 거부합니다. 새 버전은 `supersedes`에 현재 ID를 지정해야
하고 `effective_from`을 이전 버전보다 과거로 옮길 수 없습니다. 같은 유효일의 수정은 허용합니다.

분류와 승인 때도 유효일과 DB의 현재 버전을 다시 확인합니다. 이전 카탈로그를 들고 있는
프로세스는 자동 배정하지 않고 검토 상태와 감사 이벤트를 남깁니다. 그 상태에서의 승인은
`human_review_blocked`로 기록하고 거부합니다. 카탈로그 교체는 로컬 앱을 중지하고 파일을
검증한 뒤 재시작하는 절차이며, 실시간 업로드 API나 외부 자료 동기화 기능은 없습니다.

## M2 마이그레이션·호환 동작

- 기존 `app/data/departments.json`의 v1 바이트는 변경하지 않습니다. 기존 SHA-256과 현재
  버전 이력은 그대로 읽으며, 이전 형식의 현재 버전은 `supersedes` 없이도 재실행할 수 있습니다.
  앞으로 추가하는 후속 버전부터 명시적 `supersedes`가 필요합니다.
- 기존 SQLite 열을 변경하거나 삭제하지 않습니다. 카탈로그 이력이 없는 이전 DB에는
  `create_all`로 이력 테이블을 추가하고 기존 테이블에 추가형 트리거를 설치합니다.
  `supersedes` 연결은 기존 가져오기 이벤트의 `previous_catalog_version`에 저장합니다.
- 초기 가져오기에서 카탈로그 밖의 이전 부서는 비활성화하고 행과 외래 키를 보존합니다.
  새 버전 가져오기 때는 미검토 자동 배정(`assigned`)을 모두 `needs_review`로 바꾸고 배정을
  비운 뒤 각각 `catalog_route_invalidated`를 남깁니다. 같은 현재 버전 재실행은 이를 반복하지 않습니다.
- 기존 후보 JSON·답변·감사·담당자 승인 이력은 소급해서 새 출처로 바꾸지 않습니다.
  `reviewed` 민원은 역사 기록으로 유지합니다. 재분석은 새 버전 후보와 새 감사를 기록하고,
  담당자가 현재 활성 부서를 선택해 승인하면 승인 시점의 출처를 기록합니다.
- 버전 메타데이터 없는 예전 JSON 배열은 거부합니다. ID·합성 출처·유효일·업무 참조를
  갖춘 envelope로 명시적으로 옮겨야 하며 표시명에서 ID나 버전을 추측하지 않습니다.
- 일반 부서 목록 API 형식은 유지됩니다. 카탈로그 API에는 선택적 `supersedes`가 추가됩니다.
  새 의존성은 없고 테스트의 실제 HTTP 전송은 차단하며 OpenAI 출력은 모의 객체로 검증합니다.

## 로컬 확인 명령

PowerShell에서 저장소 루트 기준으로 실행합니다.

```powershell
$env:AI_PROVIDER = "rules"
.venv\Scripts\python.exe -c "from app.config import Settings; from app.services.classifier import DepartmentCatalog; c=DepartmentCatalog.from_json(Settings().departments_path); print(c.catalog_version, c.source_sha256)"
.venv\Scripts\python.exe -m pytest tests\test_department_catalog.py tests\test_catalog_routing_safety.py
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m mypy app evals tests
.venv\Scripts\python.exe -m evals.run
```

로그인한 로컬 데모 세션에서는 `GET /api/v1/departments/catalog`으로 현재 버전, SHA-256,
업무분장과 라우팅 규칙을 확인할 수 있습니다. 일반 `GET /api/v1/departments`는 기존처럼 활성
그룹의 현재 투영만 반환합니다.

## 변경 절차

1. 실제 민원·개인정보·실제 기관 내부 자료가 아닌 합성 항목만 수정합니다.
2. 안정적인 기존 ID의 의미를 재사용하지 않습니다. 의미가 달라지면 새 ID를 만듭니다.
3. `catalog_version`을 새 값으로 올리고 `supersedes`에 현재 버전 ID를 넣은 뒤 유효일을 검토합니다.
4. 카탈로그 테스트와 전체 안전 평가를 실행합니다.
5. 변경·비활성 목록과 민감·긴급 회귀 결과를 검토한 뒤에만 커밋합니다.

이 기능은 실제 관할 판정이나 공식 조직 최신성 확인을 제공하지 않습니다. 실제 자료 도입 전에는
자료 소유자, 갱신 주기, 승인자, 보존 정책, 개인정보·보안 검토와 정식 데이터 마이그레이션이
별도로 필요합니다.
