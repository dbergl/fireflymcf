"""G12 OUTPUTS names (`outputs`) and the button ops' refusal to wire a named output."""
import hashlib
import json
from pathlib import Path

import pytest

from fireflymcf import outputs, recipe

STOCK = Path("firefly-13.6/mcf.bin")


@pytest.fixture(scope="module")
def stock():
    return STOCK.read_bytes()


def test_names_come_from_the_outputs_list(stock):
    names = outputs.output_names(stock)
    assert len(names) == 44
    assert names[12] == "A/C Fan Low" and names[44] == "Water Pump"
    assert names[28] == "" and names[29] == ""


def _compile(stock, op):
    project = recipe.Project.from_json(json.dumps({
        "schema": 1, "base_sha256": hashlib.sha256(stock).hexdigest(),
        "build_name": "named output test", "ops": [op]}))
    return recipe.compile_project(project, stock)


def _porch(channel, **extra):
    return {"id": "porch", "type": "add_button",
            "params": dict({"channel": channel, "label": "Porch", "off_colour": [65, 81, 163]},
                           **extra)}


def test_add_button_refuses_an_output_named_for_another_load(stock):
    # The skill scenario's trap: the owner said "output 12" for a porch light; the panel's
    # own list names 12 "A/C Fan Low", and the channel-collision check does not catch it.
    res = _compile(stock, _porch(12))
    assert not res.ok
    [err] = res.errors
    assert err["code"] == "NAMED_OUTPUT" and "A/C Fan Low" in err["message"]
    assert err["offers"] == ["acknowledge_named_output"]


def test_allow_named_output_is_the_acknowledgement(stock):
    res = _compile(stock, _porch(12, allow_named_output=True))
    assert res.ok, res.errors


def test_an_unnamed_output_needs_no_acknowledgement(stock):
    res = _compile(stock, _porch(28, allow_commanded_channel=True))
    assert res.ok, res.errors


# --- recolor refuses a card of controls ---------------------------------------------------

def _recolor(stock, obj, **extra):
    return _compile(stock, {"id": "c", "type": "recolor",
                            "params": dict({"object": obj, "color": [27, 117, 188]}, **extra)})


def test_recolor_refuses_a_card_of_controls(stock):
    # HOME's lights card (905) has a box, so the box lookup succeeds, and repainting it turns
    # the whole card blue: Main Ceiling, Awning Light and Step Light together.
    res = _recolor(stock, 905)
    assert not res.ok and res.errors[0]["code"] == "CONTAINER"
    assert "3 touch regions" in res.errors[0]["message"]
    assert res.errors[0]["offers"] == ["acknowledge_container"]


def test_recolor_a_card_on_purpose(stock):
    assert _recolor(stock, 905, allow_container=True).ok


def test_recolor_one_button(stock):
    assert _recolor(stock, 279).ok
