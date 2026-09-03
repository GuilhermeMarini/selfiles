"""Discovery of relays with DNP maps inside an already-extracted RDB."""

from __future__ import annotations

from tests.test_set_dnp import SAMPLE_751

from selfiles import dnp_map as set_dnp


def _make_extraction(tmp_path):
    """Build a fake RDB extraction, laid out like the real one."""
    relays = tmp_path / "Relays"

    # Relay with GLE and three sessions, two of them identical (same map, different headers).
    a = relays / "QPC1_LT1_UPC1"
    (a / "Misc").mkdir(parents=True)
    (a / "Misc" / "GL1.gle").write_bytes(b"<xml/>")

    # D1 and D2 have identical maps but different [D1] and [D2] headers.
    # Extract INFO and data lines, then rebuild with correct section header.
    d1_content = (
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
    d2_content = d1_content.replace(b"[D1]\r\n", b"[D2]\r\n")
    d3_content = (
        b"[INFO]\r\n"
        b"RELAYTYPE=SEL-411L-A\r\n"
        b"FID=SEL-411L-A-RXXX-VX-Z022004-DXXXXXXXX\r\n"
        b"BFID=SLBT-4XX-R300-V0-Z001002-D20200229\r\n"
        b"PARTNO=0411LAX6X5C7DDXH5D474XX\r\n"
        b"[D3]\r\n"
        b'MINDIST,"1.0"\x1c\r\n'
        b'MAXDIST,"10000.0"\x1c\r\n'
        b'BI_1,"PSV23"\x1c\r\n'
        b'BI_2,""\x1c\r\n'
        b'AI_1,"IAMAG"\x1c\r\n'
        b'AI_SCA1,"1.0"\x1c\r\n'
        b'AI_DBD1,"0.5"\x1c\r\n'
        b'CO_1,""\x1c\r\n'
        b'CO_DBD1,""\x1c\r\n'
    )

    (a / "SET_D1.TXT").write_bytes(d1_content)
    (a / "SET_D2.TXT").write_bytes(d2_content)
    (a / "SET_D3.TXT").write_bytes(d3_content)
    (a / "SET_G.TXT").write_bytes(b"[INFO]\r\n")

    # Relay without any GLE -- the case where RdbInfo.relays loses it.
    b = relays / "SEL-2440 008 to 002"
    (b / "Misc").mkdir(parents=True)
    (b / "set_D1.txt").write_bytes(SAMPLE_751)

    # Relay with no DNP map: should not appear.
    c = relays / "TR1-2414"
    (c / "Misc").mkdir(parents=True)
    (c / "set_G.txt").write_bytes(b"[INFO]\r\n")

    return tmp_path


def test_finds_relays_that_have_a_dnp_map(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    assert [r.name for r in found] == ["QPC1_LT1_UPC1", "SEL-2440 008 to 002"]


def test_finds_a_relay_with_no_gle_at_all(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    r = next(r for r in found if r.name == "SEL-2440 008 to 002")
    assert [s.name for s in r.sessions] == ["D1"]


def test_sessions_are_sorted_and_named_by_their_section(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    r = next(r for r in found if r.name == "QPC1_LT1_UPC1")
    assert [s.name for s in r.sessions] == ["D1", "D2", "D3"]


def test_stream_parts_mirror_the_ole_path_with_original_case(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    a = next(r for r in found if r.name == "QPC1_LT1_UPC1")
    assert a.sessions[0].stream_parts == ("Relays", "QPC1_LT1_UPC1", "SET_D1.TXT")
    b = next(r for r in found if r.name == "SEL-2440 008 to 002")
    assert b.sessions[0].stream_parts == ("Relays", "SEL-2440 008 to 002", "set_D1.txt")


def test_relaytype_comes_from_the_info_header(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    assert found[0].relaytype == "SEL-411L-A"
    assert found[1].relaytype == "SEL-751"


def test_identical_sessions_are_grouped(tmp_path):
    found = set_dnp.discover(_make_extraction(tmp_path))
    a = next(r for r in found if r.name == "QPC1_LT1_UPC1")
    assert set_dnp.identical_groups(a) == [["D1", "D2"], ["D3"]]


def test_a_missing_extraction_yields_nothing(tmp_path):
    assert set_dnp.discover(tmp_path / "nao-existe") == []
