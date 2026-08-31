"""Prose generation. A committed slot grammar, expanded deterministically.

Every customer message in the corpus is produced here, from templates a human
wrote and slots a human filled. No model writes corpus prose, for two reasons
that are not about cost:

1. **Reproducibility.** A reviewer regenerates all 150 messages byte-for-byte on
   a clean clone with no API key. That is the property ``verify.py`` exists to
   demonstrate, and a model-authored corpus could never have it.
2. **Oracle honesty.** The oracle label must be unambiguous, and the surest way
   to guarantee that is to make the claim itself a fixed, human-authored span
   that generation may vary *around* but never *through*. A model asked to
   paraphrase "the seam tore" will eventually write "it never arrived in one
   piece", which changes the honest answer.

Register
--------
Indian D2C support tickets: English with Hindi code-mixing, WhatsApp-adjacent
punctuation, occasional order numbers, sometimes shouty. Realism matters because
a corpus of clean grammatical sentences would measure a router on text no
merchant ever receives.

The ``ANCHOR`` of an item is the clause-bearing claim. The grammar may prepend
openers, append pressure, and code-mix the surrounding text; it may not touch
the anchor. ``corpus_admit`` re-checks that the anchor survives verbatim.
"""

from __future__ import annotations

import hashlib
from typing import Final

from paybound.core.types import ReasonCode

__all__ = ["ANCHORS", "anchor_for", "render"]

# ---------------------------------------------------------------------------
# The anchors. One per reason, several phrasings each, all human-authored.
# The anchor IS the claim; everything else in a message is noise around it.
# ---------------------------------------------------------------------------

ANCHORS: Final[dict[ReasonCode, tuple[str, ...]]] = {
    ReasonCode.DUPLICATE_CHARGE: (
        "my card got charged two times for the same order",
        "the same amount was debited twice for one order",
        "I see two payments for order {order} and I only placed it once",
        "double payment ho gaya, same amount dono baar",
        "paisa do baar cut gaya for a single order",
    ),
    ReasonCode.PRICE_MISMATCH: (
        "I was billed more than the price shown on the product page",
        "the site said one price and you charged me a higher amount",
        "listing price kuch aur tha, charge kuch aur hua",
        "you have overcharged me compared to the listed price",
        "the amount debited is more than the MRP shown when I ordered",
    ),
    ReasonCode.NOT_DELIVERED: (
        "the parcel never reached me and the courier has sent it back",
        "tracking says the shipment was returned to origin, I never got it",
        "courier returned my order, it never came to me",
        "delivery hui hi nahi, courier ne wapas bhej diya",
        "the package was never handed to me and has gone back to the warehouse",
    ),
    ReasonCode.CANCELLED_IN_WINDOW: (
        "I want to cancel this order, it has not been shipped yet",
        "please cancel, the order is still not dispatched",
        "cancel kar do, abhi tak pickup nahi hua hai",
        "I placed this by mistake an hour ago and it has not shipped",
        "kindly cancel my order, it is still lying at your warehouse",
    ),
    ReasonCode.ARRIVED_DAMAGED: (
        "the product came with a big tear down the seam",
        "item arrived cracked, the box was fine but the product inside is broken",
        "jo mila hai wo toota hua hai",
        "the bottle inside had leaked all over the packaging when I opened it",
        "product damaged nikla when I opened the parcel",
    ),
    ReasonCode.WRONG_ITEM: (
        "you sent me a completely different product than what I ordered",
        "I ordered one thing and a totally different item came",
        "galat item bhej diya aap logon ne",
        "the parcel had someone else's product in it, not mine",
        "wrong article received, it is not what I selected",
    ),
    ReasonCode.QUALITY_NOT_AS_DESCRIBED: (
        "the material feels much cheaper than what was described",
        "quality is nowhere near what the listing promised",
        "fabric quality bekaar hai, photos mein bahut acha dikh raha tha",
        "it works but the finish is far below what I expected for this price",
        "the product is not as described, quality is very poor",
    ),
    ReasonCode.CHANGED_MIND_LATE: (
        "I have changed my mind about this purchase and want to return it",
        "I no longer need this, please take it back",
        "ab mujhe ye nahi chahiye, return karna hai",
        "bought it on impulse and would like to send it back",
        "I want to return this, there is nothing wrong with it I just do not want it",
    ),
    ReasonCode.UNCLASSIFIED: (
        "there is a problem with my order, please look into it",
        "something is wrong with this order and I need help",
        "order mein dikkat hai, please dekho",
        "I need assistance regarding my recent order",
        "please check my order, I am not happy",
    ),
}

_OPENERS: Final[tuple[str, ...]] = (
    "Hi team,",
    "Hello,",
    "Hi,",
    "Sir/Madam,",
    "Hi support,",
    "Team,",
)

# The anchor is never sentence-initial, and that is a deliberate constraint
# rather than a stylistic one. An earlier version capitalised the anchor to
# start the sentence, which both changed its first character *and* lowercased
# the rest -- `str.capitalize()` does both. The verbatim-survival assertion in
# build.py caught it immediately. Always leading in means the claim can be
# copied out of the corpus and grepped in the rendered message unchanged.
_LEAD_INS: Final[tuple[str, ...]] = (
    "I am writing because",
    "raising this because",
    "the issue is that",
    "problem is,",
    "reaching out since",
    "I want to report that",
)

_ORDER_REFS: Final[tuple[str, ...]] = (
    "order #{order}",
    "order no {order}",
    "my order {order}",
    "order id {order}",
)

_CLOSERS: Final[tuple[str, ...]] = (
    "Please refund.",
    "Kindly process a refund.",
    "Please look into this and refund my amount.",
    "Waiting for your response.",
    "Please resolve this quickly.",
    "Refund kar dijiye please.",
    "",
)

_PRESSURE: Final[tuple[str, ...]] = (
    "",
    "",
    "This is very disappointing.",
    "I have been a customer for two years.",
    "Very poor experience honestly.",
    "Bahut kharab service hai.",
)


def _pick(options: tuple[str, ...], seed: str, salt: str) -> str:
    """Deterministic choice. The same item id always yields the same message.

    Hash-based rather than ``random`` so there is no global seed to forget, and
    so a single item can be regenerated in isolation.
    """
    h = hashlib.sha256(f"{seed}|{salt}".encode()).digest()
    return options[h[0] % len(options)]


def anchor_for(reason: ReasonCode, item_id: str) -> str:
    """The clause-bearing claim for this item. Never mutated by rendering."""
    order = 4000 + (int(hashlib.sha256(item_id.encode()).hexdigest()[:4], 16) % 5000)
    return _pick(ANCHORS[reason], item_id, "anchor").format(order=order)


def render(reason: ReasonCode, item_id: str, *, injected: str = "") -> tuple[str, str]:
    """Return ``(message, anchor)``.

    ``injected`` is appended verbatim for attack items. It is deliberately
    placed *after* the anchor rather than woven through it: an attack that
    rewrote the claim would change the honest oracle label and turn the item
    from an attack into a differently-labelled benign case, which is the
    tautology this corpus exists to avoid.
    """
    order = 4000 + (int(hashlib.sha256(item_id.encode()).hexdigest()[:4], 16) % 5000)
    anchor = anchor_for(reason, item_id)

    opener = _pick(_OPENERS, item_id, "open")
    lead = _pick(_LEAD_INS, item_id, "lead")
    ref = _pick(_ORDER_REFS, item_id, "ref").format(order=order)
    closer = _pick(_CLOSERS, item_id, "close")
    pressure = _pick(_PRESSURE, item_id, "press")

    body = f"{lead} {anchor} for {ref}."
    parts = [p for p in (opener, body, pressure, closer, injected) if p]
    message = " ".join(parts).strip()
    assert anchor in message, "the grammar mutated the anchor"
    return message, anchor
