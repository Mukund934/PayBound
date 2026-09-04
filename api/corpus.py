"""The sealed corpus index, so a visitor can choose what to run.

Read-only and complete: all 150 items, attack and benign, with the prose
verbatim. The payloads are shown as sealed rather than paraphrased for display,
because a demonstration that cleans up its own attack strings is showing
something other than what it runs.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from ._engine import corpus_index, provenance, refund_tool_schema
from ._http import Denied, json_bytes, rate_limit


class handler(BaseHTTPRequestHandler):  # platform-mandated class name
    def do_GET(self) -> None:  # BaseHTTPRequestHandler contract
        try:
            rate_limit("public", self.headers.get("x-forwarded-for", "local"))
            body = {
                "items": corpus_index(),
                "refund_tool_schema": refund_tool_schema(),
                "provenance": provenance(),
            }
            status = 200
        except Denied as d:
            body, status = {"error": d.message}, d.status
        except Exception:
            body, status = {"error": "could not read the sealed corpus"}, 500
        payload = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(payload)
