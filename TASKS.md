# 현재 작업과 다음 우선순위

우선순위의 기준은 [GOALS.md](GOALS.md)의 **G0: 시민이 대화로 안내·접수·진행 확인을
마치고 담당자의 반복 업무를 줄이는 것**이다. 아래 기술 이력의 미완료 항목을 위에서부터
자동으로 구현하지 않는다. 사용자의 최신 요청에 맞는 소목표 하나를 선택한다.

## 마지막 완료 · G2.1 가로등 업무의 공개 근거 검수

2026-09-13 · 상태: 검수 기록 작성 완료, **실제 적용은 보류**. 진행 중인 새 소목표는 없다.

- 상위 목표: G2 성남 업무에 맞는 근거 → G0 시민이 담당 부서를 직접 찾는 부담 감소.
- 결과물: [가로등 한 건의 검수 기록](docs/LIGHTING_SOURCE_REVIEW.md).
- 완료 증거: 공식 원본 GET의 확인 시각과 세 구 담당 업무, 분당구 일부 동별 표기를 기록.
  본인확인 이후 신고서·자료별 이용 범위·미확인 관할을 L01~L05로 나눠 담당과 해결 증거를 남김.
- 제외: 전체 사이트 수집, 본인인증·신고 제출, 실제 자료의 자동 공개·배정, 모델 서버 실행.
- 종료: 공개 자료 검수는 여기서 종료. 기관·권한 있는 팀원 확인이 필요한 항목은 A에게
  전달할 목록으로 남겼으며 연락을 발송하지 않음. 후보 JSON·날짜·승인 상태는 변경하지 않음.

G2 전체가 완료된 것은 아니다. 대표 업무 12개 중 한 건의 추가 검수 기록이 생겼고,
해당 업무도 실제 서비스 적용·검색·학습 승인은 미완료다. G5.1 목표 체계 정렬은 이전 완료 이력이다.

## 다음 한 가지 · G3.1 가로등 합성 대화의 라벨 검토 준비

공식 자료의 남은 확인을 기다리는 동안, 기존 [합성 평가셋](docs/PILOT_MODEL_EVALS.md)의
가로등 사례와 관련 경계 사례를 현재 추출·분류 계약에 대조한다. 실제 신고 글을 가져오지 않는다.

- 상위 목표: G3 두 LLM의 실제 협업 → G0 시민이 말한 내용을 정확히 이해하고 필요한 것만 질문.
- 결과물: 사례별 목적·업무 후보·추출 가능한 원문·다음 질문의 대응과 불일치 목록.
- 완료 증거: 현재 계약에서 표현 가능한 항목과 사람이 검토할 라벨 차이를 사례 ID로 설명.
- 제외: 새 모델·평가 플랫폼, 전체 데이터셋 재작성, 파인튜닝, 공식 정답 승인·모델 성능 주장.
- 의존성·종료: 기존 파일만으로 대조하고 A·D가 결정할 불일치를 남기면 종료. 서버 정보는 필요 없음.

이 다음 작업은 아직 시작하지 않았다. 가로등 실자료의 적용은 L01~L05 확인 후 별도 소목표로 한다.

## 중간목표별 소목표와 의존성

| 소목표 | 완료할 결과 | 현재 상태·남은 의존성 | 주 담당 |
| --- | --- | --- | --- |
| G1.1 입력 정리와 시민 확인 | 목적·장소·답변 제안을 확인·수정하고 최종 접수 동의를 별도로 받음 | 연결부·UI·합성 검증 완료, 실제 모델은 G3에 의존 | B·C |
| G1.2 시민 과제의 막힘 해결 | U1~U8의 자력/도움/미완료와 막힌 지점을 기록하고 주요 실패 재확인 | 실제 참여자·실기기·보조기기 검증 대기 | B·A |
| G2.1 대표 업무 자료 검수 | 한 번에 한 업무의 근거를 확인하고 12개 후보의 검수 상태를 누적 | 가로등 추가 검수 기록 완료, 이용 범위·신고서·관할은 보류 | A·C |
| G2.2 검수한 자료의 적용 | 승인 버전·실제 업무 대응이 검색·분류·화면에서 일치 | G2.1 결과, 실제 자료를 허용하는 로더·계약·화면 검수 필요 | C·A |
| G3.1 모델 입력·평가 정답 확정 | 기존 JSON 계약과 합성 라벨의 불일치를 검토한 버전·기록 | 가로등 사례의 계약 대조가 다음 후보, 최종 A·D 검수 대기 | D·A |
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
