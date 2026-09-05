import json
import runpy
from pathlib import Path

import pytest

POLICY = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "check_gitflow.py"))


@pytest.mark.parametrize(
    ("base", "head", "same_repository", "allowed"),
    [
        ("develop", "feature/chatbot-ui", True, True),
        ("develop", "feature/chatbot-ui", False, True),
        ("main", "feature/chatbot-ui", True, False),
        ("main", "develop", True, False),
        ("main", "release/0.2.0", True, True),
        ("develop", "release/0.2.0", True, True),
        ("main", "hotfix/receipt-lookup", True, True),
        ("develop", "hotfix/receipt-lookup", True, True),
        ("release/0.2.0", "hotfix/receipt-lookup", True, True),
        ("release/0.2.0", "feature/unfinished-work", True, False),
        ("develop", "main", True, True),
        ("develop", "main", False, False),
        ("main", "release/", True, False),
        ("develop", "feature/", True, False),
        ("develop", "chore/update-docs", True, False),
        ("feature/chatbot-ui", "develop", True, False),
    ],
)
def test_gitflow_routes(base: str, head: str, same_repository: bool, allowed: bool) -> None:
    assert POLICY["validate_route"](base, head, same_repository=same_repository) is allowed


def test_push_does_not_require_pull_request_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert POLICY["main"]() == 0


@pytest.mark.parametrize("base", ["develop", "main"])
def test_event_route_changes_when_pr_is_retargeted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base: str
) -> None:
    event = {
        "action": "edited",
        "pull_request": {
            "base": {"ref": base, "repo": {"full_name": "example/project"}},
            "head": {"ref": "feature/chatbot-ui", "repo": {"full_name": "example/project"}},
        },
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    assert POLICY["main"]() == (0 if base == "develop" else 1)


@pytest.mark.parametrize("payload", ["not json", "{}", '{"pull_request": null}'])
def test_missing_event_metadata_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    assert POLICY["main"]() == 1
