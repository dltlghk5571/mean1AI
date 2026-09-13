"""Explicit public-page scope. Query normalization never authorizes collection."""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.service_data_schemas import official_url

Profile = Literal["legacy", "welfare", "organization"]
ORGANIZATION_CODES = frozenset(
    {
        "37900000000",
        "38000000000",
        "38100720000",
        "38100730000",
        "38100700000",
        "38100610000",
        "38100540000",
    }
)
EMPTY_WELFARE_FILTERS = {
    "sortType",
    "arrWlfCrrCyclCd",
    "arrWlfHshdSttnCd",
    "arrWlfItrstTpcCd",
    "srchWlfCrrCyclCd",
    "srchWlfHshdSttnCd",
    "srchWlfItrstTpcCd",
    "srchWlfPvsnTypeCd",
    "srchType",
    "srchText",
}


class CollectionError(ValueError):
    pass


@dataclass(frozen=True)
class CollectionSource:
    id: str
    seed_url: str
    path_pattern: str
    content_id: str
    collection_reviewed: bool = False
    profile: Profile = "legacy"

    def canonical_url(self, url: str) -> str | None:
        try:
            official_url(url)
            parsed = urlsplit(url)
            pairs = parse_qsl(
                parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=20
            )
        except ValueError:
            return None
        if (
            parsed.netloc != urlsplit(self.seed_url).netloc
            or re.fullmatch(self.path_pattern, parsed.path) is None
            or len(pairs) != len(dict(pairs))
        ):
            return None
        query = dict(pairs)
        if self.profile == "organization":
            if (
                set(query) != {"deptCode", "orgSelect"}
                or query["deptCode"] not in ORGANIZATION_CODES
                or query["orgSelect"] != "orgSelect02"
            ):
                return None
            query = {"deptCode": query["deptCode"], "orgSelect": "orgSelect02"}
        elif self.profile == "welfare":
            detail_id = (
                parsed.path.removeprefix("/wf-pm020101/") if parsed.path.count("/") == 2 else None
            )
            allowed = EMPTY_WELFARE_FILTERS | {
                "curPage",
                "cntPerPage",
                "cvlcptBizSn",
                "board-input-text",
            }
            if not set(query) <= allowed or any(
                query.get(key, "") for key in EMPTY_WELFARE_FILTERS
            ):
                return None
            if any(
                key in query and not re.fullmatch(r"[1-9][0-9]{0,3}", query[key])
                for key in ("curPage", "board-input-text")
            ):
                return None
            if "cntPerPage" in query and query["cntPerPage"] not in {"9", "12", "15", "18"}:
                return None
            if "cvlcptBizSn" in query and query["cvlcptBizSn"] != detail_id:
                return None
            query = (
                {}
                if detail_id
                else {
                    key: value
                    for key, value in query.items()
                    if key in {"curPage", "cntPerPage"}
                    and (key, value) not in {("curPage", "1"), ("cntPerPage", "9")}
                }
            )
        elif query and (
            set(query) != {"curPage"} or re.fullmatch(r"[1-9][0-9]?", query["curPage"]) is None
        ):
            return None
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(sorted(query.items())), "")
        )

    def allows(self, url: str) -> bool:
        return self.canonical_url(url) is not None


# Browser DOM was inspected on 2026-09-09. HTTP/robots/usage review is still unresolved.
# These records do not authorize live collection or public retrieval/training.
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
    "seongnam-welfare": CollectionSource(
        "seongnam-welfare",
        "https://www.seongnam.go.kr/wf-pm020101",
        r"/wf-pm020101(?:/[1-9][0-9]{0,9})?",
        "contentLoad",
        profile="welfare",
    ),
    "seongnam-organization": CollectionSource(
        "seongnam-organization",
        "https://www.seongnam.go.kr/pm04041101?deptCode=38100720000&orgSelect=orgSelect02",
        r"/pm04041101",
        "contentLoad",
        profile="organization",
    ),
}
