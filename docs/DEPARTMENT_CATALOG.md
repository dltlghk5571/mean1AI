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

## 실행 시 흐름과 감사

규칙 분류기는 코드에 내장된 부서별 키워드 표가 아니라 이 카탈로그의 `routing_rules`를
사용합니다. 모든 후보 스냅샷에는 `catalog_version`과 `work_assignment_ids`가 붙습니다.
`triage_completed` 감사 이벤트에는 최상위 후보의 버전과 업무 ID가, 담당자 승인 이벤트에는
승인 시점의 버전과 선택 그룹의 업무 ID가 기록됩니다.

SQLite에는 다음 추가형 테이블을 사용합니다.

- `department_catalog_versions`: 버전·유효일·출처 SHA-256·항목 수
- `department_catalog_entries`: 버전별 부서·업무·규칙 전체 스냅샷
- `catalog_import_events`: 이전 버전과 비교한 추가·변경·비활성 그룹 ID
- `departments`: 화면과 외래 키가 사용하는 현재 상태 투영

앞의 세 이력 테이블은 SQLite 트리거가 `UPDATE`와 `DELETE`를 거부합니다. 새 버전을 가져올 때
기존 스냅샷은 유지되고 `departments` 투영만 새 버전에 맞춰 갱신됩니다. 이 구조는 현재 MVP의
추가형 호환 방식이며 정식 마이그레이션 시스템을 대신하지 않습니다.

## 로컬 확인 명령

PowerShell에서 저장소 루트 기준으로 실행합니다.

```powershell
$env:AI_PROVIDER = "rules"
.venv\Scripts\python.exe -c "from app.config import Settings; from app.services.classifier import DepartmentCatalog; c=DepartmentCatalog.from_json(Settings().departments_path); print(c.catalog_version, c.source_sha256)"
.venv\Scripts\python.exe -m pytest tests\test_department_catalog.py
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
3. `catalog_version`을 새 값으로 올리고 유효일을 검토합니다.
4. 카탈로그 테스트와 전체 안전 평가를 실행합니다.
5. 변경·비활성 목록과 민감·긴급 회귀 결과를 검토한 뒤에만 커밋합니다.

이 기능은 실제 관할 판정이나 공식 조직 최신성 확인을 제공하지 않습니다. 실제 자료 도입 전에는
자료 소유자, 갱신 주기, 승인자, 보존 정책, 개인정보·보안 검토와 정식 데이터 마이그레이션이
별도로 필요합니다.
