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

from selfiles._paths import PACKAGE_DATA
from selfiles.models import relay_models as rm
from selfiles.scl.mms_tables import norm_part

RELAY_MODELS_DIR = PACKAGE_DATA / "relay_models"

FAMILIA_4XX = ("SEL-411L", "SEL-451", "SEL-487E")


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    rm.load_relay_models()


# -- o bloco ACT ------------------------------------------------------------

@pytest.mark.parametrize("model", FAMILIA_4XX)
def test_the_4xx_family_derives_the_automation_timer_bit(model):
    """ACNDTIMER é o gêmeo de automação do PCNDTIMER, e o bit é ACTnnQ.

    Medido: `type="ACNDTIMER"` aparece nos GLE de 411L, 451 e 487E, com
    physical_instance_name ACT01.., e a Relay Word do SEL-411L-A-R133 e do
    SEL-451-5-R331 traz ACT01Q..ACT32Q.
    """
    m = rm.lookup(model)
    assert m is not None, model
    assert m.derived_bit_for("ACNDTIMER", 1) == "ACT01Q"
    assert m.derived_bit_for("ACNDTIMER", 12) == "ACT12Q"
    # E o de proteção continua onde estava.
    assert m.derived_bit_for("PCNDTIMER", 1) == "PCT01Q"


@pytest.mark.parametrize("model", FAMILIA_4XX)
def test_the_automation_timer_is_shaped_like_the_protection_one(model):
    """É uma cópia do PCT: mesmo desenho, mesmas portas, só o bit muda."""
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
    """PLTnn/ALTnn, e não PLTnnQ/ALTnnQ.

    O Q é dos timers e contadores. O perfil do 411L tinha o ALT com o desenho
    e o bit do timer copiados por engano (`ALTnnQ`, entradas in/PU/DO), então
    pedia ao relé um bit que não existe e o latch de automação nunca acendia.
    """
    m = rm.lookup(model)
    assert m.derived_bit_for("PLT", 3) == "PLT03"
    assert m.derived_bit_for("ALT", 3) == "ALT03"
    assert m.blocks["ALT"].input_sublabels == ("S", "R")


# -- um tipo de XML, um dono ------------------------------------------------

def test_no_profile_has_two_blocks_claiming_the_same_xml_type(caplog):
    """Dois blocos com o mesmo `gle_xml_type` fazem um deles perder.

    O 487E declarava `["ALT", "LATCH"]` e `["PLT", "LATCH"]`: o último definido
    vencia, então num GLE que escrevesse o tipo genérico LATCH todo latch de
    proteção viraria ALTnn. Medido no acervo: nenhum GLE de 4xx escreve LATCH
    ou TIMER genéricos -- isso é dialeto de 3xx/2xxx/7xx.
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

    # E o loader concorda: recarregar não pode acender o aviso de colisão.
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
    """Medido nos 451 do acervo: IPADDR em SET_P5.TXT, RID em SET_G1.TXT,
    e nenhum SET_*.TXT com MAC (é hardware)."""
    m = rm.lookup("SEL-451-5")
    assert (m.ip_address_file or "").lower() == "set_p5.txt"
    assert m.ip_address_key == "IPADDR"
    assert m.relay_id is not None
    assert (m.relay_id.file or "").lower() == "set_g1.txt"
    assert m.relay_id.key == "RID"
    assert m.mac_address is None
    # 4xx: a Relay Word sai da região TARGET, não do Fast Meter.
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
        # ASV tem TRÊS dígitos (ASV001..ASV256), ao contrário do PSV.
        ("ASV", 7): "ASV007",
    }
    for (xml_type, inst), bit in esperado.items():
        assert m.derived_bit_for(xml_type, inst) == bit, xml_type


def test_the_451_knows_both_current_banks_are_analog():
    """O 451 tem dois bancos de corrente, W e X. O perfil do 411L só cobre
    `^I[ABC]W$` e deixaria IAX/IBX/ICX passarem por bit da Relay Word."""
    m = rm.lookup("SEL-451-5")
    for nome in ("IAW", "IBW", "ICW", "IAX", "IBX", "ICX"):
        assert m.is_analog_symbol(nome), nome
        assert m.analog_group_for(nome).key == "WND"
    for nome in ("AMV001", "VABRMS", "VAY", "VCZ"):
        assert m.is_analog_symbol(nome), nome
    # E um bit continua sendo um bit.
    for nome in ("PCT01Q", "ACT01Q", "T1_LED"):
        assert not m.is_analog_symbol(nome), nome


def test_the_451_speaks_the_explicit_4xx_dialect():
    """Medido: os GLE de 451 escrevem PLT/PCNDTIMER/AST explícitos e nunca os
    tipos genéricos TIMER/LATCH/COUNTER (esses são de 3xx/2xxx/7xx)."""
    m = rm.lookup("SEL-451-5")
    for generico in ("TIMER", "LATCH", "COUNTER"):
        assert generico not in m.blocks_by_xml_type, generico


def test_every_shipped_profile_still_loads():
    """Um JSON quebrado some em silêncio: `_load_one` engole o erro e loga."""
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
    from selfiles.scl import mms_tables

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
