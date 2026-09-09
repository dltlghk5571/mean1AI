"""Run once and exit: python -m app.collect_services --help."""

import argparse
import json
from pathlib import Path

from app.services.collection_report import build_report, collect_local_manifest
from app.services.service_collection import (
    SOURCES,
    CollectionError,
    HttpPageFetcher,
    collect,
    extract_page,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect public service documents for review only")
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    local_inputs = parser.add_mutually_exclusive_group()
    local_inputs.add_argument(
        "--input-html", type=Path, help="Parse a lawfully obtained local UTF-8 page"
    )
    local_inputs.add_argument(
        "--input-manifest", type=Path, help="Reconcile up to 500 local pages from a JSON manifest"
    )
    parser.add_argument(
        "--input-kind",
        choices=["saved_html", "rendered_dom"],
        help="Local input provenance; defaults to saved_html for a single file",
    )
    parser.add_argument("--source-url", help="Registered source URL for the local page")
    parser.add_argument(
        "--synthetic", action="store_true", help="Mark a local test fixture as synthetic"
    )
    args = parser.parse_args()
    result: dict
    try:
        if args.output.exists():
            raise CollectionError("output_already_exists")
        source = SOURCES[args.source]
        if args.input_manifest:
            if args.synthetic or args.source_url or args.input_kind:
                raise CollectionError("manifest_defines_local_input_metadata")
            result = collect_local_manifest(source, args.input_manifest)
        elif args.input_html:
            with args.input_html.open("rb") as stream:
                page = extract_page(
                    source,
                    args.source_url or source.seed_url,
                    stream.read(1_000_001),
                    synthetic=args.synthetic,
                    input_kind=args.input_kind or "saved_html",
                )
            result = build_report(
                source,
                [page],
                [],
                mode="local_html",
                visited=1,
                remaining_links=len(set(page.discovered_links) - {page.source_url}),
            )
            result["discovered_links"] = page.discovered_links
        else:
            if args.synthetic or args.source_url or args.input_kind:
                raise CollectionError("local_input_required_for_selected_options")
            result = collect(source, HttpPageFetcher(), max_pages=args.max_pages)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(
            json.dumps(
                {
                    "status": "pending_review",
                    "output_written": True,
                    "completed": result["completed"],
                    "error_count": len(result["errors"]),
                }
            )
        )
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
