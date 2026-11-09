# -*- coding: utf-8 -*-
"""Read-only Model Context Protocol endpoint for MusicLSP public catalogue data.

Implements MCP Streamable HTTP JSON responses without introducing a second
database or touching the bot, player, or Connect runtime.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from aiohttp import web

from core import providers as P
from core.charts import SPECS, build_chart, visible
from core.config import APP_VERSION

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("musiclsp.mcp")
PORT = int(os.environ.get("PORT", "8080"))
PROTOCOL_VERSION = "2025-03-26"
MAX_LIMIT = 20


def _limit(value, default=10):
    try:
        return max(1, min(MAX_LIMIT, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _public_item(item):
    """Return catalogue metadata only; never expose playable stream/download URLs."""
    if not isinstance(item, dict):
        return item
    hidden = {"stream", "download_url", "audio", "audiodownload", "token", "access_token"}
    return {k: v for k, v in item.items() if k not in hidden}


def _search_tracks(query, limit):
    return [_public_item(x) for x in (P.search_tracks_smart(query, limit=limit) or [])]


def _search_albums(query, limit):
    return [_public_item(x) for x in (P.search_albums_smart(query, limit=limit) or [])]


def _search_artists(query, limit):
    return [_public_item(x) for x in (P.search_artists_smart(query, limit=limit) or [])]


def _charts(period):
    charts = []
    for spec in SPECS:
        _chart_id, _title, _source, provider, parameter = spec
        if provider == "audius":
            tracks = P.audius_trending(genre=parameter, limit=10)
        elif provider == "jamendo":
            tracks = P.jamendo_tag_tracks(
                parameter, limit=10,
                order="popularity_week" if period == "week" else "popularity_month",
            )
        else:
            tracks = []
        charts.extend(visible([build_chart(spec, [_public_item(x) for x in tracks], period)]))
    return charts


TOOLS = [
    {"name": "musiclsp_status",
     "description": "Get MusicLSP version and availability of public music sources. Read-only.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "search_tracks",
     "description": "Search the MusicLSP public music catalogue for track metadata. Does not return audio stream URLs.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "minLength": 1, "maxLength": 160},
         "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}},
         "required": ["query"], "additionalProperties": False}},
    {"name": "search_albums",
     "description": "Search public album and release metadata in MusicLSP.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "minLength": 1, "maxLength": 160},
         "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}},
         "required": ["query"], "additionalProperties": False}},
    {"name": "search_artists",
     "description": "Search public artist profiles in MusicLSP.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "minLength": 1, "maxLength": 160},
         "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}},
         "required": ["query"], "additionalProperties": False}},
    {"name": "get_charts",
     "description": "Get MusicLSP source-labelled trending collections for a week or month. These are provider collections, not official global rankings.",
     "inputSchema": {"type": "object", "properties": {
         "period": {"type": "string", "enum": ["week", "month"], "default": "month"}},
         "additionalProperties": False}},
]


def _jsonrpc(result, request_id):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


async def health(_request):
    return web.json_response({"ok": True, "service": "musiclsp-mcp",
                              "version": APP_VERSION})


async def mcp_endpoint(request):
    if request.method != "POST":
        return web.Response(status=405, headers={"Allow": "POST, GET, OPTIONS"})
    try:
        payload = await request.json()
    except (json.JSONDecodeError, web.HTTPException):
        return web.json_response(_error(None, -32700, "Invalid JSON"), status=400)
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return web.json_response(_error(None, -32600, "Invalid JSON-RPC request"),
                                 status=400)

    method = payload.get("method")
    params = payload.get("params") or {}
    request_id = payload.get("id")
    if method == "notifications/initialized" or (
            isinstance(method, str) and method.startswith("notifications/")):
        return web.Response(status=202)
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "MusicLSP", "version": APP_VERSION},
            "instructions": (
                "Read-only access to MusicLSP public catalogue metadata and "
                "source-labelled charts. No playback, user data, or admin actions."
            ),
        }
        return web.json_response(_jsonrpc(result, request_id))
    if method == "ping":
        return web.json_response(_jsonrpc({}, request_id))
    if method == "tools/list":
        return web.json_response(_jsonrpc({"tools": TOOLS}, request_id))
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return web.json_response(_error(request_id, -32602,
                                            "arguments must be an object"))
        try:
            if name == "musiclsp_status":
                result = {"version": APP_VERSION,
                          "sources": await asyncio.to_thread(P.source_status)}
            elif name in ("search_tracks", "search_albums", "search_artists"):
                query = str(args.get("query") or "").strip()
                if not query or len(query) > 160:
                    raise ValueError("query must contain 1–160 characters")
                limit = _limit(args.get("limit"))
                fn = {"search_tracks": _search_tracks,
                      "search_albums": _search_albums,
                      "search_artists": _search_artists}[name]
                result = await asyncio.to_thread(fn, query, limit)
            elif name == "get_charts":
                period = args.get("period", "month")
                if period not in ("week", "month"):
                    raise ValueError("period must be week or month")
                result = await asyncio.to_thread(_charts, period)
            else:
                return web.json_response(_error(request_id, -32601, "Unknown tool"))
            body = {
                "content": [{"type": "text",
                             "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": {"result": result},
                "isError": False,
            }
            return web.json_response(_jsonrpc(body, request_id))
        except ValueError as exc:
            return web.json_response(_error(request_id, -32602, str(exc)))
        except Exception:  # noqa: BLE001
            log.exception("MCP tool failed: %s", name)
            body = {"content": [{"type": "text",
                                 "text": "MusicLSP tool temporarily unavailable"}],
                    "isError": True}
            return web.json_response(_jsonrpc(body, request_id))
    return web.json_response(_error(request_id, -32601, "Method not found"))


def create_app():
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/healthz", health)
    app.router.add_post("/mcp", mcp_endpoint)
    app.router.add_route("OPTIONS", "/mcp",
                         lambda _request: web.Response(status=204))
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT)
