"""Round-trip of SET_D*.TXT parser."""

from __future__ import annotations

import pytest

from sellib import dnp_map as set_dnp

# Minimal SET_D from 411L: CRLF in header, 0x1C + CRLF in data lines,
# index without padding, empty slot as "".
SAMPLE_411L = (
    b"[INFO]\r\n"
    b"RELAYTYPE=SEL-411L-A\r\n"
    b"FID=SEL-411L-A-RXXX-VX-Z022004-DXXXXXXXX\r\n"
    b"BFID=SLBT-4XX-R300-V0-Z001002-D20200229\r\n"
    b"PARTNO=0411LAX6X5C7DDXH5D474XX\r\n"
    b"[D1]\r\n"
    b'MINDIST,"1.0"\x1c\r\n'
    b'MAXDIST,"10000.0"\x1c\r\n'
    b'BI_1,"PSV22"\x1c\r\n'
    b'BI_2,""\x1c\r\n'
    b'AI_1,"IAMAG"\x1c\r\n'
    b'AI_SCA1,"1.0"\x1c\r\n'
    b'AI_DBD1,"0.5"\x1c\r\n'
    b'CO_1,""\x1c\r\n'
    b'CO_DBD1,""\x1c\r\n'
)

# SET_D from 751: index with two-digit padding, empty slot as "NA".
SAMPLE_751 = (
    b"[INFO]\r\n"
    b"RELAYTYPE=SEL-751\r\n"
    b"FID=SEL-751-RXXX-VX-Z102100-DXXXXXXXX\r\n"
    b"BFID=SLBT7XX-RXXX-VX-Z000000-DXXXXXXXX\r\n"
    b"PARTNO=751401A4A3A2A85AG30\r\n"
    b"[D1]\r\n"
    b'BO_00,"89OC2P1"\x1c\r\n'
    b'BO_01,"NA"\x1c\r\n'
    b'AI_00,"NA"\x1c\r\n'
)


@pytest.mark.parametrize("sample", [SAMPLE_411L, SAMPLE_751])
def test_roundtrip_is_byte_identical(sample):
    assert set_dnp.parse(sample).serialize() == sample


def test_parses_info_header():
    f = set_dnp.parse(SAMPLE_411L)
    assert f.info["RELAYTYPE"] == "SEL-411L-A"
    assert f.info["PARTNO"] == "0411LAX6X5C7DDXH5D474XX"


def test_parses_section_name():
    assert set_dnp.parse(SAMPLE_411L).section == "D1"
    assert set_dnp.parse(SAMPLE_751).section == "D1"


def test_keeps_the_file_separator_out_of_the_value():
    f = set_dnp.parse(SAMPLE_411L)
    line = next(line for line in f.lines if line.key == "BI_1")
    assert line.value == "PSV22"
    assert line.terminator == b"\x1c\r\n"


def test_header_lines_have_a_plain_crlf_terminator():
    f = set_dnp.parse(SAMPLE_411L)
    line = next(line for line in f.lines if line.raw == b"[INFO]")
    assert line.terminator == b"\r\n"


def test_empty_value_stays_quoted_and_empty():
    f = set_dnp.parse(SAMPLE_411L)
    line = next(line for line in f.lines if line.key == "BI_2")
    assert line.value == ""
    assert line.quoted is True


def test_serialize_reflects_an_edited_value():
    f = set_dnp.parse(SAMPLE_411L)
    f.set_value("BI_2", "IN205")
    assert b'BI_2,"IN205"\x1c\r\n' in f.serialize()
    # The rest of the file was untouched.
    assert b'BI_1,"PSV22"\x1c\r\n' in f.serialize()


def test_set_value_on_an_unknown_key_raises():
    f = set_dnp.parse(SAMPLE_411L)
    with pytest.raises(KeyError):
        f.set_value("BI_999", "IN205")


def test_index_padding_is_preserved_on_edit():
    f = set_dnp.parse(SAMPLE_751)
    f.set_value("BO_01", "52A")
    assert b'BO_01,"52A"\x1c\r\n' in f.serialize()


def test_a_file_with_lf_only_terminators_still_roundtrips():
    sample = SAMPLE_411L.replace(b"\r\n", b"\n")
    assert set_dnp.parse(sample).serialize() == sample


def test_points_carry_kind_and_index():
    pts = set_dnp.parse(SAMPLE_411L).points()
    bi1 = next(p for p in pts if p.key == "BI_1")
    assert (bi1.kind, bi1.index, bi1.value) == ("BI", 1, "PSV22")


def test_padded_index_is_parsed_as_a_number_but_key_is_kept():
    pts = set_dnp.parse(SAMPLE_751).points()
    bo1 = next(p for p in pts if p.index == 1 and p.kind == "BO")
    assert bo1.key == "BO_01"
    assert bo1.value == "NA"


def test_ai_sca_and_dbd_fold_into_the_ai_point():
    pts = set_dnp.parse(SAMPLE_411L).points()
    ai1 = next(p for p in pts if p.kind == "AI" and p.index == 1)
    assert ai1.value == "IAMAG"
    assert (ai1.sca_key, ai1.sca) == ("AI_SCA1", "1.0")
    assert (ai1.dbd_key, ai1.dbd) == ("AI_DBD1", "0.5")
    # And they do not appear as their own points.
    assert not any(p.key.startswith("AI_SCA") for p in pts)
    assert not any(p.key.startswith("AI_DBD") for p in pts)


def test_co_dbd_folds_into_the_co_point():
    ptss = set_dnp.parse(SAMPLE_411L).points()
    co1 = next(p for p in ptss if p.kind == "CO" and p.index == 1)
    assert (co1.dbd_key, co1.dbd) == ("CO_DBD1", "")
    assert not any(p.key.startswith("CO_DBD") for p in ptss)


def test_blocks_only_contains_what_the_file_has():
    blocks = set_dnp.parse(SAMPLE_751).blocks()
    assert set(blocks) == {"BO", "AI"}
    assert [p.key for p in blocks["BO"]] == ["BO_00", "BO_01"]


def test_mindist_and_maxdist_are_extras_not_points():
    f = set_dnp.parse(SAMPLE_411L)
    assert dict(f.extras()) == {"MINDIST": "1.0", "MAXDIST": "10000.0"}
    assert not any(p.key in ("MINDIST", "MAXDIST") for p in f.points())


def test_a_file_without_extras_reports_none():
    assert set_dnp.parse(SAMPLE_751).extras() == []


def test_invalid_modifier_appears_in_extras_not_points():
    """BO_SCA1 is not a valid modifier; it appears in extras, not as a point column."""
    sample = (
        b"[INFO]\r\n"
        b"RELAYTYPE=SEL-411L-A\r\n"
        b"[D1]\r\n"
        b'BO_1,"OUT1"\x1c\r\n'
        b'BO_SCA1,"INVALID"\x1c\r\n'
    )
    f = set_dnp.parse(sample)
    pts = f.points()
    bo1 = next(p for p in pts if p.kind == "BO" and p.index == 1)
    # BO_SCA1 is invalid, so it does not fold into BO_1.
    assert bo1.sca_key is None
    assert bo1.sca is None
    # It appears in extras instead.
    extras_dict = dict(f.extras())
    assert "BO_SCA1" in extras_dict
    assert extras_dict["BO_SCA1"] == "INVALID"
    # Round-trip contract holds.
    assert f.serialize() == sample


def test_orphaned_modifier_appears_in_extras():
    """AI_SCA5 without AI_5 appears in extras, not as a point."""
    sample = (
        b"[INFO]\r\n"
        b"RELAYTYPE=SEL-411L-A\r\n"
        b"[D1]\r\n"
        b'AI_SCA5,"1.5"\x1c\r\n'
    )
    f = set_dnp.parse(sample)
    # No AI_5 point exists.
    assert not any(p.kind == "AI" and p.index == 5 for p in f.points())
    # AI_SCA5 appears in extras.
    extras_dict = dict(f.extras())
    assert "AI_SCA5" in extras_dict
    assert extras_dict["AI_SCA5"] == "1.5"
    # Round-trip contract holds.
    assert f.serialize() == sample


def test_index_of_empty_key_raises_instead_of_addressing_the_header():
    """A falsy key must never fall through to ``line.key == key`` and match
    ``RawLine``'s own default -- that would address whichever unparsed line
    (typically ``[INFO]``) comes first, and ``set_value("", ...)`` would
    silently corrupt the header instead of raising like every other unknown
    key.
    """
    f = set_dnp.parse(SAMPLE_411L)
    with pytest.raises(KeyError):
        f.index_of("")
    with pytest.raises(KeyError):
        f.set_value("", "PWNED")
    # And the header line is provably untouched.
    assert f.serialize().startswith(b"[INFO]\r\n")


def test_point_keys_excludes_extras():
    f = set_dnp.parse(SAMPLE_411L)
    keys = f.point_keys()
    assert {"BI_1", "BI_2", "AI_1", "AI_SCA1", "AI_DBD1",
            "CO_1", "CO_DBD1"} <= keys
    assert "MINDIST" not in keys
    assert "MAXDIST" not in keys


def test_check_value_accepts_an_ordinary_string():
    assert set_dnp.check_value("PSV22") is None
    assert set_dnp.check_value("") is None


def test_check_value_rejects_non_latin1():
    # An en dash, routine when pasted from a spec document: valid Unicode,
    # not representable in Latin-1 -- what `RawLine.emit` encodes with.
    assert set_dnp.check_value("IN101–spare") is not None


def test_check_value_rejects_embedded_terminator_bytes():
    # A crafted value that would inject an extra physical line: 0x1C is the
    # field terminator, CR/LF would start a new physical line either way.
    assert set_dnp.check_value('A\r\nBI_01,"HACK"') is not None
    assert set_dnp.check_value("A\x1cB") is not None


def test_well_formed_folding_still_works():
    """Re-assert that well-formed AI_SCA/DBD and CO_DBD still fold correctly."""
    f = set_dnp.parse(SAMPLE_411L)
    pts = f.points()
    ai1 = next(p for p in pts if p.kind == "AI" and p.index == 1)
    assert ai1.sca_key == "AI_SCA1"
    assert ai1.sca == "1.0"
    assert ai1.dbd_key == "AI_DBD1"
    assert ai1.dbd == "0.5"
    co1 = next(p for p in pts if p.kind == "CO" and p.index == 1)
    assert co1.dbd_key == "CO_DBD1"
    assert co1.dbd == ""


class TestSameModel:
    """The gate on copying one relay's DNP map onto another's."""

    def test_the_same_relaytype_matches(self):
        assert set_dnp.same_model("SEL-411L-A", "SEL-411L-A")

    def test_blanks_and_case_do_not_count(self):
        assert set_dnp.same_model(" sel-411l-a ", "SEL-411L-A")

    def test_an_option_suffix_is_a_different_model(self):
        # The suffix is what changes the I/O board, and with it how many
        # BI/BO points the SET_D even has.
        assert not set_dnp.same_model("SEL-411L-A", "SEL-411L-B")
        assert not set_dnp.same_model("SEL-411L-A", "SEL-411L")

    def test_an_unknown_model_matches_nothing_including_itself(self):
        assert not set_dnp.same_model(None, None)
        assert not set_dnp.same_model("", "")
        assert not set_dnp.same_model("   ", "SEL-751")
        assert not set_dnp.same_model("SEL-751", None)
