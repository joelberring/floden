"""Floden som MCP-server: styr Floden med AI, utan att AI:n äger ett enda tal.

Grundidén: Floden räknar, AI:n ställer frågor, väljer körningar och skriver. Alla tal i ett
verktygssvar kommer ur kärnans egna filer (`karna`, `webb.fragor`) och bär ett belägg
(körnings-id, fil, mottagare eller cell, mått, enhet). MCP-lagret räknar inget själv och har ingen
egen logik för vatten, dagsljus eller kartor: det anropar samma kärn-API som webben.

- `floden_mcp.karna_api`: fasaden mot kärnan (en sanning, samma anrop som webben).
- `floden_mcp.server`: MCP-verktyg, resurser och promptar (officiella Python MCP SDK).
- `floden_mcp.dataklass`: dataklasser (öppet, internt, skyddat, personuppgifter) och policyhook.
- `floden_mcp.korlogg`: lokal append-only körlogg (JSONL med hashkedja).
- `floden_mcp.karta`: serverrenderad karta med fast kartografi.
- `floden_mcp.http`: Streamable HTTP bakom samma inloggning som webben.

Start: `floden mcp` (stdio) eller `floden mcp --http --port 8766`. Se `docs/MCP.md`.
"""

from __future__ import annotations

__all__ = ["MCP_VERSION"]

MCP_VERSION = "0.1.0"
"""Version av Flodens MCP-gränssnitt (verktygens namn och svarsform)."""
