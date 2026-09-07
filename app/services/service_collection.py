"""Bounded collection into a review queue. Never publish or crawl from a model URL."""

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from pydantic import ValidationError

from app.service_data_schemas import SourceDocument, official_url
from app.services.pii import redact_pii

USER_AGENT = "SeongnamMinwonResearch/0.1"
MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class CollectionSource:
    id: str
    seed_url: str
    path_pattern: str
    content_id: str
    collection_reviewed: bool = False

    def allows(self, url: str) -> bool:
        try:
            official_url(url)
        except ValueError:
            return False
        parsed = urlsplit(url)
        return (
            parsed.netloc == urlsplit(self.seed_url).netloc
            and re.fullmatch(self.path_pattern, parsed.path) is not None
            and (not parsed.query or re.fullmatch(r"curPage=[1-9][0-9]?", parsed.query) is not None)
        )


# Selectors and collection terms need verification against an accessible live response.
# These records authorize no network collection until that review is recorded in code review.
SOURCES = {
    "seongnam-handbook": CollectionSource(
        "seongnam-handbook",
        "https://www.seongnam.go.kr/bbs020405",
        r"/bbs020405(?:/[0-9]+)?",
        "contents",
    ),
    "seongnam-services": CollectionSource(
        "seongnam-services",
        "https://www.seongnam.go.kr/pm02020101?curPage=1",
        r"/pm02020101(?:/[0-9]+)?",
        "contents",
    ),
}


class CollectionError(ValueError):
    pass


class PageFetcher(Protocol):
    def get(self, url: str, *, max_bytes: int, timeout: float) -> tuple[bytes, str]: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpPageFetcher:
    def get(self, url: str, *, max_bytes: int, timeout: float) -> tuple[bytes, str]:
        # Keep TLS verification and environment proxy settings; do not follow redirects.
        official_url(url)
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                deadline = time.monotonic() + timeout
                data = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise CollectionError("response_time_limit")
                    chunk = response.read1(min(65536, max_bytes + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise CollectionError("response_too_large")
                return bytes(data), response.headers.get_content_type()
        except (URLError, TimeoutError, OSError):
            raise CollectionError("source_connection_failed") from None


class MainContentParser(HTMLParser):
    def __init__(self, content_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.content_id = content_id
        self.stack: list[tuple[str, bool, bool]] = []
        self.parts: list[str] = []
        self.links: list[str] = []
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        inside = bool(self.stack and self.stack[-1][1]) or attributes.get("id") == self.content_id
        blocked = bool(self.stack and self.stack[-1][2]) or tag in {
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "form",
            "noscript",
        }
        if inside and not blocked:
            self.found = True
            if tag == "a" and attributes.get("href"):
                self.links.append(str(attributes["href"]))
            if tag in {"p", "div", "tr", "td", "th", "li", "dt", "dd", "h1", "h2", "h3", "br"}:
                self.parts.append("\n")
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append((tag, inside, blocked))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                self.parts.append("\n")
                break

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1][1] and not self.stack[-1][2]:
            self.parts.append(data)


def extract_document(
    source: CollectionSource,
    url: str,
    data: bytes,
    *,
    synthetic: bool = False,
    fetched_at: datetime | None = None,
) -> tuple[SourceDocument, list[str]]:
    if not source.allows(url) or len(data) > MAX_BYTES:
        raise CollectionError("source_url_or_size_not_allowed")
    try:
        html = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CollectionError("unsupported_source_encoding") from None
    parser = MainContentParser(source.content_id)
    parser.feed(html)
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    body = redact_pii("\n".join(line for line in lines if line)).text
    if not parser.found or not 5 <= len(body) <= 40_000:
        raise CollectionError("main_content_not_verified")
    digest = hashlib.sha256(body.encode()).hexdigest()
    try:
        document = SourceDocument(
            id=f"SRC-{hashlib.sha256(url.encode()).hexdigest()[:16]}-{digest[:12]}",
            source_id=source.id,
            source_url=None if synthetic else url,
            title=body.splitlines()[0][:200],
            text=body,
            content_hash=digest,
            fetched_at=fetched_at,
            ingested_at=datetime.now(UTC),
            license_label="unverified; human review required",
            synthetic=synthetic,
        )
    except ValidationError:
        raise CollectionError("document_metadata_not_verified") from None
    links = sorted(
        {urljoin(url, link) for link in parser.links if source.allows(urljoin(url, link))}
    )
    return document, links


def collect(
    source: CollectionSource,
    fetcher: PageFetcher,
    *,
    max_pages: int = 3,
    time_limit: float = 45,
    delay: float = 2,
) -> dict[str, object]:
    if not source.collection_reviewed:
        raise CollectionError("collection_terms_and_selector_review_required")
    if not 1 <= max_pages <= 10 or not 1 <= time_limit <= 60 or not 1 <= delay <= 10:
        raise CollectionError("collection_limits_invalid")
    start = time.monotonic()
    origin = urlsplit(source.seed_url)
    robot_url = f"{origin.scheme}://{origin.netloc}/robots.txt"
    robots_bytes, robots_type = fetcher.get(
        robot_url, max_bytes=64_000, timeout=min(10, time_limit)
    )
    if len(robots_bytes) > 64_000 or robots_type not in {"text/plain", "text/x-robots"}:
        raise CollectionError("robots_not_verified")
    try:
        robot_text = robots_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CollectionError("robots_not_verified") from None
    if not re.search(r"(?im)^\s*user-agent\s*:", robot_text):
        raise CollectionError("robots_not_verified")
    robots = RobotFileParser(robot_url)
    robots.parse(robot_text.splitlines())
    delay = max(delay, float(robots.crawl_delay(USER_AGENT) or 0))
    rate = robots.request_rate(USER_AGENT)
    if rate and rate.requests > 0:
        delay = max(delay, rate.seconds / rate.requests)
    pending = [source.seed_url]
    visited: set[str] = set()
    documents: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    while pending and len(visited) < max_pages:
        remaining = time_limit - (time.monotonic() - start)
        if remaining <= delay:
            break
        url = pending.pop(0)
        if url in visited:
            continue
        if not source.allows(url) or not robots.can_fetch(USER_AGENT, url):
            errors.append({"code": "robots_or_scope_denied", "url": url})
            visited.add(url)
            continue
        time.sleep(delay)
        visited.add(url)
        try:
            data, content_type = fetcher.get(
                url, max_bytes=MAX_BYTES, timeout=min(10, remaining - delay)
            )
            if content_type != "text/html":
                raise CollectionError("unsupported_content_type")
            document, links = extract_document(source, url, data, fetched_at=datetime.now(UTC))
            documents.append(document.model_dump(mode="json"))
            pending.extend(link for link in links if link not in visited and link not in pending)
        except CollectionError as exc:
            errors.append({"code": str(exc), "url": url})
    return {
        "schema_version": "1",
        "source_id": source.id,
        "review_status": "pending",
        "documents": documents,
        "errors": errors,
        "visited": len(visited),
        "remaining_links": len(pending),
        "completed": not pending and not errors,
        "note": "Document extraction only; service/department mappings require review.",
    }
