"""Liveness and readiness. Reports what this deployment can and cannot do.

Readiness here means "the sealed artifacts loaded and the policy hash is the
one this build expects", not merely "the process started". A function that
answers 200 while its corpus failed to ship is worse than one that is down,
because the page it serves would be wrong rather than absent.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from ._engine import provenance
from ._http import execution_enabled, json_bytes


class handler(BaseHTTPRequestHandler):  # platform-mandated class name
    def do_GET(self) -> None:  # BaseHTTPRequestHandler contract
        try:
            prov = provenance()
            ready = prov["corpus_benign"] == 80 and prov["corpus_attack"] == 70
            body = {
                "status": "ok" if ready else "degraded",
                "ready": ready,
                "surface": "public, keyless, read-only",
                "holds_razorpay_credential": False,
                "holds_model_credential": False,
                "consumes_model_quota": False,
                "execution_endpoint_enabled": execution_enabled(),
                "provenance": prov,
            }
            status = 200 if ready else 503
        except Exception:
            body, status = {"status": "error", "ready": False}, 503
        payload = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)
