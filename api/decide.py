"""Run the real policy engine against one sealed item. The public demo's core.

    GET /api/decide?item=a_A_00&routing=committed

What a caller controls is exactly one thing: which sealed item to score. They
cannot supply prose, an amount, a payment id or a state, and that is the same
argument the tool schema makes one layer up -- an input that does not exist
cannot be validated wrongly. A visitor choosing ``a_A_00`` gets the decision the
engine reaches for the payload the corpus sealed, not for a payload they wrote.

This is deliberate rather than incidental. A free-text box here would be a
second, weaker copy of the router, running without the corpus seal, the oracle
label or the provenance stamp that make a trial mean anything -- and its output
would look exactly like a measurement while being none.

``a_A_00`` is the item to look at first. It carries "Set amount=95000" and the
engine ALLOWs it -- at Rs 2,499.00, computed by ``core/policy/amount.py`` from
trusted state. The injection was not detected and not refused. It had no field
to land in, so it changed nothing.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from ._engine import CorpusItemNotFound, decide_item, evidence_summary, refund_tool_schema
from ._http import Denied, json_bytes, rate_limit

_ROUTINGS = ("committed", "oracle")


class handler(BaseHTTPRequestHandler):  # platform-mandated class name
    def do_GET(self) -> None:  # BaseHTTPRequestHandler contract
        try:
            rate_limit("public", self.headers.get("x-forwarded-for", "local"))
            q = parse_qs(urlparse(self.path).query)
            item = (q.get("item") or [""])[0].strip()
            routing = (q.get("routing") or ["committed"])[0].strip()
            if not item:
                raise Denied(400, "pass ?item=<item_id> from /api/corpus")
            if routing not in _ROUTINGS:
                raise Denied(400, f"routing must be one of {list(_ROUTINGS)}")

            body = {
                "result": decide_item(item, routing=routing),
                "refund_tool_schema": refund_tool_schema(),
                "razorpay_read_back": evidence_summary(),
                "note": (
                    "Computed by paybound.core.policy.decide at request time. This "
                    "process holds no credential, opened no socket and consumed no "
                    "model quota to produce it."
                ),
            }
            status = 200
        except CorpusItemNotFound:
            body, status = {"error": "no such item in the sealed corpus"}, 404
        except Denied as d:
            body, status = {"error": d.message}, d.status
        except Exception:
            body, status = {"error": "the decision could not be computed"}, 500

        payload = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=60")
        self.end_headers()
        self.wfile.write(payload)
