import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import service_collection as collection
from app.services.service_collection import CollectionError, collect, extract_document

SOURCE = collection.SOURCES["seongnam-handbook"]
HTML = (Path(__file__).parent / "fixtures/service_source_synthetic.html").read_bytes()


class FakeFetcher:
    def __init__(self, robots: bytes = b"User-agent: *\nAllow: /\n") -> None:
        self.robots = robots
        self.urls: list[str] = []

    def get(self, url: str, *, max_bytes: int, timeout: float) -> tuple[bytes, str]:
        self.urls.append(url)
        if url.endswith("robots.txt"):
            return self.robots, "text/plain"
        return HTML, "text/html"


def test_extracts_only_main_content_and_preserves_unknown_dates() -> None:
    doc, links = extract_document(SOURCE, SOURCE.seed_url, HTML, synthetic=True)
    assert doc.synthetic and doc.source_url is None and doc.fetched_at is None
    assert doc.published_at is None and doc.updated_at is None
    assert doc.retrieval_use == "unknown" and doc.training_use == "unknown"
    assert "010-1111-2222" not in doc.text and "[전화번호]" in doc.text
    assert "수집하면 안 되는" not in doc.text
    assert links == ["https://www.seongnam.go.kr/bbs020405/100001"]


@pytest.mark.parametrize(
    "url",
    [
        "http://www.seongnam.go.kr/bbs020405",
        "https://www.seongnam.go.kr.evil.test/bbs020405",
        "https://www.seongnam.go.kr/private",
        "https://www.seongnam.go.kr/bbs020405?token=secret",
        "https://user:password@www.seongnam.go.kr/bbs020405",
        "https://127.0.0.1/bbs020405",
    ],
)
def test_collection_scope_rejects_unregistered_urls(url: str) -> None:
    with pytest.raises(CollectionError):
        extract_document(SOURCE, url, HTML)


@pytest.mark.parametrize(
    "data",
    [b"<html>Login required</html>", b"x" * 1_000_001, b"\xff\xfe"],
    ids=["missing-main", "oversize", "invalid-encoding"],
)
def test_size_encoding_and_missing_main_fail_closed(data: bytes) -> None:
    with pytest.raises(CollectionError):
        extract_document(SOURCE, SOURCE.seed_url, data)


def test_unreviewed_collection_does_not_make_network_requests() -> None:
    fetcher = FakeFetcher()
    with pytest.raises(CollectionError, match="review_required"):
        collect(SOURCE, fetcher)
    assert fetcher.urls == []


@pytest.mark.parametrize("robots", [b"User-agent: *\nDisallow: /", b"<html>unavailable</html>"])
def test_robots_denial_or_unknown_prevents_document_fetch(robots: bytes) -> None:
    fetcher = FakeFetcher(robots)
    try:
        result = collect(replace(SOURCE, collection_reviewed=True), fetcher)
        assert result["documents"] == []
    except CollectionError as exc:
        assert str(exc) == "robots_not_verified"
    assert len(fetcher.urls) == 1


def test_page_budget_and_robot_rate_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []
    monkeypatch.setattr(collection.time, "sleep", delays.append)
    fetcher = FakeFetcher(b"User-agent: *\nAllow: /\nCrawl-delay: 3\nRequest-rate: 1/4\n")
    result = collect(replace(SOURCE, collection_reviewed=True), fetcher, max_pages=1)
    assert result["review_status"] == "pending" and result["visited"] == 1
    documents = result["documents"]
    assert isinstance(documents, list)
    assert len(documents) == 1 and result["completed"] is False
    assert len(fetcher.urls) == 2 and delays == [4.0]
    assert documents[0]["fetched_at"] is not None


def test_deadline_prevents_fetch_when_robot_delay_exceeds_budget() -> None:
    fetcher = FakeFetcher(b"User-agent: *\nAllow: /\nCrawl-delay: 50\n")
    result = collect(replace(SOURCE, collection_reviewed=True), fetcher, time_limit=5)
    assert result["visited"] == 0 and result["completed"] is False
    assert len(fetcher.urls) == 1


def test_local_cli_is_pending_and_never_overwrites(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.collect_services import main

    output = tmp_path / "extracted.json"
    args = [
        "collect_services",
        "--source",
        SOURCE.id,
        "--input-html",
        str(Path(__file__).parent / "fixtures/service_source_synthetic.html"),
        "--synthetic",
        "--output",
        str(output),
    ]
    monkeypatch.setattr("sys.argv", args)
    assert main() == 0
    before = output.read_text(encoding="utf-8")
    result = json.loads(before)
    assert result["review_status"] == "pending" and result["documents"][0]["synthetic"]
    assert main() == 1 and output.read_text(encoding="utf-8") == before
