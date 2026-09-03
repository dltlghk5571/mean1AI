# 여기서 시작

## 1. 압축을 풀고 PowerShell 열기

```powershell
cd seongnam-civic-ai
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. 처음에는 `.env`의 `AI_PROVIDER=rules`이므로 API 키 없이 작동합니다.

## 2. Git 체크포인트 만들기

새 PowerShell 창에서:

```powershell
cd seongnam-civic-ai
git init
git add .
git commit -m "bootstrap civic complaint AI MVP"
```

## 3. Codex 시작

```powershell
codex
```

Codex에 먼저 다음을 입력합니다.

```text
Read AGENTS.md, README.md, docs/PRD.md, docs/ARCHITECTURE.md, and TASKS.md.
Run the existing tests and explain the current end-to-end flow. Do not edit files yet.
Identify the three highest-risk failure modes and confirm which safety invariants cover them.
```

설명을 확인한 뒤 `codex-prompts/01-evaluation-harness.md` 전체를 붙여 넣어 첫 개발 작업을 시작합니다. 큰 기능부터 붙이지 말고, 먼저 평가셋과 안전 회귀 테스트를 만드는 순서입니다.

## 4. 실제 AI 분류 켜기

`.env`를 수정합니다.

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=본인_API_키
OPENAI_MODEL=gpt-5.6
```

서버를 재시작하면 됩니다. 키를 Git에 커밋하지 마세요.

## 중요한 제한

이 프로젝트에는 로그인·권한관리·CSRF 방어·암호화·보존정책·실제 기관 연계가 없습니다. 인터넷에 공개 배포하지 말고 로컬 시연용으로만 사용합니다.
