"""The renderer and the relay profiles must describe the same block.

`data/relay_models/*.json` declares, per block, a `geometry`, a `css_class`,
`default_ports` and a `label_glyph`. `gle.py` holds its own
`ELEMENT_MIN_SIZE`, `PORT_FIRST_OFFSET`, `DEFAULT_PORTS` and `GATE_GLYPHS`.
Two sources for one fact, and nothing compared them -- which is how the
counter shipped with `css_class="element-counter"` and no CSS rule (a black
box on the pages whose whole purpose is counters), and how six block types the
profiles declare came to have no branch in `render_element` at all and drew as
NOTHING.

`tests/test_gle_css_completeness.py` guards the class the RENDERER asks for.
This guards the other direction: what the PROFILES declare.

The rule for a disagreement is the one `test_relay_models.py` already uses for
the registries -- it fails unless the pair is written down in
`KNOWN_DISAGREEMENTS` with the reason. Silence is what let the last two
through.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from selfiles.gle import (
    CSS,
    DEFAULT_FIRST_OFFSET,
    DEFAULT_PORTS,
    ELEMENT_MIN_SIZE,
    PORT_FIRST_OFFSET,
    element_info,
    render_element,
)

MODELS_DIR = Path(__file__).resolve().parent.parent / "src" / "selfiles" / "data" / "relay_models"

#: `(model, xml_type, field)` -> why the profile and the renderer differ.
#: Add a line only with a reason; an entry here is a decision, not a snooze.
KNOWN_DISAGREEMENTS = {
    ("SEL-451", "ADD", "min_size"):
        "The 451 profile draws ADD, DIV and SQRT at MULT's 30x18 while the "
        "renderer gives every arithmetic block but MULT 36x24. A GLE records "
        "an element's position and NOT its size -- the renderer chooses that "
        "-- so there is no measurement that settles it, and the corpus cannot "
        "be asked. Left as the renderer has always drawn it rather than "
        "changed on a preference; the profile is the outlier of the seven.",
    ("SEL-451", "DIV", "min_size"):
        "Same as SEL-451/ADD.",
}


def _profiles():
    for path in sorted(MODELS_DIR.glob("*.json")):
        yield path.stem, json.loads(path.read_text(encoding="utf-8"))


def _xml_types(key: str, block: dict) -> list[str]:
    xt = block.get("gle_xml_type") or key
    return xt if isinstance(xt, list) else [xt]


def _declared_blocks():
    """`(model, xml_type, block)` for every block every profile declares."""
    for model, data in _profiles():
        for key, block in (data.get("blocks") or {}).items():
            for xml_type in _xml_types(key, block):
                yield model, xml_type, block


def _element(xml_type: str, name: str = "X01") -> dict:
    el = ET.fromstring(
        f'<element id="1" type="{xml_type}" left="10" top="20">'
        f'<logic_element type="{xml_type}" physical_instance_name="{name}">'
        f"<comment /><ports /><ports /></logic_element></element>"
    )
    return element_info(el)


# -- the invisible-block class ----------------------------------------------

def test_every_block_a_profile_declares_is_actually_drawn():
    """The bug this pins: `render_element` fell through to `return ""` for any
    type without a branch, so `ACN`, `PST` and `SQRT` -- all declared by the
    SEL-451 profile, all present in the reference corpus (8, 8 and 1
    instances) -- were positioned on the page and drawn as nothing.

    A wrong shape on a protection diagram is visible and gets reported. A
    missing one reads as "there is no logic here".
    """
    missing = sorted({
        f"{model}/{xml_type}"
        for model, xml_type, _ in _declared_blocks()
        if not render_element(_element(xml_type), 0, 0)
    })
    assert not missing, f"declared by a relay profile and drawn as nothing: {missing}"


def test_every_css_class_a_profile_declares_has_a_rule():
    """The registry half of the counter bug: the profile named
    `element-counter` too, and an SVG rect with no `fill` defaults to black."""
    undeclared = sorted({
        css for _model, _xt, block in _declared_blocks()
        if (css := block.get("css_class")) and f".{css}" not in CSS
    })
    assert not undeclared, f"css_class declared by a profile, absent from CSS: {undeclared}"


# -- the drift the two sources can have -------------------------------------

def _disagreements():
    out = []
    for model, xml_type, block in _declared_blocks():
        geo = block.get("geometry") or {}
        ports = block.get("default_ports") or {}

        if geo.get("min_width") is not None:
            have = ELEMENT_MIN_SIZE.get(xml_type)
            want = (geo["min_width"], geo["min_height"])
            if have is None or tuple(have) != want:
                out.append(((model, xml_type, "min_size"), want, have))

        if geo.get("port_first_offset_y") is not None:
            have = PORT_FIRST_OFFSET.get(xml_type, DEFAULT_FIRST_OFFSET)
            if have != geo["port_first_offset_y"]:
                out.append(((model, xml_type, "first_offset"),
                            geo["port_first_offset_y"], have))

        if ports.get("inputs") is not None:
            have = DEFAULT_PORTS.get(xml_type)
            want = (ports["inputs"], ports["outputs"])
            if have is None or tuple(have) != want:
                out.append(((model, xml_type, "default_ports"), want, have))
    return out


def test_the_profiles_and_the_renderer_agree_or_say_why_not():
    """Every block geometry a profile states must be the geometry the renderer
    draws, unless the pair is in `KNOWN_DISAGREEMENTS` with a reason.

    Measured when this test was written: 4 disagreements remained of the 196
    the two sources started with, and two of them were a real defect -- the
    SEL-751 and SEL-787 profiles said a COUNTER has one output while all 1478
    COUNTER elements on real SEL-751 relays in the reference corpus declare
    two. The profiles were corrected; the renderer's `DEFAULT_PORTS` was
    corrected with them.
    """
    unexplained = [
        f"{model}/{xml_type}: profile says {want}, renderer says {have}"
        for (model, xml_type, field), want, have in _disagreements()
        if (model, xml_type, field) not in KNOWN_DISAGREEMENTS
    ]
    assert not unexplained, (
        "the relay profile and the renderer disagree, and nothing says why:\n  "
        + "\n  ".join(unexplained)
    )


@pytest.mark.parametrize("pair", sorted(KNOWN_DISAGREEMENTS))
def test_each_known_disagreement_is_still_real(pair):
    """A written-down disagreement that has since been resolved must lose its
    line, or the list becomes a place explanations go to die."""
    live = {key for key, _want, _have in _disagreements()}
    assert pair in live, (
        f"{pair} is in KNOWN_DISAGREEMENTS but the two sources now agree -- "
        "delete the entry"
    )
