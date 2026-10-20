"""aes_card_layout: the v48 four-card ELECTRICAL page (spec 2026-10-15).

The tests that compile the layout onto the reference coach's builds live in the private research
repository.
"""
from fireflymcf import cardlayout, options, records

COACH = options.CoachConfig(
    floorplan="WD",
    options=["AES Single Output", "Bunk Accent", "Cargo / Bath Light\n    (CH. 25)",
             "Black Tank", "Bath Fan"],
    extra_slots={6: 1, 37: 3, 113: 1, 512: 1, 38: 0, 39: 0})


def test_authored_labels_have_a_transparent_background():
    """v50 on the coach: the 'm' of 'Amps' lost its right edge -- an inherited (opaque) fillB lets
    each glyph cell paint over its neighbour. Stock captions use fillB mode 5 (transparent)."""
    rec = records.decode_record(cardlayout.title_record(86, 50, "Amps"), 0)
    assert ("fillB", 5, None) in rec.colors, rec.colors
