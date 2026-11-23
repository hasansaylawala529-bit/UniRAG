# UniRAG as an MCP server

UniRAG runs as a [Model Context Protocol](https://modelcontextprotocol.io) server over stdio, so any MCP client
(Claude Desktop, the MCP Inspector, an IDE agent) can use the hybrid engine as a tool.

| Tool | Input | Output |
|---|---|---|
| `ask` | `question` | answer with `[SQL]`/`[Doc]` citations, route, who decided it, figures verified, refusal layer, models used, the read-only SQL that ran |
| `search_policies` | `query`, `k` (1–10) | cited policy passages with scores |
| `describe_data` | – | the 9 readable tables with their columns, and what this server's role may see of each |

All three are marked read-only. **No tool runs raw SQL.**

## Access policy through MCP
- The role is fixed when the server starts, from its configuration. It is **never a tool argument**: unknown arguments such as `role` or `student_id` are ignored. The default is **anonymous**.
- Every `ask` goes through the same pipeline as the web UI, with the same checks:
  - write-intent and contact-data rules;
  - privacy rules;
  - SQL guard;
  - row scoping;
  - the read-only database role.

| Configuration | Runs as |
|---|---|
| nothing set | anonymous: policies, public data, aggregates only |
| `UNIRAG_MCP_ROLE=staff` | staff: individual records of named students |
| `UNIRAG_MCP_STUDENT_ID=S2026CS012` | that student: their own records only |

## Before you start
1. The Docker database is running: `docker compose up -d`.
2. `.env` holds `GEMINI_API_KEY` (and optionally `GROQ_API_KEY`). The server reads it from the project folder, whatever folder the client starts it from.

## Quick check (no client needed)
```powershell
python scripts/mcp_smoke.py                                    # starts the server over stdio, lists tools, calls each one
python scripts/mcp_smoke.py --role staff "How many students are in Information Technology?"
python scripts/mcp_smoke.py --student S2026CS012 "What are my overdue fees?"
```

## Claude Desktop
1. Open **Settings → Developer → Edit Config**. This opens `%APPDATA%\Claude\claude_desktop_config.json` on Windows.
2. Add the server:
```json
{
  "mcpServers": {
    "unirag": {
      "command": "D:\\Projects\\UniRAG\\.venv\\Scripts\\python.exe",
      "args": ["-m", "unirag.mcp_server"],
      "env": { "UNIRAG_MCP_ROLE": "anonymous" }
    }
  }
}
```
3. Restart Claude Desktop. **unirag** then appears under the tools (🔨) menu.

Try these questions:
- *"Ask UniRAG which research grants a third-year CS student with CGPA 8.4 can apply for, with deadlines."*
- *"List all students' email addresses."* This is refused by the contact-data rule, with 0 model calls.

To demo another role, change `env`, for example `"UNIRAG_MCP_STUDENT_ID": "S2026CS012"`, and restart Claude Desktop.

## MCP Inspector (needs Node.js)
```powershell
npx @modelcontextprotocol/inspector D:\Projects\UniRAG\.venv\Scripts\python.exe -m unirag.mcp_server
```
The Inspector lists the tools with their input and output schemas, and lets you call each one.
