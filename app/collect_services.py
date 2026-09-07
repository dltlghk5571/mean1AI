"""Run once and exit: python -m app.collect_services --help."""

import argparse
import json
from pathlib import Path

from app.services.service_collection import (
    SOURCES,
    CollectionError,
    HttpPageFetcher,
    collect,
    extract_document,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect public service documents for review only")
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument(
        "--input-html", type=Path, help="Parse a lawfully obtained local UTF-8 page"
    )
    parser.add_argument("--source-url", help="Registered source URL for the local page")
    parser.add_argument(
        "--synthetic", action="store_true", help="Mark a local test fixture as synthetic"
    )
    args = parser.parse_args()
    result: dict[str, object]
    try:
        if args.output.exists():
            raise CollectionError("output_already_exists")
        source = SOURCES[args.source]
        if args.input_html:
            with args.input_html.open("rb") as stream:
                document, links = extract_document(
                    source,
                    args.source_url or source.seed_url,
                    stream.read(1_000_001),
                    synthetic=args.synthetic,
                )
            result = {
                "schema_version": "1",
                "review_status": "pending",
                "mode": "local_html",
                "documents": [document.model_dump(mode="json")],
                "discovered_links": links,
            }
        else:
            if args.synthetic or args.source_url:
                raise CollectionError("local_input_required_for_selected_options")
            result = collect(source, HttpPageFetcher(), max_pages=args.max_pages)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"status": "pending_review", "output_written": True}))
        return 0
    except (CollectionError, OSError, ValueError):
        print(
            json.dumps(
                {"status": "failed", "reason": "check_source_review_limits_input_and_output"}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
