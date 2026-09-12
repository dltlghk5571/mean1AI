# 현재 작업과 다음 우선순위

우선순위의 기준은 [GOALS.md](GOALS.md)의 **G0: 시민이 대화로 안내·접수·진행 확인을
마치고 담당자의 반복 업무를 줄이는 것**이다. 아래 기술 이력의 미완료 항목을 위에서부터
자동으로 구현하지 않는다. 사용자의 최신 요청에 맞는 소목표 하나를 선택한다.

## 마지막 완료 · G5.1 목표와 작업 기준 정렬

상태: 문서 정렬·링크 검증 완료. 진행 중인 새 소목표는 없다.

- 상위 목표: G5 전체 흐름의 효과 입증 → G0 시민 이용과 담당자 업무 개선.
- 결과물: GOALS에 대목표·중간목표·MVP 완료 기준을 두고, TASKS·AGENTS·README·PR 양식에서 참조.
- 완료 증거: 문서 간 목표·우선순위가 일치하고 링크가 유효하며, 옛 계획과 현재 소목표가 구분됨.
- 제외: 새 제품 기능, 실제 모델 연결·학습, 데이터 수집, 목표 관리 앱이나 자동화 구현.
- 종료 조건: 위 문서 검증과 변경 공유 후 종료. 다음 소목표는 아직 시작하지 않음.

## 다음 한 가지 · G2.1 대표 업무의 공식 자료 검수

**서버 준비 전의 첫 우선순위는 가로등 업무 한 건의 실제 근거를 끝까지 확인하는 것**이다.
현재 조사 후보와 검수 작업표를 재사용하고, 같은 자료를 다시 수집하는 도구부터 만들지 않는다.

- 상위 목표: G2 성남 업무에 맞는 근거 → G0 시민이 담당 부서를 직접 찾는 부담 감소.
- 결과물: 가로등 업무의 출처·이용 범위·시행 여부·담당·관할·필요 정보에 대한 검수 결과 한 건.
- 완료 증거: 확인한 항목의 근거 URL·확인일·검토 사유, 미해결 항목과 다음 담당이 기록됨.
  모든 조건을 확인하지 못하면 `보류`로 남기며 실제 지원·공개 완료로 표시하지 않음.
- 제외: 전체 성남시 크롤링, 새 수집 프레임워크, 실자료 자동 공개·배정, LLM 서버 실행.
- 의존성·종료: 공개 근거로 확인할 수 없는 항목은 A의 실무자 확인 대상으로 남기고 종료.
  이 작업 후 G2의 남은 업무와 G1의 시민 과제 중 다음 병목을 선택.

[현재 검수 미해결 목록](docs/SEONGNAM_DATA_REVIEW.md)을 출발점으로 사용한다.
실제 연결·공개를 위한 로더 변경은 검수 결과가 준비된 뒤 별도 소목표로 한다.

## 중간목표별 소목표와 의존성

| 소목표 | 완료할 결과 | 현재 상태·남은 의존성 | 주 담당 |
| --- | --- | --- | --- |
| G1.1 입력 정리와 시민 확인 | 목적·장소·답변 제안을 확인·수정하고 최종 접수 동의를 별도로 받음 | 연결부·UI·합성 검증 완료, 실제 모델은 G3에 의존 | B·C |
| G1.2 시민 과제의 막힘 해결 | U1~U8의 자력/도움/미완료와 막힌 지점을 기록하고 주요 실패 재확인 | 실제 참여자·실기기·보조기기 검증 대기 | B·A |
| G2.1 대표 업무 자료 검수 | 한 번에 한 업무의 근거를 확인하고 12개 후보의 검수 상태를 누적 | 가로등부터 시작할 다음 후보, 실제 확인·검토 대기 | A·C |
| G2.2 검수한 자료의 적용 | 승인 버전·실제 업무 대응이 검색·분류·화면에서 일치 | G2.1 결과, 실제 자료를 허용하는 로더·계약·화면 검수 필요 | C·A |
| G3.1 모델 입력·평가 정답 확정 | 기존 JSON 계약과 합성 라벨의 불일치를 검토한 버전·기록 | 계약·채점 도구 구현, A·D 라벨 검수 대기 | D·A |
| G3.2 실제 추론 연결 | 대화/추출과 분류/비교가 두 모델 역할로 실제 응답하고 실패를 처리 | GPU·OS·모델 ID·URL·비밀값 전달 방식 미정 | D·C |
| G3.3 기본 모델 평가·필요한 개선 | 고정 평가셋의 예측·오류·지연을 기록하고 개선 전후 비교 | G3.1·G3.2 필요, 파인튜닝 여부는 기준선 이후 결정 | D |
| G4.1 담당자 업무 흐름 확인 | 보완 질문·재분류·중복 현장 처리의 기존 절차와 병목 기록 | 실무자 또는 검토 가능한 업무 사례 필요 | A |
| G4.2 업무 부담 비교 | 같은 과제로 기존 방식과 시제품의 단계·질문·오류 비교 | 기능 구현됨, G4.1과 실제 검증 기록 필요 | A·C |
| G5.1 목표 정렬 | 대목표에서 소목표까지 같은 기준과 종료 규칙 적용 | 문서·링크 검증 완료 | A·팀 전체 |
| G5.2 통합 검증·시연 | GOALS의 6개 대표 흐름을 고정 버전에서 재현하고 실패·한계 기록 | 기존 검사를 재사용, 실제 모델/공식 자료 검증은 G2·G3 필요 | 팀 전체 |
| G5.3 성과 정리 | 시민·직원·모델 평가의 개선과 한계를 근거로 설명 | G1~G4 실제 측정 전에는 완료 처리하지 않음 | A·D |

A 기획·업무/데이터, B 시민 UI·접근성, C 서버·연계, D 모델·평가의 4개 역할이다.
개인 배정은 미정이며 이 표가 다른 팀원에게 작업을 발송하거나 새 AI 작업을 시작하지 않는다.

서버 정보가 없다는 이유로 G3의 연결부를 반복 확장하지 않는다. G2의 자료 검수,
G3.1의 라벨 검토, G1.2의 사용성 관찰은 서버 준비와 독립적으로 진척시킬 수 있다.

## 보류 목록

음성·다국어, 사진 AI 판독, 모든 민원 유형 확대, 좌표·시설 색인·재발 관계, 범용 MCP 확장,
운영 인프라·SSO·기관 제출은 [GOALS의 현재 범위](GOALS.md)에 따라 후속으로 둔다.
새로 발견한 항목은 연결 목표·현재 완료 조건을 막지 않는 이유·다시 검토할 조건만 기록한다.

## 기존 기술 구현 체크리스트 · 이력

아래 M0~M4는 초기 기술 단계 이름이며 GOALS의 G1~G5와 다른 분류다. 체크는 해당 구현의
완료만 뜻한다. 실제 모델 성능·공식 자료 승인·사용성·업무 절감이나 대목표 완료를 뜻하지 않는다.
시민 흐름과 근거 검색은 G1·G2, 모델 연결은 G3, 담당자·중복 처리는 G4, 평가는 G5에 연결한다.

## M0 — Included in this starter

- [x] Local intake UI and JSON API
- [x] PII redaction
- [x] Emergency keyword detection
- [x] Rules provider
- [x] Optional OpenAI structured classifier
- [x] Demo knowledge retrieval and grounded template draft
- [x] Human approval endpoint
- [x] Audit events
- [x] Unit/integration tests and CI

## M1 — Evaluation before smarter automation

- [x] Create a versioned, de-identified evaluation dataset with at least 200 examples
- [x] Add top-1/top-3 routing, urgent recall, PII recall, and abstention metrics
- [x] Add per-category thresholds and confusion reports
- [x] Prevent a deployment when safety-regression gates fail

## M2 — Better routing and duplicate detection

- [x] Import a versioned synthetic department/work-assignment catalog with immutable history
- [x] Reject superseded imports and retired IDs; audit unsafe routes and preserve legacy history
- [x] Add local location normalization and human confirmation
- [ ] Add district/jurisdiction rules and an approved coordinate source
- [x] Add text + normalized-location + time duplicate-candidate scoring
- [x] Add human-confirmed shared field incidents, reversible membership and per-complaint audit
- [x] Add shared progress, explicit private citizen publication/withdrawal and revision checks
- [x] Add opt-in incident comparison HTTP/JSON contract, source validation and staff advisory UI
- [ ] Connect/evaluate the actual comparison LLM and add issue-level links for mixed complaints
- [ ] Add coordinate-distance scoring after privacy and source review
- [x] Introduce a local durable queue for optional expensive AI calls, with bounded retries and human fallback
- [x] Add club classification HTTP adapter with catalog/model checks, explicit synthetic mode and mandatory review
- [x] Scaffold disabled-by-default model API for planning/classification/comparison and export JSON schemas
- [ ] Implement/evaluate actual inference backends once club GPU/server specifications are available
- [x] Add free-text intent/answer extraction contract, source validation, confirmation UI and opt-in transport
- [ ] Evaluate actual extraction LLM, mixed/ambiguous input, and selected-topic extraction

## M3 — RAG and officer console

- [x] Store document metadata, effective dates, approvals, and supersession links
- [x] Add approved/effective lexical retrieval and sentence-level citations
- [x] Compare and add an offline concept hybrid after the lexical safety baseline
- [x] Reject or flag unsupported draft claims
- [x] Add local role-based officer login and append-only review history

## M4 — Production hardening

- [ ] PostgreSQL, migrations, backups, retention jobs
- [ ] Object storage and malware scanning for attachments
- [ ] KMS/secret manager, SSO, RBAC, audit export
- [ ] Observability without complaint-body logging
- [ ] Accessibility, multilingual intake, and load tests
- [ ] Formal privacy, security, legal, and records-management reviews
