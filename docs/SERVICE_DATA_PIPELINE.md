# 공식 자료 수집·검수 계약 v1

2026-09-06 · 구현 기준

공개 문서 추출 → 업무·분류·조직 대응표 작성 → 검수 대기 등록 → 담당자 승인 → 시민 검색 순서다.
수집 성공은 공개 승인이 아니다. 현재 저장소에는 **합성 업무 3개**만 있으며, 실제 성남시 전체
분류표·조직도·복지 DB를 구축한 상태가 아니다. 기존 민원 배정 카탈로그와도 분리되어 있다.

## 현재 수집 범위

`app/services/service_collection.py`의 출처 레지스트리에 다음 두 시작점을 준비했다.

| 출처 ID | 시작점 | 현재 상태 |
| --- | --- | --- |
| `seongnam-handbook` | [성남시 민원편람](https://www.seongnam.go.kr/bbs020405) | 수집 조건·본문 선택자 검수 대기 |
| `seongnam-services` | [성남시 민원/제안/신고](https://www.seongnam.go.kr/pm02020101?curPage=1) | 수집 조건·본문 선택자 검수 대기 |

이번 환경에서 직접 HTTPS 요청은 TLS handshake 오류로 실패했다. 실제 페이지의 선택자와
상세 링크 규칙을 확인하지 못했으므로 `collection_reviewed=false`이며 네트워크 수집은 시작하지
않는다. `contents` 선택자는 합성 HTML에서만 검증했다. 인증·TLS 우회는 하지 않는다.

데이터 담당자가 실제 공개 응답과 이용 조건을 확인하고 선택자·상세 경로·페이지 이동 규칙을
맞춘 뒤, 근거를 남긴 PR로 해당 출처를 활성화한다. 활성화 후에도 매 실행에서 robots.txt를
확인하며, 읽지 못하거나 해당 경로가 금지되면 수집하지 않는다.

- 등록한 HTTPS 호스트·경로만 접근한다. 리디렉션, 임의 URL, 로그인 페이지와 파일 첨부는 수집하지 않는다.
- 기본 최대 3페이지, 설정 상한 10페이지, 실행 예산 45초, 요청 timeout 최대 10초다.
  예산은 각 요청 사이에서 확인하며 진행 중인 소켓 읽기는 해당 timeout의 영향을 받는다.
- 요청 사이 최소 2초를 두고 robots의 crawl-delay와 request-rate가 더 길면 이를 따른다.
- HTML은 1MB·UTF-8·검증할 본문 영역으로 제한한다. 메뉴·스크립트·폼·푸터를 제외하고
  표의 셀을 구분해 텍스트로 추출한다. 표 구조·첨부 각주 대조는 사람이 수행해야 한다.
- 한 번 실행하고 종료한다. 스케줄러·백그라운드 크롤러는 만들지 않는다.

네트워크 없이 합성 HTML 추출을 확인하려면 저장소 루트에서 실행한다.

```powershell
python -m app.collect_services --source seongnam-handbook --input-html tests/fixtures/service_source_synthetic.html --synthetic --output .local/service-imports/demo.json
```

출력은 `review_status=pending`인 문서 목록이다. `.local/`은 Git에서 제외되며 기존 파일을
덮어쓰지 않는다. 실제로 적법하게 확보한 HTML에는 `--synthetic`을 쓰지 말고 원본의 등록된
URL을 `--source-url`로 지정한다. 로컬 파일의 원격 수집일은 알 수 없으므로 `fetched_at=null`,
로컬 처리 시각은 `ingested_at`에 기록한다. 게시일·수정일·시행일을 수집일로 추정하지 않는다.

네트워크 결과에는 페이지별 오류·방문 수·남은 링크와 `completed`가 있다. 이 값은 발견한
허용 링크를 오류 없이 소진했는지만 뜻하며, 사이트 전체 또는 업무 대응표 완성을 뜻하지 않는다.

## 팀이 전달할 JSON

검수용 전체 예시는 `app/data/service_catalog_demo.json`, 엄격한 스키마는
`app/service_data_schemas.py`의 `ServiceBundle`이다. 알 수 없는 필드를 거부한다.

| 목록 | 주요 필드와 의미 |
| --- | --- |
| `documents` | 출처 ID·URL·제목·비식별 본문·본문 SHA-256, 수집/처리/게시/수정 시각, 이용 조건 |
| `taxonomy` | 안정적인 내부 ID, 명칭, 상위 ID, 원본 코드, 근거 문서 ID |
| `organizations` | 조직 ID·명칭·상위 조직·관할·원본 코드·근거 문서 ID |
| `work_assignments` | 업무 ID·조직 ID·업무 설명·관할·근거 문서 ID |
| `services` | 서비스 ID·제목·검수한 설명·분류/업무 ID·적용 지역·유효일·필요 정보·사람 검토 여부 |

모든 연결 ID의 존재, 목록별 ID 중복, 분류·조직의 순환 참조, 유효기간 역전을 검사한다.
`regions`는 현재 `SEONGNAM`, `GYEONGGI`, `KR`만 허용하며 시민별 자격 판단에 사용하지 않는다.
본문 SHA-256은 **공백 정리·비식별화가 끝난 UTF-8 텍스트** 기준이다. 원본 파일 해시와 다르다.

필요 정보 예시:

```json
{
  "field_id": "location_text",
  "question": "어느 공원에서 있었던 일인가요? 주변 시설을 알려 주세요.",
  "required": false
}
```

스키마는 추가 질문 항목을 보관할 수 있지만 현재 채팅에서 실제로 묻고 저장하는 항목은
`content`, `location_text`뿐이다. 사진·시점·업무별 추가 항목은 UI와 상태 계약 확장이 필요하다.
기존 `departments.json`을 변경하지 않으므로 이 자료를 승인해도 민원 분류·배정이 바뀌지 않는다.
원본 분류와 내부 라벨의 다대다 대응표, 조직개편별 유효기간은 후속 확장이다.

## 등록과 공개 API

서명된 담당자 로그인 세션이 필요하며 변경 요청은 `X-CSRF-Token`을 사용한다.

| API | 권한·동작 |
| --- | --- |
| `GET /api/v1/service-catalogs` | 담당자 조회, 현재 공개 버전과 최근 50개 등록 버전 |
| `POST /api/v1/service-catalogs` | 분류 담당 또는 검토 승인, `ServiceBundle` 등록만 수행 |
| `GET /api/v1/service-catalogs/{version}` | 원본 묶음·해시·검수 이력 조회 |
| `POST /api/v1/service-catalogs/{version}/review` | 검토 승인 역할만 공개·철회 |

본문은 최대 2MB다. 같은 버전·같은 내용 재등록은 새 이력을 만들지 않는다. 같은 버전의 다른
내용은 거부한다. 등록 응답의 `status=staged`는 등록 단계의 결과이며, 재등록으로 기존 공개를
철회하지 않는다. 실제 공개 여부는 목록의 `active_version`으로 확인한다.

로컬 합성 자료를 검토하려면 먼저 등록한다. 아래 계정은 공개된 시연 계정이다.

```powershell
$catalogBase = 'http://127.0.0.1:8000'
$catalogSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Method Post -Uri "$catalogBase/login" -WebSession $catalogSession -Body @{username='review.demo'; password='review-demo-2026'} | Out-Null
$catalogIdentity = Invoke-RestMethod -Uri "$catalogBase/api/v1/session" -WebSession $catalogSession
$catalogHeaders = @{'X-CSRF-Token'=$catalogIdentity.csrf_token}
$catalogJson = Get-Content app/data/service_catalog_demo.json -Raw -Encoding utf8
$catalogImport = Invoke-RestMethod -Method Post -Uri "$catalogBase/api/v1/service-catalogs" -WebSession $catalogSession -Headers $catalogHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($catalogJson))
Invoke-RestMethod -Uri "$catalogBase/api/v1/service-catalogs/$($catalogImport.version)" -WebSession $catalogSession
```

내용·출처·이용 조건을 확인한 검토자가 승인한다. 합성 자료에는 실제 정책 내용이 없다.

```powershell
$catalogDecision = @{
  content_hash=$catalogImport.content_hash
  decision='approved'
  review_due_at=[DateTime]::UtcNow.AddDays(30).ToString('yyyy-MM-dd')
  reason='합성 자료 3개의 검색 시연용 공개를 검토했습니다.'
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$catalogBase/api/v1/service-catalogs/$($catalogImport.version)/review" -WebSession $catalogSession -Headers $catalogHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($catalogDecision))
```

승인에는 검토한 `content_hash`, 사유, 오늘부터 365일 이내의 `review_due_at`이 필요하다.
날짜는 UTC 날짜 기준이며 해당 날짜까지 유효하다. 문서의 `retrieval_use`가 모두 `allowed`여야
하며, `training_use`는 별도 속성이다. 검색 승인이 학습 사용을 승인하지 않는다.
알려진 직접 식별자 형식과 명백한 지시문 공격을 검사하지만 사람의 출처·내용 검수를 대체하지 않는다.

철회는 같은 API로 `decision=withdrawn`, 해시와 사유를 보낸다. 현재 공개 버전만 철회할 수 있다.
**공개 버전은 전체 스냅샷 하나**다. 새 승인이 이전 공개를 대체하고, 검수 대기 등록은 공개를
바꾸지 않는다. 만료·철회 시 이전 버전으로 자동 복귀하지 않는다. 필요하면 다시 검토·승인한다.
동시 검수 변경이 감지되면 거부하므로 상태를 다시 읽고 판단한다.

버전과 검수 이력은 `ServiceCatalogVersion`, `ServiceCatalogReview`에 추가만 하며 SQLite
수정·삭제 방지 트리거로 보호한다. 담당자 ID는 로그인 세션에서 결정한다. 별도 검수 UI,
운영용 권한·이관·보존 정책, PostgreSQL 마이그레이션은 후속 작업이다.

## 다음 전달물

- A 기획·데이터: 실제 대표 업무 10~15개, 원본 코드·분류·부서·관할·필요 질문·적용일 검수.
- C 서버·수집: 출처별 이용 조건과 실제 선택자 확인, 첨부 추출·표 대조, API 활용 신청.
- D 모델·평가: 검수된 서비스 ID로 검색하는 예시와 범위 밖·누락 정보·혼합 요청 평가셋.
- B UI·접근성: 필요한 추가 질문 형식, 사진 흐름, 출처 안내에 대한 사용성 검증.
