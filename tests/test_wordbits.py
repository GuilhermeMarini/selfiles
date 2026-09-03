"""Registry of word bits valid per relay model."""

from __future__ import annotations

import json

from selfiles.models import wordbits


def _write(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


def _load(tmp_path, monkeypatch):
    monkeypatch.setattr(wordbits, "_CACHE", None, raising=False)
    # `data_dirs` is the seam: the registry asks it where to look, and a
    # test that wants ONLY its own files answers with just those.
    monkeypatch.setattr(wordbits._paths, "data_dirs",
                        lambda name: [tmp_path])
    return wordbits


def test_lookup_finds_by_model_alias(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": ["411L-A"],
        "always_valid": ["", "NA"], "bits": ["LOP"], "patterns": [],
    })
    wb = _load(tmp_path, monkeypatch)
    assert wb.lookup("SEL-411L-A") is not None
    assert wb.lookup("SEL-411L") is not None


def test_unknown_model_has_no_set(tmp_path, monkeypatch):
    wb = _load(tmp_path, monkeypatch)
    assert wb.lookup("SEL-999") is None
    assert wb.lookup(None) is None


def test_check_accepts_a_listed_bit(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": ["", "NA"], "bits": ["LOP", "52A"], "patterns": [],
    })
    wb = _load(tmp_path, monkeypatch)
    s = wb.lookup("SEL-411L")
    assert s.check("LOP") == "ok"
    assert s.check("52A") == "ok"


def test_check_accepts_always_valid_placeholders(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": ["", "NA", "0", "1"], "bits": ["LOP"], "patterns": [],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("") == "ok"
    assert s.check("NA") == "ok"
    assert s.check("0") == "ok"


def test_check_accepts_a_pattern_match(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": [], "bits": [],
        "patterns": [{"re": "^PSV[0-9]{2}$", "label": "SELOGIC"}],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("PSV22") == "ok"
    assert s.check("PSV2") == "unknown"


def test_check_is_case_insensitive(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": [], "bits": ["LOP"], "patterns": [],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("lop") == "ok"


def test_check_rejects_an_unknown_bit(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", {
        "schema_version": 1, "model": "411L", "model_aliases": [],
        "always_valid": [], "bits": ["LOP"], "patterns": [],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("BANANA") == "unknown"


def test_always_valid_for_falls_back_to_the_default_placeholders_with_no_model():
    """With no wordbits file (``wbs is None``), the domain-independent
    placeholder markers must still apply -- losing them is exactly what
    turned 80-88% of every point in an unmodeled relay's DNP map into a
    false "duplicado".
    """
    assert wordbits.always_valid_for(None) == wordbits.DEFAULT_ALWAYS_VALID


def test_always_valid_for_unions_the_default_with_the_models_own():
    wbs = wordbits.WordbitSet(model="751", always_valid={"NA", "SPARE"})
    got = wordbits.always_valid_for(wbs)
    assert got == {"", "NA", "0", "1", "SPARE"}


def test_duplicates_ignores_placeholders():
    found = wordbits.duplicates(
        ["IN101", "NA", "NA", "IN101", "", "", "IN102"],
        always_valid={"", "NA"},
    )
    assert found == {"IN101"}


def test_duplicates_is_case_insensitive():
    assert wordbits.duplicates(["LOP", "lop"], always_valid=set()) == {"LOP"}


def test_the_shipped_files_load(monkeypatch):
    monkeypatch.setattr(wordbits, "_CACHE", None, raising=False)
    assert wordbits.lookup("SEL-411L-A") is not None
    assert wordbits.lookup("SEL-751") is not None


def test_one_malformed_file_does_not_disable_the_others(tmp_path, monkeypatch):
    """A syntactically valid JSON file of the wrong shape must be skipped,
    not let its exception escape and take every other model down with it.
    """
    _write(tmp_path, "SEL-751.json", {
        "schema_version": 1, "model": "751", "model_aliases": [],
        "always_valid": [], "bits": ["LOP"], "patterns": [],
    })
    # Top-level JSON that isn't an object.
    _write(tmp_path, "SEL-BAD-SHAPE.json", [1, 2, 3])
    # A "patterns" field that isn't a list of {"re": ...} objects.
    _write(tmp_path, "SEL-BAD-PATTERNS.json", {
        "schema_version": 1, "model": "BAD", "model_aliases": [],
        "always_valid": [], "bits": [], "patterns": "oops",
    })
    wb = _load(tmp_path, monkeypatch)
    s = wb.lookup("SEL-751")
    assert s is not None
    assert s.check("LOP") == "ok"


# ---------------------------------------------------------------------------
# Per-kind domains (schema 2)
# ---------------------------------------------------------------------------


def _v2(model="411L", **over):
    payload = {
        "schema_version": 2, "model": model, "model_aliases": [],
        "always_valid": ["", "NA", "0", "1"],
        "check_kinds": ["BI", "BO", "AO", "CO"],
        "kinds": {"BI": ["ENABLED"], "BO": ["RB01", "RB02"],
                  "AI": ["IA_MAG"], "AO": ["ACTGRP"], "CO": ["BKR1OPA"]},
        "bits": ["LOP", "PSV02"], "patterns": [],
    }
    payload.update(over)
    return payload


def test_names_in_splits_the_bo_close_open_pair():
    assert wordbits.names_in("BO", "RB03:RB03") == ["RB03", "RB03"]
    assert wordbits.names_in("BO", "OC:CC") == ["OC", "CC"]


def test_names_in_keeps_only_the_name_of_an_inline_scaled_analog():
    assert wordbits.names_in("AI", "IA_MAG:0.100:5") == ["IA_MAG"]
    assert wordbits.names_in("CO", "BKR1OPA:1:0") == ["BKR1OPA"]


def test_names_in_drops_purely_numeric_fragments():
    assert wordbits.names_in("BO", "RB01:0") == ["RB01"]
    assert wordbits.names_in("AI", "1.5") == []


def test_bi_and_bo_accept_relay_word_bits_but_ai_ao_co_do_not(tmp_path,
                                                              monkeypatch):
    """A Relay Word bit is a legal BI/BO value and NOT a legal analog or
    counter name; unioning it into those would only mask typos."""
    _write(tmp_path, "SEL-411L.json", _v2())
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("LOP", "BI") == "ok"
    assert s.check("LOP", "BO") == "ok"
    assert s.check("LOP", "AO") == "unknown"
    assert s.check("LOP", "CO") == "unknown"


def test_a_block_outside_check_kinds_never_warns(tmp_path, monkeypatch):
    """AI is deliberately not judged: the vendor's default point list is not
    the AI domain (math variables and fault quantities are absent)."""
    _write(tmp_path, "SEL-411L.json", _v2())
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert not s.validates("AI")
    assert s.check("LIXO_QUE_NAO_EXISTE", "AI") == "ok"


def test_a_bo_pair_warns_when_either_half_is_unknown(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", _v2())
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    assert s.check("RB01:RB02", "BO") == "ok"
    assert s.check("RB01:XYZZY", "BO") == "unknown"
    assert s.check("XYZZY:RB01", "BO") == "unknown"


def test_placeholders_stay_valid_in_every_block(tmp_path, monkeypatch):
    _write(tmp_path, "SEL-411L.json", _v2())
    s = _load(tmp_path, monkeypatch).lookup("SEL-411L")
    for kind in wordbits.KINDS:
        for placeholder in ("", "NA", "0", "1"):
            assert s.check(placeholder, kind) == "ok", (kind, placeholder)


def test_bi_is_not_judged_without_a_relay_word_list(tmp_path, monkeypatch):
    """A profile documents only the factory default BI map, so a file with
    no `bits` must leave BI alone rather than flag every mapped bit."""
    payload = _v2(bits=[], check_kinds=["BO", "AO", "CO"])
    _write(tmp_path, "SEL-421.json", dict(payload, model="421"))
    s = _load(tmp_path, monkeypatch).lookup("SEL-421")
    assert not s.validates("BI")
    assert s.check("IN306", "BI") == "ok"
    assert s.check("XYZZY", "AO") == "unknown"


def test_a_schema_1_file_still_judges_bi_only(tmp_path, monkeypatch):
    """Back-compat: a file with no `check_kinds` predates the per-kind lists
    and carried a Relay Word list, which is exactly BI's domain."""
    _write(tmp_path, "SEL-751.json", {
        "schema_version": 1, "model": "751", "model_aliases": [],
        "always_valid": [], "bits": ["LOP"], "patterns": [],
    })
    s = _load(tmp_path, monkeypatch).lookup("SEL-751")
    assert s.check_kinds == frozenset({"BI"})
    assert s.check("LOP", "BI") == "ok"
    assert s.check("XYZZY", "BI") == "unknown"
    assert s.check("XYZZY", "AO") == "ok"


def test_check_kinds_for_follows_the_measured_data():
    assert wordbits.check_kinds_for({"BO": {"RB01"}, "AO": {"ACTGRP"},
                                     "CO": {"BKR1OPA"}, "AI": {"IA_MAG"}},
                                    bits={"LOP"}) == ["BI", "BO", "AO", "CO"]
    # No Relay Word -> BI is not judged. AI is never judged.
    assert wordbits.check_kinds_for({"BO": {"RB01"}, "AI": {"IA_MAG"}},
                                    bits=set()) == ["BO"]
    assert wordbits.check_kinds_for({}, bits=set()) == []


class _FakeProfile:
    def __init__(self, models, kinds, name="p.zip"):
        self.models = models
        self.kinds = kinds
        self.source_name = name
        self.device_name = " ".join(models)
        self.document_version = "1"


def test_entry_from_profiles_never_discards_the_relay_word_half():
    existing = {"model": "411L", "bits": ["LOP", "PSV02"],
                "always_valid": ["", "NA", "SEM"],
                "patterns": [{"re": "^PSV[0-9]{2}$", "label": "psv"}],
                "source": {"relay_word": {"fid": "SEL-411L-A-R133"}}}
    entry = wordbits.entry_from_profiles(
        [_FakeProfile(["411L-2"], {"BO": {"RB01"}, "AO": {"ACTGRP"}})],
        existing)
    assert entry["bits"] == ["LOP", "PSV02"]
    assert entry["patterns"] == existing["patterns"]
    assert entry["always_valid"] == ["", "NA", "SEM"]
    assert entry["source"]["relay_word"] == {"fid": "SEL-411L-A-R133"}
    assert entry["check_kinds"] == ["BI", "BO", "AO"]


def test_entry_from_profiles_is_a_fixpoint():
    """Regenerating over its own output must not grow the file: the earlier
    `relay_word or source` fallback nested the whole previous source block
    inside itself once per run.
    """
    profs = [_FakeProfile(["411L-2"], {"BO": {"RB01"}})]
    first = wordbits.entry_from_profiles(profs, {})
    second = wordbits.entry_from_profiles(profs, first)
    assert second == first
    assert wordbits.entry_from_profiles(profs, second) == first


def test_entry_from_profiles_promotes_a_schema_1_source_once():
    existing = {"model": "751", "bits": ["LOP"],
                "source": {"fid": "SEL-751-R402", "harvested_at": "2026-08-22"}}
    entry = wordbits.entry_from_profiles(
        [_FakeProfile(["751"], {"BO": {"RB01"}})], existing)
    assert entry["source"]["relay_word"] == existing["source"]
    # ...and only once: a second pass must not re-wrap it.
    again = wordbits.entry_from_profiles(
        [_FakeProfile(["751"], {"BO": {"RB01"}})], entry)
    assert again["source"]["relay_word"] == existing["source"]


def test_merge_kinds_unions_instead_of_replacing():
    """The editor's import route sees one bundle at a time and must not
    narrow a file that already covers another option of the same model."""
    first = wordbits.entry_from_profiles(
        [_FakeProfile(["787"], {"AI": {"IA_MAG"}})], {})
    merged = wordbits.entry_from_profiles(
        [_FakeProfile(["787-4"], {"AI": {"VA_MAG"}})], first, merge_kinds=True)
    assert set(merged["kinds"]["AI"]) == {"IA_MAG", "VA_MAG"}
    # The offline generator rebuilds instead, so a dropped name disappears.
    rebuilt = wordbits.entry_from_profiles(
        [_FakeProfile(["787-4"], {"AI": {"VA_MAG"}})], first)
    assert set(rebuilt["kinds"]["AI"]) == {"VA_MAG"}


def test_the_shipped_files_declare_what_they_can_judge(monkeypatch):
    monkeypatch.setattr(wordbits, "_CACHE", None, raising=False)
    s = wordbits.lookup("SEL-411L-A")
    assert s.check_kinds == frozenset({"BI", "BO", "AO", "CO"})
    assert s.check("LOP", "BI") == "ok"          # a real Relay Word bit
    assert s.check("ACTGRP", "AO") == "ok"       # a real profile name
    assert s.check("XYZZY_NAO_EXISTE", "BI") == "unknown"
    # AI is off for every shipped model, by measurement.
    for model in ("SEL-411L-A", "SEL-751", "SEL-487E-3", "SEL-451-5"):
        assert "AI" not in wordbits.lookup(model).check_kinds


def test_invalidate_makes_a_new_file_visible(tmp_path, monkeypatch):
    """Importing a profile writes a file into a directory the registry has
    already cached; without invalidate() the model stays invisible until a
    restart and the page keeps saying validation is off."""
    wb = _load(tmp_path, monkeypatch)
    assert wb.lookup("SEL-421") is None
    _write(tmp_path, "SEL-421.json", _v2(model="421"))
    assert wb.lookup("SEL-421") is None          # still the stale cache
    wb.invalidate()
    assert wb.lookup("SEL-421") is not None
