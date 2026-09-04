#!/usr/bin/env python3
"""Assemble the static root the deployment serves. Run by the Vercel build.

    python3 scripts/build_site.py

Two pages, and they arrive by different routes because the repository treats
them differently. That difference is the whole reason this script exists.

``showcase.html`` **is committed.** It is a generated artifact kept in the tree
on purpose -- GitHub does not run ``pb showcase``, and a reviewer should be able
to download one file and open it -- and the price of that convenience is
``test_the_committed_showcase_is_current``, which fails the build if the
committed bytes differ from a fresh render. So the build copies it, and copying
rather than regenerating is deliberate: the committed bytes are the ones under
test, and regenerating here would serve something no test has looked at.

``report.html`` **is not committed.** It is ignored at ``.gitignore:64``, so a
fresh checkout does not contain it and never has. It is produced on demand by
``pb report`` from the committed trials in ``evidence/``.

The first deployment configuration missed that distinction and copied both. It
worked locally, where ``report.html`` is left over from the last ``pb report``,
and failed on Vercel with ``cp: cannot stat 'report.html'`` -- a build that
passes on the machine that wrote it and fails on every clean checkout. The fix
is not to commit the file. It is to generate it here, from the same code path
and the same evidence the command line uses.

Dependencies: none beyond the standard library and this repository. The report
generator reaches ``paybound.harness.report`` and ``paybound.core.money``, both
of which are stdlib-only, so this runs in a build image that installed nothing
but ``httpx`` -- which is exactly what Vercel installs.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PUBLIC = REPO / "public"
COMMITTED = "showcase.html"
GENERATED = "report.html"


def _fail(message: str) -> None:
    """Exit non-zero and loudly. A half-built static root must not deploy."""
    sys.stderr.write(f"build_site: {message}\n")
    raise SystemExit(1)


def copy_committed() -> Path:
    """Place the committed showcase in the static root, unchanged."""
    src = REPO / COMMITTED
    if not src.is_file():
        _fail(
            f"{COMMITTED} is missing from the checkout. It is a committed "
            "artifact; regenerate it with `pb showcase` and commit the result."
        )
    dst = PUBLIC / COMMITTED
    shutil.copyfile(src, dst)
    return dst


def generate_report() -> Path:
    """Render report.html from the committed trials, into the static root.

    Invokes the CLI's own entry point rather than reaching into the report
    module. ``pb report`` is the canonical command and it is what the README
    documents; calling anything else here would be a second definition of what
    the report is.
    """
    from paybound.cli import main

    dst = PUBLIC / GENERATED
    rc = main(["report", "--out", str(dst)])
    if rc != 0:
        _fail(f"`pb report` exited {rc}; the static root would be incomplete")
    return dst


def main() -> int:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    built = [copy_committed(), generate_report()]

    # Verify rather than assume. The failure this script exists to prevent was
    # a build command that reported success for a file it had not produced.
    for path in built:
        if not path.is_file() or path.stat().st_size == 0:
            _fail(f"{path.name} was not written, or is empty")
        print(f"  {path.relative_to(REPO)}  {path.stat().st_size:,} bytes")

    index = PUBLIC / "index.html"
    if not index.is_file():
        _fail("public/index.html is missing; the static root has no entry page")

    print(f"static root ready: {PUBLIC.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
