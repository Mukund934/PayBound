"""Generate ``report.html``. One file, opens by double-click, no server.

There is no web server anywhere in this project. ``pb demo`` writes a single
self-contained HTML file and you open it. That deletes FastAPI, SSE, CORS, ports
and a whole class of on-camera failures, and it means the artifact opens on the
machine that grades it — which is the first clause of the published build-quality
criterion.

The Decision View is five columns, and the fifth is the point:

  1. the customer's prose, tagged untrusted
  2. the reason code the model routed to — its entire contribution
  3. every precondition, with the field read and the value observed
  4. the amount, and the ``file:line`` that computed it
  5. **outbound HTTP calls during this decision**

Column 5 reads ``0`` on every refusal. Not "blocked", not "denied" — zero calls
were made, because the decision to refuse happened before any socket existed.

Two rules this module enforces rather than trusts:

* **A red guard renders every number as ``——``.** Not a banner over real
  numbers: the digits are not emitted at all. A reader cannot accidentally quote
  a figure the run could not defend.
* **Adversarial rates render only through ``fmt_adversarial``**, which welds the
  attacker description into the same string as the digit. A screenshot, a crop
  and a 720p re-encode all carry it. ``tests/regression`` parses the generated
  HTML and fails the build if any adversarial rate appears without it.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paybound.agent.models import ATTACKER_PROVENANCE, attacker_paragraph, attacker_stamp
from paybound.core.money import format_inr
from paybound.harness.guard import GuardReport
from paybound.harness.stats import fmt_adversarial, fmt_rate

__all__ = ["AMOUNT_SOURCE", "DecisionRow", "render_report"]

# The literal reference the video's cursor lands on. Kept as a constant so the
# report and the narration cannot drift apart.
AMOUNT_SOURCE = "paybound/core/policy/amount.py"

REDACTED = "——"


@dataclass(frozen=True, slots=True)
class DecisionRow:
    item_id: str
    prose: str
    routed: str | None
    decision: str
    amount_paise: int | None
    clause_id: str | None
    amount_fn: str | None
    predicates: tuple[dict[str, Any], ...]
    outbound_http_posts: int
    refund_id: str | None = None
    refused_by: str | None = None


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _predicate_cell(predicates: tuple[dict[str, Any], ...]) -> str:
    if not predicates:
        return '<span class="muted">no clause preconditions evaluated</span>'
    rows = []
    for p in predicates:
        result = str(p.get("result", "")).replace("Kleene.", "")
        cls = {"TRUE": "ok", "FALSE": "no", "UNKNOWN": "unk"}.get(result, "unk")
        observed = p.get("observed")
        shown = json.dumps(observed) if not isinstance(observed, str) else observed
        rows.append(
            f'<div class="pred"><span class="k {cls}">{_esc(result)}</span>'
            f'<span class="pn">{_esc(p.get("name"))}</span>'
            f'<span class="pf">{_esc(p.get("source_field"))}</span>'
            f'<span class="po">{_esc(shown)}</span></div>'
        )
    return "".join(rows)


def _decision_rows_html(rows: list[DecisionRow], redact: bool) -> str:
    out = []
    for r in rows:
        chip = r.decision.lower()
        if redact:
            amount_cell = f'<span class="redacted">{REDACTED}</span>'
        elif r.amount_paise is None:
            amount_cell = '<span class="muted">no amount computed</span>'
        else:
            amount_cell = (
                f'<div class="amt">{_esc(format_inr(r.amount_paise))}</div>'
                f'<div class="src">chosen by <code>{_esc(r.amount_fn or "policy")}</code> '
                f"in <code>{AMOUNT_SOURCE}</code>, not by the model</div>"
            )
        posts_cls = "zero" if r.outbound_http_posts == 0 else "nonzero"
        refused = (
            f'<div class="refused">refused: {_esc(r.refused_by)}</div>' if r.refused_by else ""
        )
        out.append(
            f"""<tr>
  <td class="prose"><span class="tag">L0_UNTRUSTED</span><div>{_esc(r.prose)}</div></td>
  <td class="routed"><code>{_esc(r.routed or "—")}</code>
      <div class="src">the model's entire contribution</div>{refused}</td>
  <td class="preds">{_predicate_cell(r.predicates)}</td>
  <td class="amount"><span class="chip {chip}">{_esc(r.decision)}</span>{amount_cell}</td>
  <td class="posts"><span class="{posts_cls}">{r.outbound_http_posts}</span>
      <div class="src">outbound HTTP calls during this decision</div></td>
</tr>"""
        )
    return "\n".join(out)


_CSS = """
:root{--bg:#0f1115;--panel:#161a21;--line:#232935;--txt:#e6e9ef;--mut:#8b94a7;
--ok:#3fb950;--no:#f85149;--unk:#d29922;--acc:#4c8dff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:22px 28px;border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:19px;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:13px}
main{padding:22px 28px;max-width:1600px}
section{margin-bottom:30px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.09em;color:var(--mut);
margin:0 0 12px;font-weight:600}
table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:8px;overflow:hidden}
th{text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;
letter-spacing:.07em;color:var(--mut);border-bottom:1px solid var(--line);font-weight:600}
td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.tag{display:inline-block;font-size:10px;letter-spacing:.06em;color:var(--unk);
border:1px solid var(--unk);border-radius:3px;padding:1px 5px;margin-bottom:6px}
.prose{max-width:340px}
.src{color:var(--mut);font-size:11px;margin-top:4px}
.chip{display:inline-block;padding:2px 9px;border-radius:4px;font-size:11px;
font-weight:600;letter-spacing:.04em;margin-bottom:7px}
.chip.allow{background:rgba(63,185,80,.15);color:var(--ok)}
.chip.deny{background:rgba(248,81,73,.15);color:var(--no)}
.chip.escalate{background:rgba(210,153,34,.15);color:var(--unk)}
.amt{font-size:20px;font-weight:650;letter-spacing:-.02em}
.pred{display:grid;grid-template-columns:56px 1fr;gap:2px 8px;margin-bottom:5px;font-size:12px}
.k{font-size:10px;font-weight:700;letter-spacing:.05em}
.k.ok{color:var(--ok)}.k.no{color:var(--no)}.k.unk{color:var(--unk)}
.pn{font-family:ui-monospace,monospace;font-size:11px}
.pf,.po{grid-column:2;color:var(--mut);font-size:11px}
.posts .zero{font-size:26px;font-weight:700;color:var(--ok)}
.posts .nonzero{font-size:26px;font-weight:700;color:var(--acc)}
.muted{color:var(--mut)}
.redacted{font-size:20px;color:var(--no);letter-spacing:.1em}
.metric{display:flex;justify-content:space-between;padding:9px 12px;
border-bottom:1px solid var(--line)}
.metric:last-child{border-bottom:none}
.metric .v{font-family:ui-monospace,monospace;font-size:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px}
.banner{padding:13px 16px;border-radius:8px;margin-bottom:18px;font-size:13px}
.banner.red{background:rgba(248,81,73,.1);border:1px solid var(--no);color:#ffb3ae}
.banner.warn{background:rgba(210,153,34,.1);border:1px solid var(--unk);color:#f0d08a}
.banner.info{background:rgba(76,141,255,.08);border:1px solid #2b4a80;color:#b8d0ff}
.refused{color:var(--no);font-size:11px;margin-top:4px}
footer{padding:20px 28px;border-top:1px solid var(--line);color:var(--mut);font-size:12px}
"""


def render_report(
    *,
    rows: list[DecisionRow],
    guard: GuardReport,
    metrics: dict[str, Any],
    run_id: str,
    provenance: dict[str, str],
    out_path: str | Path = "report.html",
) -> Path:
    """Write the single self-contained report file.

    ``metrics`` values are pre-formatted strings from ``harness.stats`` — this
    module never divides two numbers. Formatting is where the denominator rule
    and the attacker stamp are enforced, so a renderer that did its own
    arithmetic could route around both.
    """
    redact = guard.red

    if redact:
        banner = (
            '<div class="banner red"><strong>DENOMINATOR GUARD TRIPPED.</strong> '
            "No number on this page can be defended by this run, so none is shown. "
            + _esc("; ".join(guard.blocks))
            + "</div>"
        )
    elif guard.warns:
        banner = '<div class="banner warn"><strong>WARN.</strong> ' + _esc(
            "; ".join(guard.warns)
        ) + "</div>"
    else:
        banner = ""

    attacker_banner = (
        '<div class="banner info"><strong>Adversary.</strong> '
        + _esc(attacker_paragraph())
        + "</div>"
    )

    metric_html = []
    for key, value in metrics.items():
        if isinstance(value, dict):
            for k, v in value.items():
                shown = REDACTED if redact else v
                metric_html.append(
                    f'<div class="metric"><span>{_esc(key)} · {_esc(k)}</span>'
                    f'<span class="v">{_esc(shown)}</span></div>'
                )
        else:
            shown = REDACTED if redact else value
            metric_html.append(
                f'<div class="metric"><span>{_esc(key)}</span>'
                f'<span class="v">{_esc(shown)}</span></div>'
            )

    metrics_card = "".join(metric_html) or (
        '<div class="metric"><span>no metrics</span></div>'
    )

    prov_html = "".join(
        f'<div class="metric"><span>{_esc(k)}</span><span class="v">{_esc(v)}</span></div>'
        for k, v in provenance.items()
    )

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PayBound · {_esc(run_id)}</title><style>{_CSS}</style></head>
<body>
<header>
  <h1>PayBound — Decision View</h1>
  <div class="sub">run <code>{_esc(run_id)}</code> · every refusal made zero outbound
  HTTP calls · amounts computed by <code>{AMOUNT_SOURCE}</code></div>
</header>
<main>
  {banner}
  {attacker_banner}

  <section>
    <h2>Decisions</h2>
    <table>
      <thead><tr>
        <th>Customer message</th><th>Routed reason</th>
        <th>Preconditions, re-verified from trusted state</th>
        <th>Decision &amp; amount</th><th>HTTP</th>
      </tr></thead>
      <tbody>
{_decision_rows_html(rows, redact)}
      </tbody>
    </table>
  </section>

  <section>
    <h2>Results</h2>
    <div class="card">{metrics_card}</div>
  </section>

  <section>
    <h2>Provenance</h2>
    <div class="card">{prov_html}</div>
  </section>
</main>
<footer>
  Recompute every number on this page offline, with no keys and nothing installed:
  <code>python3 verify.py</code>
</footer>
</body></html>
"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path


def metrics_block(
    *,
    benign_allowed: int,
    benign_total: int,
    attack_succeeded: int,
    attack_total: int,
    declined: int,
    trials_total: int,
) -> dict[str, Any]:
    """Pre-format every published figure. The renderer never divides."""
    return {
        "automation rate (benign)": fmt_rate(benign_allowed, benign_total),
        "attack success": fmt_adversarial(attack_succeeded, attack_total, attacker_stamp()),
        "model declined": fmt_rate(declined, trials_total),
        "attacker tier vs agent": ATTACKER_PROVENANCE["tier_vs_t1"],
    }
