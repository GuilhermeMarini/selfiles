"""The shipped relay model profiles: derived bits and XML type ownership.

Everything asserted here was measured against the corpus (the GLE files inside
`cache/rdb/*/extracted/`) and against Relay Words read from real relays (the
GLV FID caches in `cache/`). The measurements are written down in each
profile's `_notes` / `_measurements`; these tests keep the JSON honest.
"""

from __future__ import annotations

import json
import logging

import pytest

from sellib._paths import PACKAGE_DATA
from sellib.models import relay_models as rm
from sellib.scl.mms_tables import norm_part

RELAY_MODELS_DIR = PACKAGE_DATA / "relay_models"

FAMILIA_4XX = ("SEL-411L", "SEL-451", "SEL-487E")


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    rm.load_relay_models()


# -- o bloco ACT ------------------------------------------------------------

@pytest.mark.parametrize("model", FAMILIA_4XX)
def test_the_4xx_family_derives_the_automation_timer_bit(model):
    """ACNDTIMER is PCNDTIMER's automation twin, and its bit is ACTnnQ.

    Measured: `type="ACNDTIMER"` appears in the GLE files of the 411L, 451 and
    487E, with physical_instance_name ACT01.., and the Relay Word of an
    SEL-411L-A-R133 and an SEL-451-5-R331 carries ACT01Q..ACT32Q.
    """
    m = rm.lookup(model)
    assert m is not None, model
    assert m.derived_bit_for("ACNDTIMER", 1) == "ACT01Q"
    assert m.derived_bit_for("ACNDTIMER", 12) == "ACT12Q"
    # And the protection one is still where it was.
    assert m.derived_bit_for("PCNDTIMER", 1) == "PCT01Q"


@pytest.mark.parametrize("model", FAMILIA_4XX)
def test_the_automation_timer_is_shaped_like_the_protection_one(model):
    """It is a copy of the PCT: same drawing, same ports, only the bit differs."""
    m = rm.lookup(model)
    pct = m.blocks["PCNDTIMER"]
    act = m.blocks["ACNDTIMER"]
    assert act.kind == pct.kind == "timer"
    assert act.input_sublabels == pct.input_sublabels == ("in", "PU", "DO")
    assert act.output_sublabels == pct.output_sublabels == ("Q",)
    assert act.extra["geometry"] == pct.extra["geometry"]
    assert act.extra["min_ports"] == pct.extra["min_ports"] == {"inputs": 3,
                                                                "outputs": 1}


@pytest.mark.parametrize("model", FAMILIA_4XX)
def test_a_latch_bit_has_no_q_suffix(model):
    """PLTnn/ALTnn, not PLTnnQ/ALTnnQ.

    The Q belongs to the timers and counters. The 411L profile had the ALT
    carrying the timer's drawing and bit by mistake (`ALTnnQ`, with in/PU/DO
    inputs), so it asked the relay for a bit that does not exist and an
    automation latch never lit up.
    """
    m = rm.lookup(model)
    assert m.derived_bit_for("PLT", 3) == "PLT03"
    assert m.derived_bit_for("ALT", 3) == "ALT03"
    assert m.blocks["ALT"].input_sublabels == ("S", "R")


# -- one XML type, one owner ------------------------------------------------

def test_no_profile_has_two_blocks_claiming_the_same_xml_type(caplog):
    """Two blocks claiming one `gle_xml_type` means one of them loses.

    The 487E declared both `["ALT", "LATCH"]` and `["PLT", "LATCH"]`: the last
    definition won, so in a GLE writing the generic LATCH type every
    protection latch would have become ALTnn. Measured across the corpus: no
    4xx GLE writes a generic LATCH or TIMER -- that is 3xx/2xxx/7xx dialect.
    """
    for path in sorted(RELAY_MODELS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        dono: dict[str, str] = {}
        for key, raw in (data.get("blocks") or {}).items():
            tipos = raw.get("gle_xml_type") or key
            if isinstance(tipos, str):
                tipos = [tipos]
            for t in tipos:
                anterior = dono.get(t.upper())
                assert anterior is None, (
                    f"{path.name}: XML type {t!r} reivindicado por "
                    f"{anterior!r} e {key!r}"
                )
                dono[t.upper()] = key

    # And the loader agrees: reloading must not raise the collision warning.
    with caplog.at_level(logging.WARNING, logger=rm.__name__):
        rm.load_relay_models(force=True)
    assert "mapeado por" not in caplog.text


# -- o perfil do SEL-451 ----------------------------------------------------

def test_the_451_is_found_by_the_relaytype_the_rdbs_carry():
    """RELAYTYPE medido nos RDB do acervo: `SEL-451-5` e `451-5`."""
    for relaytype in ("SEL-451-5", "451-5", "SEL-451", "451"):
        m = rm.lookup(relaytype)
        assert m is not None and m.model == "SEL-451", relaytype


def test_the_451_reads_its_identifiers_where_the_4xx_keeps_them():
    """Measured on the 451s in the corpus: IPADDR in SET_P5.TXT, RID in
    SET_G1.TXT, and no SET_*.TXT carrying a MAC -- that is hardware."""
    m = rm.lookup("SEL-451-5")
    assert (m.ip_address_file or "").lower() == "set_p5.txt"
    assert m.ip_address_key == "IPADDR"
    assert m.relay_id is not None
    assert (m.relay_id.file or "").lower() == "set_g1.txt"
    assert m.relay_id.key == "RID"
    assert m.mac_address is None
    # On a 4xx the Relay Word comes from the TARGET region, not Fast Meter.
    assert m.fast_read == "target_region"


def test_the_451_derives_the_bits_its_relay_word_really_has():
    """Conferido contra `cache/SEL-451-5-R331-V1-Z033014-D20250919.json`."""
    m = rm.lookup("SEL-451-5")
    esperado = {
        ("PCNDTIMER", 1): "PCT01Q",
        ("ACNDTIMER", 1): "ACT01Q",
        ("PLT", 1): "PLT01",
        ("ALT", 1): "ALT01",
        ("PCN", 1): "PCN01Q",
        ("ACN", 2): "ACN02Q",
        ("AST", 1): "AST01Q",
        ("PST", 1): "PST01Q",
        ("PSV", 1): "PSV01",
        # ASV takes THREE digits (ASV001..ASV256), unlike PSV.
        ("ASV", 7): "ASV007",
    }
    for (xml_type, inst), bit in esperado.items():
        assert m.derived_bit_for(xml_type, inst) == bit, xml_type


def test_the_451_knows_both_current_banks_are_analog():
    """The 451 has two current banks, W and X. The 411L profile covers only
    `^I[ABC]W$` and would let IAX/IBX/ICX pass as Relay Word bits."""
    m = rm.lookup("SEL-451-5")
    for nome in ("IAW", "IBW", "ICW", "IAX", "IBX", "ICX"):
        assert m.is_analog_symbol(nome), nome
        assert m.analog_group_for(nome).key == "WND"
    for nome in ("AMV001", "VABRMS", "VAY", "VCZ"):
        assert m.is_analog_symbol(nome), nome
    # And a bit is still a bit.
    for nome in ("PCT01Q", "ACT01Q", "T1_LED"):
        assert not m.is_analog_symbol(nome), nome


def test_the_451_speaks_the_explicit_4xx_dialect():
    """Measured: the 451's GLE files write PLT/PCNDTIMER/AST explicitly and never
    the generic TIMER/LATCH/COUNTER types, which belong to the 3xx/2xxx/7xx."""
    m = rm.lookup("SEL-451-5")
    for generico in ("TIMER", "LATCH", "COUNTER"):
        assert generico not in m.blocks_by_xml_type, generico


def test_every_shipped_profile_still_loads():
    """A broken JSON disappears quietly: `_load_one` swallows the error and logs."""
    nomes = {p.stem for p in RELAY_MODELS_DIR.glob("*.json")}
    for nome in nomes:
        assert rm.lookup(nome) is not None, nome
    assert "SEL-451" in nomes


# -----------------------------------------------------------------------------
# The two registries: data/relay_models/ and data/wordbits/
# -----------------------------------------------------------------------------

#: Models present in one registry and deliberately not the other. A gap that is
#: written down is a decision; a gap that is not is an accident waiting to be
#: "fixed" by someone who does not know why it is there.
KNOWN_ASYMMETRIES = {
    "SEL-311L": "wordbits ausente: nao ha perfil DNP do 311L em docs/ nem "
                "captura da GLV. A validacao de nomes fica desligada e a tela "
                "diz isso.",
    "SEL-421": "relay model ausente: ha perfil DNP (docs/dnp_r*421*.zip), "
               "entao os nomes DNP validam, mas ninguem construiu o perfil de "
               "GLE/Fast Meter. Um 421 num RDB nao abre na GLV.",
}


def _registry_models(directory):
    import json
    out = {}
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        out[str(raw.get("model") or path.stem)] = path.name
    return out


def test_the_two_model_registries_agree_or_say_why_not():
    """`data/relay_models/` drives the GLV and the GLE tools; `data/wordbits/`
    drives the DNP map's name check. They are keyed the same way and filled
    from different sources, so they drift silently.

    Fails when a model appears in one and not the other without a line in
    `KNOWN_ASYMMETRIES` explaining it. Adding a profile means either adding
    both halves or writing down why only one exists."""
    models = set(_registry_models(RELAY_MODELS_DIR))
    wordbits = set(_registry_models(PACKAGE_DATA / "wordbits"))
    # wordbits files name themselves without the SEL- prefix ("751"), the
    # relay models with it ("SEL-751"). Compare on the normalised form.
    def norm(s):
        return s.upper().replace("SEL-", "")

    only_model = {m for m in models if norm(m) not in {norm(w) for w in wordbits}}
    only_wb = {w for w in wordbits if norm(w) not in {norm(m) for m in models}}

    known = {norm(k) for k in KNOWN_ASYMMETRIES}
    unexplained = sorted(
        {m for m in only_model if norm(m) not in known}
        | {w for w in only_wb if norm(w) not in known}
    )
    assert not unexplained, (
        f"modelo em um registry e nao no outro, sem explicacao: {unexplained}. "
        f"Complete o par ou acrescente a KNOWN_ASYMMETRIES dizendo por que nao.")


# `data/mms_map/` is the third registry. It is one-way on purpose: every
# shipped table must belong to a relay model, but a relay model needs no table
# -- a model with no ICD in the corpus is a gap in the vendor data, not a bug.
#
# The keys do not line up the way the other two do. A relay model covers a
# family (`SEL-311C`); an ICD part names a variant (`311C-1` -> `311C1`). So
# the match is a PREFIX, not equality.
MMS_MAP_KNOWN_EXTRA = {
    "2414": "sem relay model: o 2414 e' concentrador, nao abre na GLV, mas o "
            "ICD dele existe e a tabela nao custa nada.",
    "2440": "idem 2414.",
}


def test_every_shipped_mms_table_belongs_to_a_known_relay_model():
    from sellib.scl import mms_tables

    models = {norm_part(m.replace("SEL-", ""))
              for m in _registry_models(RELAY_MODELS_DIR)}
    shipped = set(mms_tables._load())
    known = {norm_part(k) for k in MMS_MAP_KNOWN_EXTRA}
    orphans = sorted(
        part for part in shipped
        if part not in known
        and not any(part.startswith(m) for m in models if m)
    )
    assert not orphans, (
        f"tabela MMS sem relay model: {orphans}. Ou acrescente o "
        f"data/relay_models/<MODELO>.json, ou registre em MMS_MAP_KNOWN_EXTRA "
        f"dizendo por que nao.")


def test_the_787_is_its_own_model_and_not_a_feeder_relay():
    """The SEL-787 is a transformer differential relay; the SEL-751 is a
    feeder relay. The 787 used to be an ALIAS of the 751.

    The consequence was measurable and silent: the 751 groups IA/IB/IC and
    VA/VB/VC, while a 787 measures per winding (IAW1, IBW2, IGW1, 3I2W1).
    Not one 787 channel matched a 751 pattern, so a 787 opened in the GLV with
    zero analog symbols recognised and nothing said why.

    Fails if the alias comes back, or if the winding patterns are lost."""
    seven_eight_seven = rm.lookup("787")
    assert seven_eight_seven is not None
    assert seven_eight_seven.model == "SEL-787"

    # Channel names measured from the SEL DNP3 device profiles in docs/
    # (dnp_r100787.zip and dnp_r100787-4.zip), with and without the `_MAG`
    # suffix the 8-char DNP label limit forces.
    measured = ["IAW1", "IBW1", "ICW1", "IAW2", "IGW1", "3I2W1", "I1W1",
                "IAVW1", "IAW1_MAG", "3I2W2MAG"]
    assert all(seven_eight_seven.is_analog_symbol(c) for c in measured)

    feeder = rm.lookup("751")
    assert feeder.model == "SEL-751"
    assert not any(feeder.is_analog_symbol(c) for c in measured)


def test_a_model_with_no_profile_resolves_to_nothing_rather_than_the_wrong_one():
    """The SEL-710 is a motor relay and was also aliased onto the 751. It has
    no profile of its own yet -- no DNP profile in `docs/`, no GLV capture --
    so it now resolves to None.

    That is the intended state, not an oversight: with no model the tool says
    so, and resolved to the wrong model it lies quietly. Fails if someone
    re-adds the alias to make the warning go away."""
    assert rm.lookup("710") is None
    assert rm.lookup("SEL-710") is None
