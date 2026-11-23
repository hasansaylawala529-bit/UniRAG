"""Smoke test of the MCP server over stdio, exactly as Claude Desktop starts it.

    python scripts/mcp_smoke.py                                  # anonymous server
    python scripts/mcp_smoke.py --role staff "How many students are in Information Technology?"
    python scripts/mcp_smoke.py --student S2026CS012 "What are my overdue fees?"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="List all students' email addresses.")
    ap.add_argument("--role", default="")
    ap.add_argument("--student", default="")
    args = ap.parse_args()
    env = {**os.environ, "UNIRAG_MCP_ROLE": args.role, "UNIRAG_MCP_STUDENT_ID": args.student}
    params = StdioServerParameters(command=sys.executable, args=["-m", "unirag.mcp_server"], env=env, cwd=str(ROOT))
    async with Client(params) as c:
        print(f"Connected: {c.server_info.name if c.server_info else 'unirag'}")
        for t in (await c.list_tools()).tools:
            print(f"  tool {t.name:<16} args={list(t.input_schema.get('properties', {}))}  read-only="
                  f"{t.annotations.read_only_hint if t.annotations else '?'}")
        r = await c.call_tool("describe_data", {})
        print("\ndescribe_data: " + (r.content[0].text if r.is_error else
              f"role={r.structured_content['role']}, {len(r.structured_content['tables'])} tables"))
        r = await c.call_tool("search_policies", {"query": "minimum attendance", "k": 2})
        print("search_policies: " + (r.content[0].text if r.is_error else
              "; ".join(p["citation"] for p in r.structured_content["passages"])))
        print(f"\nask: {args.question}")
        r = await c.call_tool("ask", {"question": args.question})
        print(json.dumps(r.structured_content, indent=2, ensure_ascii=False) if not r.is_error else r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
