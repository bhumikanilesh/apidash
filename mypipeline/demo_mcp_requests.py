"""
Small MCP demo script for submission screenshots and terminal demos.

It sends exact JSON-RPC requests to the local MCP server implementation and
prints the request/response pairs in a readable way.
"""

from __future__ import annotations

import json

from mcp_server import APIMarketplaceMCPServer


def emit(title: str, message: dict, response: dict) -> None:
    line = "=" * 72
    print(f"\n{line}\n{title}\n{line}")
    print("REQUEST")
    print(json.dumps(message, indent=2, ensure_ascii=False))
    print("\nRESPONSE")
    print(json.dumps(response, indent=2, ensure_ascii=False))


def main() -> None:
    server = APIMarketplaceMCPServer()

    requests = [
        (
            "Initialize MCP Server",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        ),
        (
            "List Tools",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ),
        (
            "Explore APIs",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "explore_apis",
                    "arguments": {
                        "query": "finance",
                        "category": "finance",
                        "limit": 3,
                    },
                },
            },
        ),
        (
            "Get Pipeline Status",
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "get_pipeline_status",
                    "arguments": {},
                },
            },
        ),
        (
            "Read Embedded Explorer UI",
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "resources/read",
                "params": {
                    "uri": "ui://api-explorer",
                },
            },
        ),
    ]

    for title, message in requests:
        response = server.handle(message)
        emit(title, message, response)


if __name__ == "__main__":
    main()
