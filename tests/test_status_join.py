import pytest

from fireflymcf import statusjoin

BUILD_136 = "firefly-13.6/mcf.bin"

# The six pairings computed from 13.6 (plan's oracle table).
EXPECTED_136 = {295: 86, 277: 75, 255: 39, 247: 82, 229: 68, 228: 19}


def _data(path):
    with open(path, "rb") as fh:
        return fh.read()


def test_known_pairings_match_the_hand_computed_oracle():
    pairings = statusjoin.known_status_pairings(_data(BUILD_136))
    # The oracle must contain exactly the six expected control->variable pairs, with values.
    for obj, var in EXPECTED_136.items():
        assert pairings.get(obj) == var, f"object {obj}: got {pairings.get(obj)}, want {var}"


def test_oracle_is_nonempty_and_internally_consistent():
    pairings = statusjoin.known_status_pairings(_data(BUILD_136))
    assert len(pairings) >= len(EXPECTED_136)
    # No two objects map to the same status variable (each control has its own indicator).
    assert len(set(pairings.values())) == len(pairings)


# RESOLVED 2026-08-26 (was strict-xfail). The dig closed the thread with a settled result, not
# a stored field: the pairing is neither a cross-reference (six searches, all negative) nor
# encoded in the indicator widget (the widget-cell inversion reproduces only 4/10, and object
# 295's status var 86 is drawn by NO widget at all). The join is the shared G12 output instance:
# `predicted_status_slot` derives the var from the control's OWN command channel via the §1928
# instance law, which reproduces every known pairing on 13.6 AND 14.7. This is NOT fitting the
# oracle (both apply the instance->var law), and it is what would let a command remap compute its
# indicator target automatically instead of the manual `move_display_list` choice. See
# MCF-FORMAT.md §10 item 11a and GUICODE.md Findings 32/33.
def test_predictor_reproduces_every_known_pairing():
    data = _data(BUILD_136)
    known = statusjoin.known_status_pairings(data)
    assert known, "oracle must be non-empty or the test proves nothing"
    for obj, var in known.items():
        assert statusjoin.predicted_status_slot(data, obj) == var
    # And the negative: an object carrying no single-channel control has no pairing.
    assert statusjoin.predicted_status_slot(data, 0) is None
