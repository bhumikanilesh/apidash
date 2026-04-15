# API Explorer Pipeline + MCP Integration

This repository already contains the full 7-stage API Explorer pipeline:

1. `fetcher.py`
2. `parser.py`
3. `enricher.py`
4. `publisher.py`
5. `validator.py`
6. `deployer.py`
7. `search_indexer.py`

The MCP integration sits on top of those outputs rather than replacing them.

## Integration Model

The pipeline remains the source of truth:

`fetch -> parse -> enrich -> publish -> validate -> deploy -> search`

The new MCP layer reads the generated artifacts after publish/search:

- `marketplace/index.json`
- `marketplace/apis/*.json`
- `logs/validation_report.json`
- `logs/metrics.json`

That gives AI agents structured access to the same published marketplace used by
the Explorer UI.

## New MCP Capabilities

Run the MCP server:

```bash
python mcp_server.py
```

Run the demo request script:

```bash
python demo_mcp_requests.py
```

That script prints exact JSON-RPC request and response pairs you can reuse in
your PoC notes, screenshots, or submission write-up.

Available tools:

- `explore_apis`
- `get_api_details`
- `generate_apidash_collection`
- `suggest_sequence`
- `get_pipeline_status`
- `read_validation_report`
- `refresh_demo_marketplace`

Available resource:

- `ui://api-explorer`

## UI Integration

`index.html` now works in two modes:

1. Standalone mode
   - reads `marketplace/index.json`
   - fetches `marketplace/apis/{id}.json`

2. Embedded MCP mode
   - receives bootstrapped marketplace data from `mcp_server.py`
   - can call MCP tools using `postMessage` + JSON-RPC style messages
   - renders tool results inline in the Explorer UI

## VS Code MCP Config

Open the repo in VS Code and use `.vscode/mcp.json` to launch the server.
