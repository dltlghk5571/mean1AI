"""Structural extraction for the observed welfare and organization page layouts."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from app.collection_schemas import ExtractedPage, InputKind, ListingItem, WelfareFilter, WorkRow
from app.service_data_schemas import SourceDocument
from app.services.collection_sources import CollectionError, CollectionSource
from app.services.pii import redact_pii

MAX_BYTES = 1_000_000
VOID = {
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
}
BLOCKED = {"script", "style", "nav", "header", "footer", "noscript", "button", "select", "textarea"}
BLOCKS = {"p", "div", "tr", "td", "th", "li", "dt", "dd", "h1", "h2", "h3", "h4", "br"}
EXCLUDED_CLASSES = {"board-view-info", "content-file", "btn-wrap", "content-foot"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: Node | None = None
    children: list[Node | str] = field(default_factory=list)

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def walk(self) -> Iterator[Node]:
        for child in self.children:
            if isinstance(child, Node):
                if (
                    child.tag in BLOCKED
                    or "hidden" in child.attrs
                    or child.attrs.get("aria-hidden") == "true"
                ):
                    continue
                yield child
                yield from child.walk()

    def text(self, *, exclude: set[str] | None = None) -> str:
        if self.tag in BLOCKED or "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true":
            return ""
        if exclude and any(self.has_class(name) for name in exclude):
            return ""
        value = "".join(
            child.text(exclude=exclude) if isinstance(child, Node) else child
            for child in self.children
        )
        return f"\n{value}\n" if self.tag in BLOCKS else value


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack = [self.root]
        self.nodes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > 20_000 or len(self.stack) > 100:
            raise CollectionError("html_structure_limit")
        node = Node(tag, {key: value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def find(root: Node, *, tag: str = "", cls: str = "") -> list[Node]:
    return [
        node
        for node in root.walk()
        if (not tag or node.tag == tag) and (not cls or node.has_class(cls))
    ]


def one(nodes: list[Node]) -> Node:
    if len(nodes) != 1:
        raise CollectionError("page_structure_not_verified")
    return nodes[0]


def clean(value: str) -> str:
    return redact_pii(re.sub(r"\s+", " ", value).strip()).text


def lines(value: str) -> str:
    return "\n".join(line for line in (clean(part) for part in value.splitlines()) if line)


def document(
    page: ExtractedPage, title: str, body: str, fetched_at: datetime | None
) -> SourceDocument:
    if not 2 <= len(title) <= 200 or not 5 <= len(body) <= 40_000:
        raise CollectionError("main_content_not_verified")
    digest = hashlib.sha256(body.encode()).hexdigest()
    return SourceDocument(
        id=f"SRC-{hashlib.sha256(page.source_url.encode()).hexdigest()[:16]}-{digest[:12]}",
        source_id=page.source_id,
        source_url=None if page.synthetic else page.source_url,
        title=title,
        text=body,
        content_hash=digest,
        fetched_at=fetched_at,
        ingested_at=page.processed_at,
        license_label="unverified; human review required",
        synthetic=page.synthetic,
    )


def detail_link(source: CollectionSource, base: str, anchor: Node) -> str | None:
    href = anchor.attrs.get("href", "")
    onclick = anchor.attrs.get("onclick", "")
    match = re.fullmatch(r"\s*fn_move_form\(\s*([1-9][0-9]{0,9})\s*\)\s*;?\s*", onclick)
    scripted = source.canonical_url(urljoin(base, f"/wf-pm020101/{match[1]}")) if match else None
    direct = (
        source.canonical_url(urljoin(base, href))
        if href and not href.startswith("javascript:")
        else None
    )
    if onclick and not match or scripted and direct and scripted != direct:
        return None
    if href and href not in {"javascript:void(0)", "javascript:void(0);"} and direct is None:
        return None
    result = direct or scripted
    return result if result and urlsplit(result).path.count("/") == 2 else None


def welfare_list(source: CollectionSource, root: Node, page: ExtractedPage) -> None:
    total_node = one(find(one(find(root, cls="total")), tag="strong"))
    total = clean(total_node.text()).replace(",", "")
    if not re.fullmatch(r"[0-9]{1,6}", total):
        raise CollectionError("listing_total_not_verified")
    page.reported_total = int(total)
    gallery = one(find(root, cls="board-gallery-body"))
    cards = [
        node for node in find(gallery, tag="li") if node.parent and node.parent.parent is gallery
    ]
    if not cards and page.reported_total:
        raise CollectionError("listing_cards_missing")
    page.records_seen = len(cards)
    for card in cards:
        try:
            description = one(find(card, tag="dl"))
            anchor = one(find(one(find(description, tag="dt")), tag="a"))
            link = detail_link(source, page.source_url, anchor)
            if not link:
                raise CollectionError("listing_detail_link_unverified")
            title = clean(anchor.text())
            summary = clean(one(find(description, tag="dd")).text())
            if not title or not summary:
                raise CollectionError("listing_text_missing")
            departments = find(card, cls="menu-name")
            department = clean(one(departments).text()) if departments else None
            page.listing_items.append(
                ListingItem(
                    source_code=urlsplit(link).path.rsplit("/", 1)[1],
                    source_url=link,
                    title=title,
                    summary=summary,
                    displayed_department=department,
                    tags=[clean(node.text()) for node in find(card, cls="receipt")],
                )
            )
            page.discovered_links.append(link)
        except CollectionError as exc:
            page.review_issues.append(str(exc))
            page.extraction_complete = False
    page.records_extracted = len(page.listing_items)
    if page.records_seen > page.reported_total:
        page.review_issues.append("listing_total_smaller_than_page")
        page.extraction_complete = False
    for category in find(root, cls="info-category"):
        group_nodes = [child for child in category.children if isinstance(child, Node)]
        group = clean(group_nodes[0].text()) if group_nodes else ""
        for label in find(category, tag="label"):
            code = label.attrs.get("for", "")
            if re.fullmatch(r"srchCtgry_WLF_(?:CRR_CYCL|HSHD_STTN|ITRST_TPC)_[1-9][0-9]?", code):
                page.welfare_filters.append(
                    WelfareFilter(group=group, source_code=code, label=clean(label.text()))
                )
    for anchor in find(root, tag="a"):
        href = anchor.attrs.get("href", "")
        link = source.canonical_url(urljoin(page.source_url, href)) if href else None
        if link and urlsplit(link).path == "/wf-pm020101" and link != page.source_url:
            page.discovered_links.append(link)


def welfare_detail(root: Node, page: ExtractedPage, fetched_at: datetime | None) -> None:
    section = one(find(root, cls="content-section"))
    title = clean(one(find(section, tag="h3")).text())
    body = lines(section.text(exclude=EXCLUDED_CLASSES))
    page.source_code = urlsplit(page.source_url).path.rsplit("/", 1)[1]
    owners = find(section, cls="board-view-info")
    if owners:
        for paragraph in find(one(owners), tag="p"):
            label = clean(paragraph.text())
            if label.startswith("담당부서"):
                if page.footer_department is not None:
                    raise CollectionError("multiple_footer_departments")
                page.footer_department = label.removeprefix("담당부서").strip(" :：") or None
    if page.footer_department is None:
        page.review_issues.append("footer_department_missing")
    body_lines = body.splitlines()
    for index, line in enumerate(body_lines):
        match = re.match(r"^(?:문의처|문의전화|문의)(?:\s*[:：]\s*|\s+|$)(.*)$", line)
        if match:
            contact = match[1] or (body_lines[index + 1] if index + 1 < len(body_lines) else "")
            contact = contact.split("[전화번호]")[0].strip(" :：,·")
            if contact:
                page.body_contacts.append(contact)
    if not page.body_contacts:
        page.review_issues.append("body_contact_unresolved")
    elif page.footer_department and any(
        re.sub(r"\s+", "", contact) != re.sub(r"\s+", "", page.footer_department)
        for contact in page.body_contacts
    ):
        # Different labels are evidence to review, not proof that either is incorrect.
        page.review_issues.append("department_labels_differ")
    page.policy_year_mentions = sorted(
        {int(year) for year in re.findall(r"(?<!\d)((?:19|20)\d{2})\s*년", body)}
    )
    if page.policy_year_mentions:
        page.review_issues.append("policy_years_require_review")
    page.document = document(page, title, body, fetched_at)
    page.records_seen = page.records_extracted = 1


def organization(root: Node, page: ExtractedPage, fetched_at: datetime | None) -> None:
    page.source_code = parse_qs(urlsplit(page.source_url).query)["deptCode"][0]
    body = one(find(root, cls="content-body"))
    tables = find(body, tag="table", cls="content-table")
    if not tables:
        raise CollectionError("organization_tables_missing")
    text = []
    for table in tables:
        wrapper = table.parent
        if not wrapper or not wrapper.parent:
            raise CollectionError("organization_heading_missing")
        siblings = [n for n in wrapper.parent.children if isinstance(n, Node)]
        position = siblings.index(wrapper)
        heading = siblings[position - 1] if position else None
        if heading is None or heading.tag != "h4":
            raise CollectionError("organization_heading_missing")
        unit = clean(heading.text())
        heads = [clean(n.text()) for n in find(one(find(table, tag="thead")), tag="th")]
        if not unit or heads.count("담당업무") != 1:
            raise CollectionError("organization_columns_changed")
        column = heads.index("담당업무")
        text.append(unit)
        for row in find(one(find(table, tag="tbody")), tag="tr"):
            page.records_seen += 1
            cells = [n for n in row.children if isinstance(n, Node) and n.tag in {"td", "th"}]
            if len(cells) != len(heads) or any(
                n.attrs.get("rowspan", "1") != "1" or n.attrs.get("colspan", "1") != "1"
                for n in cells
            ):
                page.review_issues.append("organization_row_shape_changed")
                page.extraction_complete = False
                continue
            duty = lines(cells[column].text())
            if not duty:
                page.review_issues.append("organization_duty_missing")
                page.extraction_complete = False
                continue
            page.work_rows.append(WorkRow(unit_name=unit, duty=duty, row_number=page.records_seen))
            text.append(duty)
    page.records_extracted = len(page.work_rows)
    if not page.work_rows:
        raise CollectionError("organization_duties_missing")
    if any(
        count > 1 for count in Counter((row.unit_name, row.duty) for row in page.work_rows).values()
    ):
        page.review_issues.append("repeated_duty_text")
    page.review_issues.append("jurisdiction_mapping_requires_review")
    page.document = document(
        page, f"{page.work_rows[0].unit_name} 업무분장", "\n".join(text), fetched_at
    )


def extract_structured_page(
    source: CollectionSource,
    url: str,
    data: bytes,
    *,
    synthetic: bool = False,
    input_kind: InputKind = "saved_html",
    fetched_at: datetime | None = None,
) -> ExtractedPage:
    canonical = source.canonical_url(url)
    if not canonical or len(data) > MAX_BYTES:
        raise CollectionError("source_url_or_size_not_allowed")
    if input_kind != "http" and fetched_at is not None:
        raise CollectionError("local_input_has_no_fetch_time")
    try:
        html = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CollectionError("unsupported_source_encoding") from None
    parser = TreeParser()
    parser.feed(html)
    root = one([node for node in parser.root.walk() if node.attrs.get("id") == source.content_id])
    page = ExtractedPage(
        source_id=source.id,
        source_url=canonical,
        page_kind="organization"
        if source.profile == "organization"
        else ("welfare_detail" if urlsplit(canonical).path.count("/") == 2 else "welfare_list"),
        input_kind=input_kind,
        input_sha256=hashlib.sha256(data).hexdigest(),
        processed_at=datetime.now(UTC),
        synthetic=synthetic,
        review_issues=["usage_review_required"],
    )
    if page.page_kind == "welfare_list":
        welfare_list(source, root, page)
    elif page.page_kind == "welfare_detail":
        welfare_detail(root, page, fetched_at)
    else:
        organization(root, page, fetched_at)
    page.discovered_links = sorted(set(page.discovered_links))
    page.review_issues = sorted(set(page.review_issues))
    return page
