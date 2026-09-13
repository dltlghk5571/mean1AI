# 현재 작업과 다음 우선순위

우선순위의 기준은 [GOALS.md](GOALS.md)의 **G0: 시민이 대화로 안내·접수·진행 확인을
마치고 담당자의 반복 업무를 줄이는 것**이다. 아래 기술 이력의 미완료 항목을 위에서부터
자동으로 구현하지 않는다. 사용자의 최신 요청에 맞는 소목표 하나를 선택한다.

## 마지막 수행 · G3.1 LLR-04 모호한 장소 처리 기준안

2026-09-13 · 상태: 두 사례의 장소 제안·후속 상태 대조와 기준안 작성 완료. 팀 최종 검수 대기.

- 상위 목표: G3 입력·평가 기준 → G1·G0 정확한 주소를 몰라도 장소 단서를 보완하며 진행.
- 결과물: [LLR-04 기준안](docs/LIGHTING_LABEL_REVIEW.md). 모호한 단서는 원문에 남기고 초기
  추출 `location`을 비워 장소 질문으로 이어가는 권고, 안내 문구와 모를 때 계속하는 기준.
- 완료 증거: pc005·pc072의 장소 제안 5개를 원문 검사·채점·질문에 대조하고 합성 후속 흐름
  6개에서 원문·발견 시점·건너뛰기 보존을 확인. `location_checked`가 위치 정확성은 아님을 기록.
- 제외: 앱·평가 코드·정답·분할 변경, 키워드 차단·지오코딩·주소 정규화·새 모델 연결,
  실제 모델 성능·브라우저·참여자 관찰로 결과 해석, LLR-05~06 동시 수행.
- 의존성·종료: 기준안은 완료. A·D는 장소 단서의 의미, B는 안내·사용성, C는 진행 상태와
  미확정 장소 표시를 검수해야 한다. 현재 앱은 모호한 직접 답변도 다음 단계로 넘기는 한계가 있음.

## 이전 수행 · G3.1 LLR-03 인용 범위 검수안

2026-09-13 · 상태: 두 사례·8개 후보 대조와 검수안 작성 완료. A·D 최종 결정 대기.

- 상위 목표: G3 모델 입력·평가 정답 → G0 시민이 말한 시점 정보를 보존하는지 평가.
- 결과물: [LLR-03 검수안](docs/LIGHTING_LABEL_REVIEW.md). pc027·pc013의 원문·의미·채점 결과와
  사례별로 원래 정답에 `사흘 전 밤부터` / `사흘 전 밤에`를 추가 허용하는 권고안.
- 완료 증거: 수동 후보 8개의 앱 원문 검사·현행 채점 결과 확인, 기존 채점기 검사 32건 통과.
  `밤`처럼 정보가 빠진 값과 해당 원문에 없는 표현을 구분했고 원본 데이터 해시를 확인.
- 제외: 앱·채점기 수정, 정답·분할·버전 변경, 날짜 추론·일괄 정규화, LLR-04~06 동시 수행.
- 의존성·종료: 검수안은 완료. A는 질문에 충분한 의미인지, D는 허용 범위와 평가 영향을 검수해
  결정·검토자·날짜를 남겨야 한다. 실제 반영은 그 후 별도 수행하며 이번 결과는 모델 성능이 아님.

## 이전 수행 · G3.1 LLR-02 답변·건너뛰기 상태 대조

2026-09-13 · 상태: 실제 UI 상태 대조·회귀 검증 완료. 평가 입력 대응 합의는 대기.

- 상위 목표: G3 모델 입력·평가 계약 → G1·G0 이미 답하거나 건너뛴 항목을 불필요하게 다시 묻지 않음.
- 결과물: [LLR-02 후속 대조](docs/LIGHTING_LABEL_REVIEW.md)와 세 답변 경로의 브라우저 회귀 검사.
  직접 답변·이미 적음·추출 확인 후 건너뛰기가 이어보기·수정·같은 주제 선택에도 유지됨을 확인.
- 완료 증거: 관련 API·재질문 평가 검사 47건과 독립 Chromium의 세 경로 통과. 답변 수정은
  명시적으로 선택한 항목만 재개하며, 원문 근거를 지운 추출 답변은 기존 API 검사에서 재확인.
- 제외: 재현되지 않은 상태 손실을 이유로 제품 코드 수정, 모델 서버·새 연결부·범용 다중 턴 플랫폼,
  정답·분할 변경, LLR-03~06 동시 수행. 로컬 서버를 시작하지 않음.
- 의존성·종료: UI 상태 검증은 종료. 원문 전용 추출과 `known_fields`·`skipped_fields`를 받는 평가의
  범위·전달 주체는 C·D 합의와 A 의미 검수 대기. 실제 모델·사용성 효과를 검증한 결과가 아님.

G1.2의 첫 관찰은 2026-09-13 사용자 답변 **“참여자 아직 없음”**에 따라 대기한다.
G0는 실제 자료·모델·사용성과 업무 절감 근거를 확보하기 전까지 미완료다.

## 이전 수행 · G2.1 RD02 수정구 도로 관할 공개 자료 대조

2026-09-13 · 상태: 공개 자료 대조 완료. 관할 확정·A·C 최종 검수는 대기, 실제 적용은 `pending`.

- 상위 목표: G2 성남 업무에 맞는 근거 → G0 시민이 부서를 몰라도 상황을 설명할 수 있는 안내.
- 결과물: [기존 도로·보도 업무 검수](docs/ROAD_SOURCE_REVIEW.md)에 RD02 후속 대조를 추가.
  구청 TF팀 업무표·2026년 업무계획·공식 사업 소식을 비교해 개설 사업과 상시 유지관리를 구분.
- 완료 증거: 추가 원본 GET 시각·출처, PDF 인쇄 140~142쪽의 표·추진 경과·일정 시각 대조.
  TF팀의 수진동 사업 이력과 도로관리팀의 수진동 유지관리 표기, 사송1통 사업기간 차이를 기록.
  동별 전체 관할표·시설 인수인계 기준은 확보하지 못했으므로 RD02 해결로 표시하지 않음.
- 제외: 다른 업무 수집, 실제 데이터 승인·자동 배정 규칙, 기관 제출·모델 평가·새 서버.
- 의존성·종료: 추가 공식 관할 자료 또는 담당 부서 확인과 A·C 검토가 필요. 전달할 기준 세 가지를
  기록하고 공개 대조는 종료. 근거가 오기 전 같은 검색을 반복하지 않으며 실제 적용은 보류.

## 이전 완료 · G1.1 안내 목적의 대화 흐름 수정

2026-09-13 · 상태: 구현·관련 회귀 검증 완료. 실제 모델·참여자 효과 검증은 미실시.

- 상위 목표: G1 쉬운 시민 이용 → G0 복잡한 접수 절차 없이 필요한 정보에 도달.
- 결과물: [LLR-01](docs/LIGHTING_LABEL_REVIEW.md)의 안내 목적에 접수 질문이 붙는 흐름 수정.
  생활불편 주제의 안내는 접수 문항·미입력 목록 없이 자료를 검색한다. 주제와 확인한 내용은 보존하고
  시민이 접수로 전환하면 부족한 항목을 묻는다. 복지 안내용 조건 질문은 유지한다.
- 완료 증거: pc043·pc081의 승인 자료 있음/없음, 주제 재선택, 무단 접수 차단·별도 최종 동의와
  기존 복지·민원 질문 회귀 검증. Chromium에서 안내·이어보기·접수 전환·기존 장소/시점 보존 확인.
- 제외: LLR-02~06의 수정, 정답·분할 변경, 새 모델·서버·검색 플랫폼, 공식 자료 공개.
- 종료: 관련 검증 후 종료하며 라벨 합의는 A·D 검수 목록에 유지. 로컬 서버는 시작하지 않음.

G1의 실제 사용성과 G3의 모델 협업은 아직 완료되지 않았다. 이전 G3.1의
[14건·7가족 라벨 대조](docs/LIGHTING_LABEL_REVIEW.md)는 LLR-02 입력 대응 합의, LLR-03~04
기준안의 최종 결정, LLR-05~06 및 팀 최종 검수가 남아 있다.
G2.1의 [가로등 공식 근거 검수](docs/LIGHTING_SOURCE_REVIEW.md)는 L01~L05 해결 전까지 실제 적용을
보류한다. G5.1 목표 체계 정렬도 이전 완료 이력이다.

## 다음 한 가지 · G3.1 LLR-05 혼합·범위 밖 요청의 다음 행동

- 상위 목표: G3 모델 행동 대응 → G1·G0 여러 불편이나 지원 범위 밖 요청에도 다음 단계를 알 수 있음.
- 결과물: [LLR-05](docs/LIGHTING_LABEL_REVIEW.md)의 pc089·pc074·pc094·pc083 네 사례에서
  평가의 `clarify`·`handoff`와 앱의 보류·목적 선택 흐름을 대조한 기준안.
- 완료 증거: 혼합과 범위 밖의 의미·현행 계약 표현·사용자에게 필요한 다음 질문/안내를 구분하고,
  앱이 지원하지 않는 행동 문자열을 그대로 전달하지 않도록 대응과 결정할 사항을 기록.
- 제외: 범용 복합 민원 분해·새 도구/모델 플랫폼, 외부 기관 전송·자동 거절, 정답 임의 변경,
  LLR-06의 부정·사칭 의미 평가 동시 수행.
- 의존성·종료: 기존 사례·계약·함수로 서버 없이 대조 가능. 기존 문서의 기준안까지 완료하고
  A·B의 안내 기준, C·D의 계약/화면 대응 합의를 남긴다. 실제 구현과 모델 성능은 별도 검증.

G2.1의 RD02는 추가 공식 관할 자료 또는 담당 부서 확인과 A·C 검토 대기다. 문의는 발송하지 않는다.
G1.2의 참여자 미확보도 아래에 유지하며, 이번 두 대기를 가짜 결과·새 자료 플랫폼으로 대체하지 않는다.

## 대기 · G1.2 시민 과제의 첫 실제 관찰

기능 수를 늘리기 전에, 디지털 행정에 익숙하지 않은 참여자 한 명이 기존 U1~U4를 수행할 때
어디서 막히는지 확인한다. 이번 자동 검사를 실제 시민이 쉽게 썼다는 결과로 대신하지 않는다.

- 상위 목표: G1 쉬운 시민 이용 → G0 도움 없이 안내·접수·본인 조회를 마치는 시민 경험.
- 결과물: [기존 사용성 양식](docs/CITIZEN_USABILITY_TEST.md)으로 U1~U4 첫 관찰 결과 한 건.
- 완료 증거: 과제별 자력 완료/도움 후 완료/미완료, 소요시간·도움 횟수·막힌 문구를 기록하고
  가장 큰 막힘 한 가지를 후속 수정 후보로 선택. 한 명의 결과를 전체 시민 효과로 일반화하지 않음.
- 제외: 참여자 없이 결과 작성, 팀원의 자동 검사를 시민 관찰로 표기, 새로운 평가 플랫폼·대규모 UI 개편.
- 의존성·종료: 2026-09-13 사용자 확인으로 참여자가 아직 없다. A·B가 참여자·관찰 시간과 접속 가능한
  시연 환경을 준비해야 한다. 기기·접속 주소도 미확인이다. 연락 발송·로컬 서버 시작은 하지 않는다.
  첫 관찰 기록을 받으면 결과를 검토하고 이번 소목표를 종료.

첫 관찰은 기존 5~8명 모집 계획의 시작이며 완료 인원이 아니다. 자료·모델 담당은 별도로 G2·G3의
의존성을 해결할 수 있다. AI의 다음 구현을 이 문서에서 자동으로 발송하거나 시작하지 않는다.

## 중간목표별 소목표와 의존성

| 소목표 | 완료할 결과 | 현재 상태·남은 의존성 | 주 담당 |
| --- | --- | --- | --- |
| G1.1 입력 정리와 시민 확인 | 목적·장소·답변 제안을 확인·수정하고 최종 접수 동의를 별도로 받음 | 연결부·UI 및 LLR-01 수정·회귀 검증 완료, 실제 모델·사용성은 별도 검증 필요 | B·C |
| G1.2 시민 과제의 막힘 해결 | U1~U8의 자력/도움/미완료와 막힌 지점을 기록하고 주요 실패 재확인 | 09-13 사용자 확인: 참여자 없음. 실제 참여자·실기기·보조기기 검증 대기 | B·A |
| G2.1 대표 업무 자료 검수 | 한 번에 한 업무의 근거를 확인하고 12개 후보의 검수 상태를 누적 | 가로등·도로·RD02 공개 대조 완료. 이용 범위·신고서·관할 확정은 추가 근거와 A·C 검토 대기 | A·C |
| G2.2 검수한 자료의 적용 | 승인 버전·실제 업무 대응이 검색·분류·화면에서 일치 | G2.1 결과, 실제 자료를 허용하는 로더·계약·화면 검수 필요 | C·A |
| G3.1 모델 입력·평가 정답 확정 | 기존 JSON 계약과 합성 라벨의 불일치를 검토한 버전·기록 | 14건 대조·LLR-01 수정·LLR-02 UI 대조·LLR-03~04 기준안 완료. 다음 LLR-05 행동 대응, 팀 최종 결정은 별도 | D·A |
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

LLR-02의 입력 대응, LLR-03~04 기준 반영과 LLR-05~06은 G3.1의 후속 후보다. 이번 기준안의
완료를 막지 않으며, 입력/행동 대응과 라벨 기준을 팀이 합의할 때 해당 변경만 소목표로 선택한다.
LLR-01 앱 수정, LLR-02 UI 상태 검증과 LLR-03~04 기준안은 완료했다.

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
