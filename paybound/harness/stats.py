"""Counts, Wilson intervals, rule of three. No p-values, no bootstrap.

Three formatting rules that are enforced by the type system rather than by
discipline, because a percentage that loses its denominator is how a small-n
result gets quoted as a large-n one:

* ``fmt_rate`` never returns a bare percentage. Every rate renders as
  ``47.5% (19/40)``. There is no code path in this project that produces a
  percentage without its denominator.
* ``fmt_adversarial`` additionally welds the attacker description into the
  *same string as the digit*, so a screenshot, a crop, or a video re-encode
  carries the qualification. A constant in a source file carries it to nobody.
* A zero numerator renders with its **rule-of-three upper bound**. "0%" alone
  overstates certainty: zero successes in 45 trials is consistent with a true
  rate up to about 6.5%, and saying so is the difference between a result and a
  claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["Interval", "fmt_adversarial", "fmt_rate", "rule_of_three_upper", "wilson"]

# 95%.
_Z = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Interval:
    lower: float
    upper: float

    def __str__(self) -> str:
        return f"[{self.lower:.1%}, {self.upper:.1%}]"


def wilson(successes: int, trials: int, z: float = _Z) -> Interval:
    """Wilson score interval.

    Chosen over the normal approximation because the interesting cells in this
    project are small-n and often zero-success, and the normal approximation is
    exactly wrong there — it produces a zero-width interval at p=0, which would
    let a null result look certain.
    """
    if trials <= 0:
        raise ValueError("an interval over zero trials is not defined")
    if not 0 <= successes <= trials:
        raise ValueError(f"{successes} successes in {trials} trials is not possible")
    p = successes / trials
    denom = 1 + z**2 / trials
    centre = (p + z**2 / (2 * trials)) / denom
    half = (z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2))) / denom
    return Interval(max(0.0, centre - half), min(1.0, centre + half))


def rule_of_three_upper(trials: int, confidence: float = 0.95) -> float:
    """Upper bound on a rate after observing zero successes.

    3/n at 95%. This is the number that goes next to every zero in the report,
    because "0 attacks succeeded" and "0 attacks succeeded, and the data are
    consistent with up to 6.5%" are different statements and only the second is
    honest at n=45.
    """
    if trials <= 0:
        raise ValueError("rule of three needs at least one trial")
    k = -math.log(1 - confidence)
    return min(1.0, k / trials)


def fmt_rate(successes: int, trials: int) -> str:
    """``47.5% (19/40)``. Never a bare percentage.

    A denominator-free percentage is the single easiest way for a small result
    to be quoted as a large one, so this function has no mode that omits it.

    Uncertainty is printed on BOTH branches, and it had better be. Until 3 Sep
    a zero printed its rule-of-three upper bound while a non-zero printed bare,
    so the control arm's damaging ``50.0% (1/2)`` appeared naked next to our own
    bounded ``0.0% (0/2)``. The asymmetry ran in our favour, which is the only
    direction that matters.
    """
    if trials <= 0:
        return "—— (0/0)"
    if successes == 0:
        ub = rule_of_three_upper(trials)
        return f"0.0% (0/{trials}) · ub {ub:.1%}"
    # Interval.__str__ already renders "[lo, hi]"; verify.py builds the same
    # string from raw floats because it may not import this module.
    return f"{successes / trials:.1%} ({successes}/{trials}) · {wilson(successes, trials)}"

def fmt_adversarial(successes: int, trials: int, attacker_stamp: str) -> str:
    """An adversarial rate with the attacker welded into the same string.

    ``0.0% (0/45) · attacker T1-parity, deterministic sweep · ub 6.5%``

    The qualification is inside the digit's own string on purpose. A reader who
    sees the number cannot fail to see what produced it, and no crop, re-encode
    or copy-paste can separate them. ``tests/regression`` fails the build if a
    rate renders in an adversarial section without this token.
    """
    if not attacker_stamp:
        raise ValueError(
            "an adversarial rate may not render without an attacker description; "
            "that is the whole point of this formatter"
        )
    if trials <= 0:
        return f"—— (0/0) · {attacker_stamp}"
    if successes == 0:
        ub = rule_of_three_upper(trials)
        return f"0.0% (0/{trials}) · {attacker_stamp} · ub {ub:.1%}"
    ci = wilson(successes, trials)
    return f"{successes / trials:.1%} ({successes}/{trials}) · {attacker_stamp} · {ci}"
