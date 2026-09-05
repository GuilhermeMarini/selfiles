"""Every class the renderer paints with must have a rule in the stylesheet.

This is the generic form of a bug that shipped: `render_pcn` and
`render_counter_7xx` drew counters with ``class="element-counter"`` and that
class was defined nowhere -- not in ``gle.CSS``, not in any template. An SVG
``<rect>`` with no ``fill`` declared defaults to BLACK, so every counter block
came out as a black box with a #222 label on top of it, on pages whose whole
purpose is counters (8 of the 418 ``.gle`` in the reference corpus).

Nothing pointed at it: the renderer had no idea the class was undefined, and
the golden SVG happened to contain no counter. So the test is not "counters are
green" -- it is "no shape carries a class the stylesheet never heard of", which
would have failed the day the class was introduced.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from sellib.gle import CSS, render_page

# Classes on a GROUP (`<g>`) are hooks for JavaScript and the live overlay, not
# paint: `symbol-grp` and `gate-grp` are how the dashboard finds an element.
# Only the shapes are checked here.
_SHAPES = {"rect", "circle", "polyline", "line", "path", "text"}

_PAGE = """
<page name="CONTADORES">
  <element id="1" type="COUNTER" left="10" top="10">
    <logic_element physical_instance_name="_SC01"/>
  </element>
  <element id="2" type="PCN" left="200" top="10">
    <logic_element physical_instance_name="PCN03"/>
  </element>
  <element id="3" type="SYMBOL" left="400" top="10">
    <logic_element physical_instance_name="IN101"/>
  </element>
  <element id="4" type="PLT" left="500" top="10">
    <logic_element physical_instance_name="_PLT04"/>
  </element>
  <element id="5" type="PCNDTIMER" left="600" top="10">
    <logic_element physical_instance_name="PCT02"/>
  </element>
  <element id="6" type="AND" left="700" top="10"/>
  <group id="g1"><label>GRUPO</label>
    <element id="7" type="OR" left="800" top="10"/>
  </group>
  <connection>
    <source_port element_id="1" port_number="0"/>
    <sink_port element_id="6" port_number="0"/>
    <points><point x="76" y="34"/><point x="700" y="22"/></points>
  </connection>
</page>
"""


def _classes_on_shapes(svg: str) -> set[str]:
    root = ET.fromstring(svg)
    out: set[str] = set()
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in _SHAPES:
            continue
        out.update((el.get("class") or "").split())
    return out


def _declared_in(css: str) -> set[str]:
    return set(re.findall(r"\.([A-Za-z][\w-]*)", css))


@pytest.fixture(scope="module")
def rendered() -> str:
    return render_page(ET.fromstring(_PAGE))


def test_the_counter_class_has_a_rule(rendered):
    """The regression itself: a counter is drawn, and it is not a black box."""
    assert 'class="element-counter"' in rendered
    assert ".element-counter" in CSS


def test_no_shape_carries_a_class_the_stylesheet_never_declares(rendered):
    painted = _classes_on_shapes(rendered)
    declared = _declared_in(CSS)
    missing = sorted(c for c in painted if c not in declared)
    assert not missing, (
        f"classes desenhadas mas sem regra no CSS: {missing}. "
        "Um <rect> sem `fill` declarado cai no preto padrao do SVG."
    )


def test_every_css_class_the_renderers_ask_for_is_declared():
    """The static half: the `css_class=` arguments in the module itself."""
    import inspect

    from sellib import gle

    asked = set(re.findall(r'css_class="([\w-]+)"', inspect.getsource(gle)))
    declared = _declared_in(CSS)
    assert asked, "nenhum css_class encontrado -- o teste perdeu o alvo"
    assert not (asked - declared), sorted(asked - declared)
