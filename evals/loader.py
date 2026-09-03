from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from evals.models import (
    AbstentionCase,
    EvaluationSuite,
    PiiCase,
    RoutingCase,
    UrgencyCase,
)

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CaseT = TypeVar("CaseT", bound=BaseModel)


def load_jsonl(path: Path, model: type[CaseT]) -> list[CaseT]:
    cases: list[CaseT] = []
    seen_ids: set[str] = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Unable to read evaluation fixture: {path}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            case = model.model_validate_json(line)
        except ValidationError as exc:
            raise ValueError(f"Invalid fixture at {path}:{line_number}: {exc}") from exc
        case_id = str(getattr(case, "id", ""))
        if case_id in seen_ids:
            raise ValueError(f"Duplicate fixture id at {path}:{line_number}: {case_id}")
        seen_ids.add(case_id)
        cases.append(case)

    if not cases:
        raise ValueError(f"Evaluation fixture is empty: {path}")
    return cases


def load_suite(directory: Path = DEFAULT_FIXTURES_DIR) -> EvaluationSuite:
    suite = EvaluationSuite(
        routing=load_jsonl(directory / "routing.jsonl", RoutingCase),
        urgency=load_jsonl(directory / "urgency.jsonl", UrgencyCase),
        pii=load_jsonl(directory / "pii.jsonl", PiiCase),
        abstention=load_jsonl(directory / "abstention.jsonl", AbstentionCase),
    )

    all_ids = [
        case.id
        for cases in (suite.routing, suite.urgency, suite.pii, suite.abstention)
        for case in cases
    ]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Evaluation fixture ids must be unique across all suites")
    return suite
