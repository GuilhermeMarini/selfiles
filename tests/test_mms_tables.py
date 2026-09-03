"""The shipped ICD fallback tables.

These are the FALLBACK. The project SCD is preferred because it is the
as-built map; a table only says what the factory ICD had. Measured on a live
SEL-451-5 R331 -- a firmware (R331) with no table of its own -- 99.2% of the
451/010 table's items still existed on the relay, which is why the nearest
group plus verification is a sound fallback and a bare guess would not be.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from selfiles._paths import PACKAGE_DATA
from selfiles.scl import mms_tables


def test_the_451_table_loads_and_maps_a_known_latch():
    t = mms_tables.lookup("451")
    assert t is not None
    assert t.bits["PLT01"] == ("ANN", "PLT1GGIO1$ST$Ind01$stVal")


def test_part_normalisation_folds_the_three_spellings():
    assert mms_tables.norm_part("311C-1") == mms_tables.norm_part("311C1")
    assert mms_tables.norm_part("311c_1") == "311C1"


def test_newest_group_wins_when_no_group_is_asked_for():
    t = mms_tables.lookup("451")
    every = mms_tables.groups_for("451")
    assert t.group == max(every)


def test_unknown_part_is_none_not_an_empty_table():
    """An empty table would read as 'nothing is addressable'; None means
    'no table', which is what the badge must say."""
    assert mms_tables.lookup("9999") is None


def test_locsta_dedup_picks_the_st_item_in_ann():
    """LOCSTA in 451/010 has 28 source rows across five logical devices plus
    breaker/disconnector CSWI nodes -- all `sAddr="db:LOCSTA"`, so they are
    all fed from the same Relay Word bit and read the same value. The choice
    among them is not "which is correct" but "which container the poll uses",
    and it must not depend on wordbits.json's internal ordering. Pins the
    collapse rule's actual output against the shipped 451/010 table."""
    t = mms_tables.lookup("451", "010")
    assert t.bits["LOCSTA"] == ("ANN", "LLN0$ST$LocSta$stVal")


def _load_generator():
    """Import `tools/mms_tables_from_wordbits.py` as a standalone module.

    It isn't a package (no `tools/__init__.py`), so it's loaded by file path
    rather than `import tools.mms_tables_from_wordbits`.
    """
    import importlib.util

    repo_root = Path(__file__).resolve().parents[1]

    spec = importlib.util.spec_from_file_location(
        "mms_tables_from_wordbits",
        repo_root / "tools" / "mms_tables_from_wordbits.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_collapse_prefers_st_then_lexicographic_ld_item_over_source_order():
    """Direct test of the collapse rule against crafted rows, not real ICD
    data -- because the real LOCSTA duplicate in 451/010 happens to have its
    ST/lowest-ld candidate as the LAST row in wordbits.json, so a table-only
    assertion would not actually catch a regression to "last wins". These
    rows are ordered so last-wins and the ST-then-lexicographic rule disagree,
    which fails this test if the collapse ever reverts to a bare
    dict-comprehension (last write wins)."""
    gen = _load_generator()
    rows = [
        {"bit": "TESTBIT", "ld": "ZZZ", "item": "LLN0$ST$Foo$stVal"},
        {"bit": "TESTBIT", "ld": "AAA", "item": "LLN0$ST$Foo$stVal"},
        # Last by source order, but not FC ST -- last-wins would pick this.
        {"bit": "TESTBIT", "ld": "MMM", "item": "LLN0$CO$Foo$Oper$ctlVal"},
    ]
    assert gen._collapse(rows) == {"TESTBIT": ["AAA", "LLN0$ST$Foo$stVal"]}


def test_collapse_ranks_co_last_ahead_of_lexicographic_order():
    """Round 1's mistake, pinned directly: ranking FCs by bare lexicographic
    (ld, item) comparison put `CO` (a command) ahead of `MX` (a measurement)
    for 32 real bits, because the string "CO" sorts before "MX". `CO` must
    lose to every reading FC regardless of `ld`/`item` spelling, and `ST`
    must still beat `MX`. Fails if `_collapse` ever goes back to a plain
    lexicographic tiebreak across FCs instead of `FC_PREFERENCE`."""
    gen = _load_generator()

    co_vs_mx = [
        # "AAA" < "ZZZ" lexicographically -- a bare (ld, item) tiebreak
        # across FCs would wrongly pick the CO row here.
        {"bit": "TESTBIT", "ld": "AAA", "item": "FOO$CO$Bar$Oper$ctlVal"},
        {"bit": "TESTBIT", "ld": "ZZZ", "item": "FOO$MX$Bar$mxVal"},
    ]
    assert gen._collapse(co_vs_mx) == {"TESTBIT": ["ZZZ", "FOO$MX$Bar$mxVal"]}

    mx_vs_st = [
        {"bit": "TESTBIT", "ld": "AAA", "item": "FOO$MX$Bar$mxVal"},
        {"bit": "TESTBIT", "ld": "ZZZ", "item": "FOO$ST$Bar$stVal"},
    ]
    assert gen._collapse(mx_vs_st) == {"TESTBIT": ["ZZZ", "FOO$ST$Bar$stVal"]}


def test_no_bit_with_a_non_co_candidate_ends_up_on_a_co_item():
    """The property that actually failed in round 1: a bit with at least one
    reading candidate (ST/MX/SP/CF/DC) must never resolve to its CO item --
    CO is ranked last, never filtered out, so a bit that is genuinely CO-only
    (a remote-bit-style control point, e.g. RB01) still keeps it. Checked
    against the real ICD source across all 10 shipped tables; skipped where
    the source isn't available, since `fixtures/ICD files/SEL/wordbits.json`
    is gitignored (231 MB of vendor ICDs behind it)."""
    import json

    # The factory ICD corpus is 231 MB of vendor files and is not in any
    # repository. Point SELFILES_ICD_FIXTURES at it to run this; otherwise it
    # skips, which is the same arrangement it had before the move.
    import os

    fixtures = Path(os.environ.get("SELFILES_ICD_FIXTURES", "fixtures"))
    src_path = fixtures / "ICD files" / "SEL" / "wordbits.json"
    if not src_path.exists():
        pytest.skip("ICD corpus unavailable; set SELFILES_ICD_FIXTURES")

    gen = _load_generator()
    mms_tables.invalidate()
    models = json.loads(src_path.read_text(encoding="utf-8"))["models"]

    checked = 0
    for key, entry in models.items():
        if not entry.get("bits"):
            continue
        part, group = key.split("/", 1)
        if gen.norm(part) not in gen.CORPUS:
            continue
        t = mms_tables.lookup(part, group)
        assert t is not None, f"tabela ausente para {part}/{group}"

        by_bit: dict = {}
        for row in entry["bits"]:
            by_bit.setdefault(row["bit"].upper(), []).append(row["item"])

        for bit, items in by_bit.items():
            fcs = {gen._fc(i) for i in items}
            chosen_fc = gen._fc(t.bits[bit][1])
            if fcs != {"CO"}:
                assert chosen_fc != "CO", (
                    f"{part}/{group} {bit}: escolheu um item CO apesar de "
                    f"ter candidato nao-CO ({sorted(fcs)})")
            checked += 1

    assert checked > 20000, "varredura do corpus muito curta -- CORPUS mudou?"


# -- which DA is a bit, and which one wins ----------------------------------

class TestDaVocabulary:
    """A GLV bit is a BOOLEAN. Anything else read as one is a fabrication."""

    def test_boolean_status_das_are_the_ones_the_glv_can_paint(self):
        from selfiles.scl.mms_tables import is_boolean_status
        for da in ("stVal", "general", "phsA", "phsB", "phsC", "neut", "neg"):
            assert is_boolean_status(da), da

    def test_floats_counters_settings_quality_and_controls_are_not(self):
        from selfiles.scl.mms_tables import is_boolean_status
        for da in ("instMag.f", "phsA.instCVal.mag.f", "actVal", "setVal",
                   "setTm", "q", "valWTr.posVal", "Oper.ctlVal",
                   "Oper$ctlVal", "dirGeneral"):
            assert not is_boolean_status(da), da

    def test_both_separators_parse_the_same(self):
        """SCL descends an SDI with '.', MMS spells every level with '$'; the
        SCD source and the shipped table source use one each."""
        from selfiles.scl.mms_tables import da_parts
        assert da_parts("Oper.ctlVal") == da_parts("Oper$ctlVal")
        assert da_parts("stVal") == ("stVal",)

    def test_status_beats_anything_else_beats_a_control(self):
        from selfiles.scl.mms_tables import da_rank
        assert da_rank("stVal") < da_rank("actVal") < da_rank("Oper.ctlVal")
        assert da_rank("general") == da_rank("stVal")
        assert da_rank("Oper.ctlVal.f") == da_rank("Oper.ctlVal")
        assert da_rank("SBOw.ctlVal") == da_rank("Oper.ctlVal")


# -- os enderecos DECORADOS: um ponto 61850 que carrega dois bits -----------
#
# A SEL escreve um DPS (posicao de disjuntor/seccionadora) como UM ponto cujo
# valor codifica DOIS bits da Relay Word:
#
#     sAddr="db:52A|52B?0:1:2:3"   em BKR1CSWI1$ST$Pos$stVal
#
# As alternativas sao indexadas pela combinacao dos bits, PRIMEIRO NOME COMO
# BIT MAIS SIGNIFICATIVO -- (0,0)->0, (0,1)->1, (1,0)->2, (1,1)->3, que e'
# exatamente o Dbpos da IEC 61850 (intermediate/off/on/bad-state).
#
# Medido no corpus inteiro (SCD do projeto + os 345 ICD de fabrica), 132.250
# enderecos `db:`: 4.322 com um nome e 2 alternativas, 703 com dois nomes e 4
# alternativas, e `len(alternativas) == 2**len(nomes)` em 5.025 de 5.025. Um
# desvio dessa invariante e' uma forma que ninguem viu -- e chutar nela e'
# como um disjuntor acaba pintado fechado quando esta aberto.

class TestParseSaddr:

    def test_a_plain_address_is_one_name_and_no_rule(self):
        from selfiles.scl.mms_tables import parse_saddr
        spec = parse_saddr("db:PLT01")
        assert spec.names == ("PLT01",)
        assert spec.alternatives is None

    def test_a_double_bit_address_yields_both_names_and_the_alternatives(self):
        from selfiles.scl.mms_tables import parse_saddr
        spec = parse_saddr("db:52A|52B?0:1:2:3")
        assert spec.names == ("52A", "52B")
        assert spec.alternatives == (0, 1, 2, 3)

    def test_a_single_bit_address_can_carry_alternatives_too(self):
        """`52A?1:2` e' o mesmo Dbpos lido de um contato auxiliar so':
        aberto=1, fechado=2."""
        from selfiles.scl.mms_tables import parse_saddr
        spec = parse_saddr("db:52A?1:2")
        assert spec.names == ("52A",)
        assert spec.alternatives == (1, 2)

    def test_names_are_upper_cased_like_the_plain_ones(self):
        from selfiles.scl.mms_tables import parse_saddr
        assert parse_saddr("db:sv06|sv05?0:1:2:3").names == ("SV06", "SV05")

    def test_a_non_db_address_is_not_one_of_ours(self):
        from selfiles.scl.mms_tables import parse_saddr
        assert parse_saddr("imm:10000") is None
        assert parse_saddr("dbi:FID") is None
        assert parse_saddr("") is None

    def test_an_alternative_count_that_is_not_two_to_the_n_is_refused(self):
        """A invariante vale em 5.025 de 5.025 no corpus. Uma forma que a
        quebra e' desconhecida, e adivinhar nela pinta bit errado."""
        from selfiles.scl.mms_tables import parse_saddr
        assert parse_saddr("db:52A|52B?0:1:2") is None
        assert parse_saddr("db:52A?1:2:3") is None

    def test_a_non_numeric_alternative_is_refused(self):
        from selfiles.scl.mms_tables import parse_saddr
        assert parse_saddr("db:52A?ON:OFF") is None

    def test_an_empty_name_is_refused(self):
        from selfiles.scl.mms_tables import parse_saddr
        assert parse_saddr("db:?1:2") is None
        assert parse_saddr("db:52A|?0:1:2:3") is None


class TestDecodeBit:
    """O valor lido -> 0/1 por bit. Sem isto, `int(bool(valor))`.

    A py61850 devolve um Dbpos (BIT-STRING de 2 bits) como a STRING "10", e
    `bool("00")` e' True: deixar um ponto desses entrar no caminho booleano
    pintaria TODO disjuntor como fechado, sempre.
    """

    def _rule(self, sa, i):
        from selfiles.scl.mms_tables import parse_saddr
        return parse_saddr(sa).rule_for(i)

    def test_a_dbpos_bit_string_splits_into_the_two_named_bits(self):
        from selfiles.scl.mms_tables import decode_bit
        a = self._rule("db:52A|52B?0:1:2:3", 0)   # 52A
        b = self._rule("db:52A|52B?0:1:2:3", 1)   # 52B
        assert (decode_bit(a, "10"), decode_bit(b, "10")) == (1, 0)  # fechado
        assert (decode_bit(a, "01"), decode_bit(b, "01")) == (0, 1)  # aberto
        assert (decode_bit(a, "00"), decode_bit(b, "00")) == (0, 0)  # interm.
        assert (decode_bit(a, "11"), decode_bit(b, "11")) == (1, 1)  # bad

    def test_an_integer_status_matches_its_alternative(self):
        """`RELAY_EN?5:1` num `Mod$stVal`: 5=off, 1=on. A py61850 devolve um
        INTEGER como int, nao como string de bits."""
        from selfiles.scl.mms_tables import decode_bit
        r = self._rule("db:RELAY_EN?5:1", 0)
        assert decode_bit(r, 5) == 0
        assert decode_bit(r, 1) == 1

    def test_a_single_bit_dbpos_reads_from_the_bit_string_too(self):
        from selfiles.scl.mms_tables import decode_bit
        r = self._rule("db:52A?1:2", 0)
        assert decode_bit(r, "01") == 0      # Dbpos 1, aberto
        assert decode_bit(r, "10") == 1      # Dbpos 2, fechado

    def test_a_value_matching_no_alternative_is_no_reading_at_all(self):
        """Um Dbpos 3 (bad-state) contra um ponto `?1:2` nao e' 0 nem 1. O
        bit tem que sumir do payload e o diagrama pinta-lo indeterminado --
        a mesma regra do acesso que falha."""
        from selfiles.scl.mms_tables import decode_bit
        r = self._rule("db:52A?1:2", 0)
        assert decode_bit(r, "11") is None   # Dbpos 3
        assert decode_bit(r, "00") is None   # Dbpos 0
        assert decode_bit(r, 7) is None

    def test_junk_is_no_reading_and_never_an_exception(self):
        """O laco de polling nao pode morrer por causa de um valor estranho:
        mil outros bits dependem da mesma volta."""
        from selfiles.scl.mms_tables import decode_bit
        r = self._rule("db:52A|52B?0:1:2:3", 0)
        for junk in ("", "abc", None, True, 3.5, b"\x01", {"error": "x"}):
            assert decode_bit(r, junk) is None, junk


class TestDecoratedRank:
    """Um bit com endereco booleano E endereco decorado fica com o booleano.

    Medido em `QPC1_LT1_UPC1` do SCD da subestacao: 10 dos 33 nomes decorados
    tambem tem um `db:NOME` liso. `is_boolean_status("stVal")` e' True nos
    dois, entao sem um degrau proprio o desempate voltaria a ser ordem de
    documento.
    """

    def test_a_decorated_status_ranks_below_a_plain_boolean_one(self):
        from selfiles.scl.mms_tables import da_rank
        assert da_rank("stVal") < da_rank("stVal", decorated=True)

    def test_but_still_above_anything_else_and_above_a_control(self):
        from selfiles.scl.mms_tables import da_rank
        assert da_rank("stVal", decorated=True) < da_rank("actVal")
        assert da_rank("stVal", decorated=True) < da_rank("Oper.ctlVal")

    def test_the_existing_tiers_did_not_shift(self):
        """`tests/test_scd_saddr.py` compara `da_rank(...) == (2,)`."""
        from selfiles.scl.mms_tables import da_rank
        assert da_rank("stVal") == (0,)
        assert da_rank("actVal") == (1,)
        assert da_rank("Oper.ctlVal") == (2,)


class TestEnumStatusDas:
    """Onde os enderecos decorados caem, e o que continua recusado.

    Medido no corpus: as unicas (DO, DA) que recebem endereco decorado sao
    `Pos.stVal`, `Dir/Str.dirGeneral` e o `stVal` de
    Health/Mod/Beh/TrBeh/EEHealth/PhyHealth/ExConSt1 -- e NENHUM endereco
    liso cai numa delas (0 de 127.225).
    """

    def test_dirgeneral_is_readable_only_with_a_rule(self):
        from selfiles.scl.mms_tables import is_boolean_status, is_enum_status
        assert is_enum_status("dirGeneral")
        assert not is_boolean_status("dirGeneral")

    def test_stval_is_both_because_the_do_decides_not_the_da(self):
        """`stVal` e' o DA de um SPS booleano E o de um DPS/INS enumerado. E'
        a decoracao que separa os dois, nao o nome do DA -- por isso o portao
        em `resolve_map` exige a regra, e nao so' o nome."""
        from selfiles.scl.mms_tables import is_boolean_status, is_enum_status
        assert is_enum_status("stVal") and is_boolean_status("stVal")

    def test_a_float_or_a_control_is_not_an_enum_status_either(self):
        from selfiles.scl.mms_tables import is_enum_status
        for da in ("instMag.f", "Oper.ctlVal", "q", "setVal"):
            assert not is_enum_status(da), da


# -- a metade DECORADA da tabela de fabrica ---------------------------------
#
# `wordbits.json` -- a fonte das linhas lisas -- e' gerado por OUTRO projeto,
# cuja varredura de `sAddr` derruba a forma decorada do mesmo jeito que esta
# derrubava. Corrigir o parser deste repositorio nao alcanca aquele arquivo,
# entao o gerador le' os ICD DIRETAMENTE pra essa metade. `wordbits.json`
# continua intocado, e as linhas lisas saem identicas.
#
# O ICD nao esta no git (231 MB de arquivos de fabrica, e `fixtures/ICD files/`
# esta no .gitignore), entao o teste usa o mesmo SCL de fixture do parser --
# um ICD e' SCL como qualquer outro.

FIXTURE_SCL = Path(__file__).parent / "fixtures" / "saddr_min.scd"


class TestDecoratedTableRows:

    def test_it_finds_the_double_bit_point_and_writes_both_bits(self):
        gen = _load_generator()
        rows = gen.decorated_rows(FIXTURE_SCL)
        assert rows["52A"] == ["ANN", "BKR1CSWI1$ST$Pos$stVal", [[0, 1, 2, 3], 0, 2]]
        assert rows["52B"] == ["ANN", "BKR1CSWI1$ST$Pos$stVal", [[0, 1, 2, 3], 1, 2]]

    def test_the_fc_comes_from_the_templates_and_not_from_a_constant(self):
        """Os 2.030 enderecos decorados dos 146 ICD do corpus sao todos `ST`,
        mas quem diz isso e' o `DataTypeTemplates` do arquivo."""
        gen = _load_generator()
        assert gen.decorated_rows(FIXTURE_SCL)["52A"][1].split("$")[1] == "ST"

    def test_a_plain_address_is_not_a_decorated_row(self):
        """As linhas lisas continuam vindo do `wordbits.json`; esta passagem
        so' acrescenta o que aquela fonte nao tem."""
        gen = _load_generator()
        assert "PLT01" not in gen.decorated_rows(FIXTURE_SCL)

    def test_a_malformed_decoration_produces_no_row(self):
        gen = _load_generator()
        assert "BADFORM" not in gen.decorated_rows(FIXTURE_SCL)

    def test_a_plain_row_already_in_the_table_is_never_overwritten(self):
        """`LOC` tem endereco liso E decorado. O liso ganha, aqui como no
        SCD -- e as linhas ja publicadas nao podem mudar de item por causa
        desta passagem."""
        gen = _load_generator()
        plain = {"LOC": ["ANN", "LLN0$ST$Loc$stVal"]}
        merged = gen.merge_decorated(plain, gen.decorated_rows(FIXTURE_SCL))
        assert merged["LOC"] == ["ANN", "LLN0$ST$Loc$stVal"]
        assert merged["52A"][2] == [[0, 1, 2, 3], 0, 2]


class TestTheShippedTablesStayLoadable:

    def test_every_shipped_row_is_two_or_three_elements(self):
        import json

        for path in sorted((PACKAGE_DATA / "mms_map").glob("*.json")):
            for bit, entry in json.loads(
                    path.read_text(encoding="utf-8"))["bits"].items():
                assert len(entry) in (2, 3), f"{path.name} {bit}: {entry}"

    def test_a_two_element_row_still_loads_with_no_rule(self):
        t = mms_tables.lookup("451")
        assert t.bits["PLT01"][:2] == ("ANN", "PLT1GGIO1$ST$Ind01$stVal")


class TestOneBadFileCannotTakeTheRegistryDown:
    """A malformed `data/mms_map/*.json` used to break every model, twice.

    `_load()` parsed each file with no guard, so the first call raised
    `JSONDecodeError` out of `lookup()` -- on the GLV's connect path, for a
    relay that had nothing to do with the broken file. And because the cache
    was guarded by `if _CACHE:` (truthiness, not a loaded flag), the entries
    read before the failure stayed behind: the SECOND call returned a
    half-loaded registry and said nothing. Measured on two real tables with a
    broken file sorting between them: 411L found, 751 silently missing.

    `core.wordbits` and `core.relay_models` both already stated this policy in
    their own loaders -- one bad file must never take the rest down.
    """

    @pytest.fixture(autouse=True)
    def _restore_registry(self):
        """The cache is module state, and these tests fill it with fakes.

        `monkeypatch` puts `data_dirs` back but knows nothing about the
        memoised tables behind it -- and the `_LOADED` flag under test is
        exactly what stops the next `_load()` from noticing. Without this the
        fake tables leak into every module that runs after this one.
        """
        from selfiles.scl import mms_tables as mt

        yield
        mt.invalidate()

    def _registry(self, tmp_path, monkeypatch, files):
        from selfiles.scl import mms_tables as mt

        for name, body in files.items():
            (tmp_path / name).write_text(body, encoding="utf-8")
        monkeypatch.setattr(mt._paths, "data_dirs",
                            lambda name: [tmp_path])
        mt.invalidate()
        return mt

    def _table(self, part, bit="52A"):
        return json.dumps({"part": part, "group": "010",
                           "bits": {bit: ["ANN", f"{bit}$ST$stVal"]}})

    def test_a_broken_file_does_not_raise(self, tmp_path, monkeypatch):
        mt = self._registry(tmp_path, monkeypatch, {
            "411L-010.json": self._table("411L"),
            "500-bad.json": "{ not json",
            "751-010.json": self._table("751"),
        })
        assert mt.lookup("411L") is not None

    def test_the_files_after_the_broken_one_still_load(self, tmp_path,
                                                       monkeypatch):
        """The half-loaded registry is the part that lied."""
        mt = self._registry(tmp_path, monkeypatch, {
            "411L-010.json": self._table("411L"),
            "500-bad.json": "{ not json",
            "751-010.json": self._table("751"),
        })
        mt.lookup("411L")
        assert mt.lookup("751") is not None, "o 751 sumiu por causa do arquivo quebrado"

    def test_a_file_missing_a_required_field_is_skipped_not_fatal(
            self, tmp_path, monkeypatch):
        mt = self._registry(tmp_path, monkeypatch, {
            "aaa-010.json": json.dumps({"group": "010", "bits": {}}),  # sem 'part'
            "751-010.json": self._table("751"),
        })
        assert mt.lookup("751") is not None

    def test_an_empty_directory_is_not_rescanned_forever(self, tmp_path,
                                                         monkeypatch):
        """`if _CACHE:` also meant a re-glob of the disk on every lookup."""
        mt = self._registry(tmp_path, monkeypatch, {})
        assert mt.lookup("751") is None
        calls = []
        real_glob = type(tmp_path).glob

        def counting_glob(self, pattern):
            calls.append(pattern)
            return real_glob(self, pattern)

        monkeypatch.setattr(type(tmp_path), "glob", counting_glob)
        mt.lookup("751")
        mt.lookup("411L")
        assert calls == [], "o diretorio foi varrido de novo apos o load"
