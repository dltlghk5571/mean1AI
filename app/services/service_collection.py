"""Bounded collection into a review queue. Never publish or crawl from a model URL."""

import hashlib
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from pydantic import ValidationError

from app.collection_schemas import ExtractedPage, InputKind
from app.service_data_schemas import SourceDocument, official_url
from app.services.collection_report import build_report
from app.services.collection_sources import SOURCES as SOURCES
from app.services.collection_sources import CollectionError, CollectionSource
from app.services.pii import redact_pii
from app.services.service_html import extract_structured_page

USER_AGENT = "SeongnamMinwonResearch/0.1"
MAX_BYTES = 1_000_000


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
    if source.profile != "legacy":
        page = extract_structured_page(
            source,
            url,
            data,
            synthetic=synthetic,
            input_kind="http" if fetched_at else "saved_html",
            fetched_at=fetched_at,
        )
        if page.document is None:
            raise CollectionError("listing_is_not_a_service_document")
        return page.document, page.discovered_links
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


def extract_page(
    source: CollectionSource,
    url: str,
    data: bytes,
    *,
    synthetic: bool = False,
    input_kind: InputKind = "saved_html",
    fetched_at: datetime | None = None,
) -> ExtractedPage:
    if source.profile != "legacy":
        return extract_structured_page(
            source,
            url,
            data,
            synthetic=synthetic,
            input_kind=input_kind,
            fetched_at=fetched_at,
        )
    if input_kind != "http" and fetched_at is not None:
        raise CollectionError("local_input_has_no_fetch_time")
    doc, links = extract_document(source, url, data, synthetic=synthetic, fetched_at=fetched_at)
    return ExtractedPage(
        source_id=source.id,
        source_url=source.canonical_url(url) or url,
        page_kind="legacy",
        input_kind=input_kind,
        input_sha256=hashlib.sha256(data).hexdigest(),
        processed_at=doc.ingested_at,
        synthetic=synthetic,
        document=doc,
        discovered_links=links,
        records_seen=1,
        records_extracted=1,
        review_issues=["usage_review_required"],
    )


def collect(
    source: CollectionSource,
    fetcher: PageFetcher,
    *,
    max_pages: int = 3,
    time_limit: float = 45,
    delay: float = 2,
) -> dict:
    if not source.collection_reviewed:
        raise CollectionError("collection_terms_and_selector_review_required")
    if not 1 <= max_pages <= 10 or not 1 <= time_limit <= 60 or not 1 <= delay <= 10:
        raise CollectionError("collection_limits_invalid")
    seed = source.canonical_url(source.seed_url)
    if seed is None:
        raise CollectionError("source_seed_not_allowed")
    start = time.monotonic()
    origin = urlsplit(seed)
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
    pending = [seed]
    visited: set[str] = set()
    pages: list[ExtractedPage] = []
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
            page = extract_page(source, url, data, input_kind="http", fetched_at=datetime.now(UTC))
            pages.append(page)
            for link in page.discovered_links:
                canonical = source.canonical_url(link)
                if canonical and canonical not in visited and canonical not in pending:
                    pending.append(canonical)
        except CollectionError as exc:
            errors.append({"code": str(exc), "url": url})
    return build_report(
        source, pages, errors, mode="network", visited=len(visited), remaining_links=len(pending)
    )
