# 성남시 복지·조직도 수집기 실행 안내

2026-09-09 · 추출기 `20260909-v2` · 보고서 스키마 `2`

AI 서버 없이 저장된 공개 HTML에서 업무 정보를 추출하고, 빠진 상세 페이지·중복·서로 다른
부서 표기를 대조할 수 있다. 결과는 검수 자료이며, 카탈로그 등록·공개·실제 부서 배정을 하지 않는다.
기존 대표 업무 후보 `seongnam-pilot-20260907-v1`도 이 작업으로 갱신하거나 승인하지 않았다.

## 공식 화면에서 확인한 구조

아래는 2026-09-09 브라우저 DOM을 읽은 관찰 기록이다. 실제 HTTP 응답 원문을 파서에 넣어
검증한 결과는 아니다. 회귀 테스트에는 이 구조를 본떠 만든 **합성 HTML만** 사용한다.

| 화면 | 확인한 구조와 추출 방식 |
| --- | --- |
| [복지 목록](https://www.seongnam.go.kr/wf-pm020101) | `#contentLoad .content-body form#searchVO` 안의 `.board-gallery-body` 카드. 제목·요약·태그·목록 부서 표시와 `.total strong`의 총건수를 읽음 |
| 복지 상세 이동 | `onclick="fn_move_form(숫자)"`에서 숫자만 읽어 `/wf-pm020101/{번호}`로 대응. JavaScript를 실행하지 않으며 임의 함수·변수·추가 명령은 거부 |
| [기초연금](https://www.seongnam.go.kr/wf-pm020101/22006), [경로당 운영](https://www.seongnam.go.kr/wf-pm020101/29001) | `.content-section h3` 제목, 본문 문의처, `.board-view-info`의 하단 부서 표시를 분리 |
| [분당구 건설과](https://www.seongnam.go.kr/pm04041101?deptCode=38100720000&orgSelect=orgSelect02) | `h4` 팀명 뒤 `.table-wrapper > table.content-table`의 `thead`에서 `담당업무` 열을 찾아 추출 |

복지 목록은 조회 당시 **303건**을 표시했다. 이는 복지 목록의 표시 건수이며 전체 민원 분류 수가
아니다. 총건수를 코드에 고정하지 않는다. 생애주기·가구상황·관심주제의 `srchCtgry_WLF_*`
코드는 **복지 검색 UI의 필터 코드**로만 보관하고 내부 민원 분류·부서 ID로 사용하지 않는다.

기초연금 본문 문의처는 노인복지과, 하단 표시는 중원구>사회복지과>희망복지팀으로 다르게
표시됐다. 경로당 운영도 본문 문의처와 하단 부서 표시가 달랐다. 문자열 차이는 검토할 근거이며,
하단이 페이지 관리 담당인지, 실제 업무 담당인지, 잘못된 표시인지 파서가 결정하지 않는다.
표기만 보고 배정하지 않고 [검수 작업표 R02](SEONGNAM_DATA_REVIEW.md)를 따른다.

직접 Python HTTPS robots 요청은 TLS handshake 오류로 실패했고, 브라우저 robots 요청도
클라이언트 차단으로 읽지 못했다. TLS 검증을 끄거나 차단을 우회하지 않았다. 모든 출처는
`collection_reviewed=false`라 네트워크 CLI가 실제 페이지 요청 전에 중단된다.
출처별 robots·이용 조건·실제 HTTP 추출 확인 후 근거를 남긴 PR로 활성화해야 한다.
[성남시 저작권 정책](https://www.seongnam.go.kr/cn050401)만으로 개별 문서의 검색·학습 이용을
일괄 허용하지 않는다. 이번에 확인한 상세 본문 영역에서 이용 표시를 확정하지 못했다.

## 입력과 실행

저장소 루트에서 합성 묶음을 실행한다. 일부 상세를 의도적으로 빠뜨린 예시다.

```powershell
python -X utf8 -m app.collect_services --source seongnam-welfare --input-manifest tests/fixtures/seongnam_collection_manifest_synthetic.json --output .local/service-imports/welfare-demo-v2.json
```

결과는 문서 1개·목록 항목 2개이고 `missing_detail_ids=["9900000002"]`, `remaining_links=2`,
`completed=false`다. 오류 없이 파일을 읽어도 전체 확보로 오인하지 않는 예시다.
같은 출력 경로는 다시 쓸 수 없으므로 재실행할 때 새 파일명을 사용한다.

조직도 합성 파일 한 개도 추출할 수 있다.

```powershell
python -X utf8 -m app.collect_services --source seongnam-organization --input-html tests/fixtures/seongnam_organization_synthetic.html --synthetic --output .local/service-imports/organization-demo-v2.json
```

이 예시는 업무 행 3개를 보존하고 반복된 업무 문구를 알린다. 조직도 전체나 직원 수를 뜻하지
않는다. 등록한 7개 부서 코드 중 1개만 입력했으므로 `completed=false`다.

적법하게 확보한 실제 파일은 Git에서 제외된 별도 폴더에 저장하고, 다음 형식의 manifest를
같은 폴더에 작성한다. `input_html`은 그 폴더 아래의 상대 경로다.

```json
{
  "schema_version": "1",
  "source_id": "seongnam-welfare",
  "synthetic": false,
  "input_kind": "saved_html",
  "pages": [
    {"source_url": "https://www.seongnam.go.kr/wf-pm020101", "input_html": "list-1.html"},
    {"source_url": "https://www.seongnam.go.kr/wf-pm020101/22006", "input_html": "22006.html"}
  ]
}
```

```powershell
python -X utf8 -m app.collect_services --source seongnam-welfare --input-manifest .local/service-inputs/manifest.json --output .local/service-imports/welfare-review-v2.json
```

- 로컬 입력은 네트워크 요청을 하지 않는다. HTML은 UTF-8, 파일당 1MB, manifest는 200KB·최대
  500개 파일로 제한한다. 절대 경로·`..`·폴더 밖으로 연결되는 파일 경로를 거부한다.
- 브라우저가 렌더링한 DOM을 저장했다면 `input_kind=rendered_dom`으로 기록한다.
  단일 파일은 `--input-kind rendered_dom --source-url <등록된 원본 URL>`을 사용한다.
  manifest 모드에서는 `--synthetic`, `--source-url`, `--input-kind`로 내부 정보를 덮어쓸 수 없다.
- 로컬 파일은 원격 수집 시각을 확인할 수 없으므로 `fetched_at=null`이다. `processed_at`과
  `ingested_at`은 로컬 처리 시각이다. `input_sha256`은 **입력 파일 바이트**, 문서의
  `content_hash`는 **정리·직접 식별자 마스킹을 거친 UTF-8 본문**의 해시다.
- `source_url`은 검증한 경로와 쿼리로 정규화한다. 상세 화면의 빈 검색 조건과 페이지 이동
  파라미터는 제거하며, 상세 번호가 URL과 일치하는지 검사한다. 값이 있는 검색 필터·임의
  쿼리·중복 쿼리 키·다른 호스트·첨부 링크는 거부한다.
- 조직도는 `orgSelect02`와 등록 코드 `37900000000`, `38000000000`, `38100720000`,
  `38100730000`, `38100700000`, `38100610000`, `38100540000`만 허용한다. 현재 네트워크
  시작점은 분당구 건설과 한 페이지이며, 7개 전체 대조에는 각 페이지의 로컬 파일을 넣는다.
  이 7개 코드가 성남시 전체 조직은 아니다. 팀명만으로 새 조직 ID·관할을 만들지 않는다.
- 종료 코드 `0`은 **보고서 파일 생성 성공**, `1`은 입력·출력·수집 실패, `2`는 잘못된 CLI
  인자다. 부분 추출도 보고서는 생성될 수 있다. 반드시 `completed`, `errors`, `reconciliation`
  을 확인한다. 표준 출력에는 완료 여부와 오류 수만 요약한다.

## 보고서 판독

| 필드 | 의미 / 후속 작업 |
| --- | --- |
| `documents` | 추출한 본문. 동일 문서 중복은 합치되 같은 URL의 서로 다른 본문 버전은 모두 보존. 이용 조건은 `unknown` |
| `pages[].document_id` | 해당 입력에서 추출한 문서 참조. 복지 목록은 본문 문서가 아니므로 `null` |
| `pages[].records_seen / records_extracted / extraction_complete` | 눈에 띈 카드·업무 행과 추출 성공 수. 구조 변경으로 건너뛴 행을 숨기지 않음 |
| `pages[].listing_items / welfare_filters` | 원본 상세 번호·제목·요약·목록 부서 표시·태그와 UI 필터 코드 |
| `pages[].body_contacts / footer_department` | 본문 문의처 후보와 하단 부서 표시. 실제 담당 부서·관리 역할을 확정한 값이 아님 |
| `pages[].policy_year_mentions` | 본문에 등장한 연도. 게시일·시행일·정책 유효일로 자동 채우지 않음 |
| `pages[].work_rows` | 팀명·담당 업무·행 번호. 이름·전화·직위 열은 저장하지 않고 반복 업무 행은 보존 |
| `expected_total / reported_totals / reported_total_changed` | 페이지별 표시 총수. 서로 다르면 기준 총수를 확정하지 않고 재확보 필요 |
| `undiscovered_item_count / missing_detail_ids / unlisted_detail_ids` | 아직 목록에서 못 찾은 수, 목록에 있으나 상세가 없는 ID, 목록 근거가 없는 상세 ID |
| `duplicate_listing_ids / conflicting_listing_ids` | 페이지 겹침 등으로 중복된 항목과 동일 번호의 제목·요약·부서가 다른 항목 |
| `duplicate_page_urls / conflicting_page_urls` | 정규화 URL 중복과 동일 URL의 입력 해시 차이. 차이가 있으면 완결로 처리하지 않음 |
| `same_body_different_urls` | 여러 URL에서 본문 해시가 같음. URL별 근거는 보존하고 실제 동일 업무인지는 사람이 검토 |
| `list_detail_mismatches / issue_counts` | 목록/상세 제목·부서 차이와 본문/하단 부서 차이 등의 검토 사유 |
| `missing_organization_codes` | 조직도 입력에서 빠진 등록 코드. 전체 성남시 조직 누락 검사라는 뜻은 아님 |

위 표의 대조 필드는 `reconciliation` 아래에 있다. 복지의 `inventory_complete`는 표시 총수와
고유 목록 ID 수가 같고, 그 ID의 상세가 모두 있으며, 목록/상세 충돌·추출 오류가 없어야 참이다.
조직도는 등록된 7개 코드를 모두 추출했는지 검사한다. `completed`는 이 조건에 남은 링크와
입력 오류까지 반영한다. 기존 민원편람 경로는 발견한 링크의 소진 여부만 알 수 있다.
**완결성 판정은 공식 내용의 정확성이나 이용 허락·승인이 아니다.** 부서 표기·연도·관할·이용
조건 검수 사유가 남을 수 있으며 모든 결과는 `review_status=pending`이다.

정해진 본문 선택자가 없거나 표의 헤더를 해석할 수 없으면 추출을 중단한다. 일부 카드나
표 행이 달라지면 유효한 나머지는 보존하고 불완전으로 표시한다. 조직도 이름·전화 열은 구조로
제외하지만 자유 서술 안의 사람 이름까지 자동 제거하는 기능은 아니다. 전화·이메일·주민번호는
기존 마스킹을 적용한다. 실제 민원 기록·로그인 화면·첨부 원문은 수집 범위에 넣지 않는다.

## 팀 전달 순서

1. **C 서버·수집**: 접근 가능한 환경에서 robots·이용 조건을 확인하고 실제 응답 원문을 적법하게
   확보한다. 원본/DOM 구분과 URL을 manifest에 남기고, 합성 테스트와 실제 추출 차이를 검토한다.
2. **A 기획·데이터**: 보고서의 누락·중복·부서 표기·정책 연도·관할을 검수한다. 본문 문의처와
   하단 표시를 같은 역할로 가정하지 않는다. 사람 검토 전 `work_assignment_ids=[]`를 유지한다.
3. **A·C**: 검수한 항목만 새 `ServiceBundle.version`의 분류·조직·업무·서비스로 작성한다.
   이 추출 JSON은 검수 API에 바로 올리는 파일이 아니다. [등록·승인 계약](SERVICE_DATA_PIPELINE.md)을 따른다.
4. **D 모델·평가**: 공식 본문 이용 조건 확인 전에는 [합성 100건](PILOT_MODEL_EVALS.md)으로
   분류·대화 JSON을 맞춘다. 검색 사용 승인과 모델 학습 허용은 각각 확인한다.
5. **B UI·접근성**: [시민 사용성 과제](CITIZEN_USABILITY_TEST.md)를 실행해 질문 이해·모바일
   입력·사진·접수 확인에서 막히는 지점을 기록한다. 자료 검수가 끝날 때까지 기다릴 필요는 없다.
