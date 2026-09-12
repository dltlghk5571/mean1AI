"""Bounded SDK client smoke check; starts and always closes its own MCP child process."""

import argparse
import ctypes
import json
import sys
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from app.mcp_schemas import CatalogToolResult
from app.services.mcp_tools import TOOL_NAMES


async def check_connection(database: Path) -> dict[str, object]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "app.mcp_server", "--database", str(database.resolve())],
        cwd=Path(__file__).resolve().parents[1],
    )
    with anyio.fail_after(30):
        async with Client(parameters, read_timeout_seconds=10) as client:
            listing = await client.list_tools()
            if {tool.name for tool in listing.tools} != TOOL_NAMES:
                raise ValueError("unexpected_tool_list")
            response = await client.call_tool("search_services", {"query": "가로등", "limit": 1})
            search = CatalogToolResult.model_validate(response.structured_content)
            if response.is_error:
                raise ValueError("search_failed")
            arguments: dict[str, object] = {}
            if search.services and search.catalog:
                arguments = {
                    "service_id": search.services[0].service_id,
                    "catalog": search.catalog.model_dump(mode="json"),
                }
            response = await client.call_tool("get_required_information", arguments)
            requirements = CatalogToolResult.model_validate(response.structured_content)
            if response.is_error:
                raise ValueError("requirements_failed")
            return {
                "connection": "ok",
                "tools": sorted(TOOL_NAMES),
                "catalog_status": search.status,
                "service_count": len(search.services),
                "question_count": len(requirements.required_information),
                "synthetic": any(card.synthetic for card in search.services),
            }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seongnam MCP connection check (30-second limit)")
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetConsoleTitleW("Seongnam - MCP connection check")
    try:
        result = anyio.run(check_connection, args.database)
    except Exception:
        sys.stderr.write(
            "MCP 연결 검사를 완료하지 못했습니다. DB 준비와 서버 실행 설정을 확인하세요.\n"
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
