"""Validate PR routes using GitHub event metadata, without executing PR content."""

import json
import os
from pathlib import Path


def branch_kind(ref: str) -> str:
    for kind in ("feature", "release", "hotfix"):
        prefix = f"{kind}/"
        if ref.startswith(prefix) and ref[len(prefix) :].strip("/"):
            return kind
    return ref if ref in {"main", "develop"} else "unknown"


def validate_route(base: str, head: str, *, same_repository: bool) -> bool:
    head_kind = branch_kind(head)
    if base == "main":
        return head_kind in {"release", "hotfix"}
    if base == "develop":
        return head_kind in {"feature", "release", "hotfix"} or (head == "main" and same_repository)
    return branch_kind(base) == "release" and head_kind == "hotfix"


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    if event_name == "push":
        print("Git Flow: push checks run; PR routing is checked when a PR is opened or updated.")
        return 0
    if event_name != "pull_request":
        print("Git Flow: unsupported or missing event type.")
        return 1
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        pr = event["pull_request"]
        base = pr["base"]["ref"]
        head = pr["head"]["ref"]
        base_repository = pr["base"]["repo"]["full_name"]
        head_repository = pr["head"]["repo"]["full_name"]
        if not all(
            isinstance(value, str) and value
            for value in (base, head, base_repository, head_repository)
        ):
            raise ValueError("Invalid PR metadata")
    except (KeyError, TypeError, ValueError, OSError):
        print("Git Flow: valid PR branch and repository metadata is required.")
        return 1

    allowed = validate_route(base, head, same_repository=base_repository == head_repository)
    print(json.dumps({"head": head, "base": base, "allowed": allowed}))
    if not allowed:
        print(
            "Allowed: feature/release/hotfix -> develop; release/hotfix -> main; hotfix -> release."
        )
        print("A same-repository main -> develop PR is also allowed for release synchronization.")
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
