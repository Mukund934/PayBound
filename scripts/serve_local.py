#!/usr/bin/env python3
"""Serve the deployed surface locally, with the platform's routing.

    python scripts/serve_local.py           # http://127.0.0.1:8000
    python scripts/serve_local.py --port 9000

The point of this script is that it is **not** a second implementation. It
imports the same ``handler`` classes the platform invokes and dispatches to
their ``do_GET``/``do_POST`` directly, so a behaviour that works here works
deployed and a bug reproduced here is the deployed bug. What it emulates is
only the routing table -- ``api/x.py`` serves ``/api/x``, and everything else
is a static file -- which is the one part the platform supplies.

Static roots, in order: ``public/`` first, then the repository root, so the
committed ``showcase.html`` and ``report.html`` are reachable at ``/showcase``
and ``/report`` exactly as the rewrites in ``vercel.json`` make them.

It holds no credential of its own. ``/api/execute`` is refused here for the
same reason it is refused in a fresh deployment: the switches are unset.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PUBLIC = REPO / "public"
REWRITES = {"/showcase": "/showcase.html", "/report": "/report.html"}
_ENDPOINTS = ("health", "corpus", "decide", "execute")


def _load() -> dict[str, type[BaseHTTPRequestHandler]]:
    """Import every api/<name>.py and keep its handler class."""
    out: dict[str, type[BaseHTTPRequestHandler]] = {}
    for name in _ENDPOINTS:
        mod = importlib.import_module(f"api.{name}")
        out[f"/api/{name}"] = mod.handler
    return out


HANDLERS = _load()


class Router(SimpleHTTPRequestHandler):
    """Static files, plus the api/ functions on their own paths."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, directory=str(PUBLIC), **kw)

    # -- dispatch ----------------------------------------------------------

    def _api(self, verb: str) -> bool:
        route = self.path.split("?")[0].rstrip("/") or "/"
        cls = HANDLERS.get(route)
        if cls is None:
            return False
        # Rebind this live connection to the endpoint's class and call it. The
        # function sees the real request it would see on the platform.
        self.__class__ = cls  # type: ignore[assignment]
        getattr(self, verb)()
        self.__class__ = Router  # type: ignore[assignment]
        return True

    def do_GET(self) -> None:  # BaseHTTPRequestHandler contract
        self.path = REWRITES.get(self.path, self.path)
        if self._api("do_GET"):
            return
        if self.path == "/":
            self.path = "/index.html"
        if not (PUBLIC / self.path.lstrip("/").split("?")[0]).is_file():
            # Fall back to the repository root so the committed pages resolve.
            self.directory = str(REPO)
        else:
            self.directory = str(PUBLIC)
        super().do_GET()

    def do_POST(self) -> None:  # BaseHTTPRequestHandler contract
        if self._api("do_POST"):
            return
        self.send_error(405, "only the api/ routes accept POST")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="serve the PayBound deployment locally")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    # Threading, not HTTPServer: the page fetches /api/corpus and /api/health
    # concurrently, and a single-threaded server serialises them into a
    # deadlock that looks exactly like a hung endpoint. The platform runs
    # each function independently, so serving them serially here would also
    # be emulating the wrong thing.
    srv = ThreadingHTTPServer((args.host, args.port), Router)
    base = f"http://{args.host}:{args.port}"
    print(f"PayBound local deployment  {base}")
    print(f"  {base}/            interactive demo, real policy engine, no keys")
    print(f"  {base}/showcase    the committed causal-chain page")
    print(f"  {base}/report      Decision View over committed trials")
    print(f"  {base}/api/health  readiness and provenance")
    print("\nCtrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
