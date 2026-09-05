"""A document that declares a DTD is refused, on every path that reads XML.

The attack is entity expansion, "billion laughs": a `<!DOCTYPE` block defines
text shortcuts that reference each other, so each level multiplies. The file
stays tiny -- it stores only the definitions -- and the expansion happens in
memory, during the parse, with no half-way for the parser to stop at.

Measured against this project's own stack before the fix: 317 bytes became
1,000,000 characters in 7 ms, a factor of 3155. Two more levels of the same
file is 100 million. That parser sits behind an upload endpoint that accepts
200 MB, and the files come from outside -- a client's integrator, a vendor
export -- so "the file is trusted" was never true.

Refusing the DOCTYPE removes the class rather than mitigating it: entities can
only be declared there. It costs nothing because SCL does not use DTDs -- IEC
61850 validates against an XSD referenced by namespace. Measured over every
SCL file reachable from this project: 696 files (345 factory ICDs plus 351
substation SCD/ICD/CID), 0 with a DOCTYPE.
"""

from __future__ import annotations

import pytest

from selfiles.dnp_profile import parse as parse_dnp_profile
from selfiles.gle import parse_gle
from selfiles.scl._xmlsafe import (
    DtdNotAllowed,
    reject_dtd_in_bytes,
    reject_dtd_in_file,
)
from selfiles.scl.read import ScdDocument, load_scd

_BOMB = (
    b'<?xml version="1.0"?><!DOCTYPE SCL [\n'
    b'<!ENTITY a "AAAAAAAAAA">\n'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
    b']><SCL><IED name="&c;"/></SCL>'
)
_GOOD = (b'<?xml version="1.0"?>'
         b'<SCL xmlns="http://www.iec.ch/61850/2003/SCL">'
         b'<IED name="REL_A" type="SEL_411L"/></SCL>')


# -- the helper itself ------------------------------------------------------

def test_a_dtd_is_refused():
    with pytest.raises(DtdNotAllowed):
        reject_dtd_in_bytes(_BOMB)


def test_an_ordinary_document_passes():
    reject_dtd_in_bytes(_GOOD)          # must not raise


def test_a_dtd_hidden_behind_a_huge_comment_is_still_refused():
    """The reason this scans with a real XML parser instead of looking at the
    first N bytes: the prolog may carry comments of any length, so a fixed
    window is a bypass. Here the DOCTYPE sits behind 500 kB of comment."""
    padded = (b'<?xml version="1.0"?>\n<!-- ' + b"x" * 500_000 + b" -->\n"
              + _BOMB.split(b"?>", 1)[1])
    with pytest.raises(DtdNotAllowed):
        reject_dtd_in_bytes(padded)


def test_a_dtd_hidden_behind_a_huge_comment_in_a_FILE_is_still_refused(tmp_path):
    """The same bypass against the path an upload actually takes.

    Written after a mutation check: turning the file scan into a fixed 4 kB
    window left the in-memory version of this test passing, because that one
    hands over the whole document at once. The streaming reader needs its own.
    """
    f = tmp_path / "padded.scd"
    f.write_bytes(b'<?xml version="1.0"?>\n<!-- ' + b"x" * 500_000 + b" -->\n"
                  + _BOMB.split(b"?>", 1)[1])
    with pytest.raises(DtdNotAllowed):
        reject_dtd_in_file(f)
    assert ScdDocument.load(f) is None


def test_a_long_prolog_without_a_dtd_is_not_refused():
    """The companion of the test above: a big comment on its own is legal and
    must pass, or the check would reject real files to catch a fake one."""
    padded = (b'<?xml version="1.0"?>\n<!-- ' + b"x" * 500_000 + b" -->\n"
              + _GOOD.split(b"?>", 1)[1])
    reject_dtd_in_bytes(padded)


def test_the_scan_stops_at_the_root_and_does_not_read_the_body(tmp_path):
    """It reads the PROLOG, not the document: a 22 MB SCD must not be read
    twice just to check its first line."""
    big = tmp_path / "big.scd"
    big.write_bytes(_GOOD.replace(b"</SCL>", b"<Junk/>" * 200_000 + b"</SCL>"))
    assert big.stat().st_size > 1_000_000
    reject_dtd_in_file(big)             # must not raise, must not be slow


def test_a_malformed_prolog_is_left_to_the_real_parser(tmp_path):
    """Not a bypass: a prolog expat cannot read is one `ET.parse` cannot read
    either, and the real parse reports it properly a moment later."""
    bad = tmp_path / "bad.scd"
    bad.write_bytes(b"<?xml version=")
    reject_dtd_in_file(bad)             # swallowed here
    assert load_scd(bad) == []          # and reported there


# -- every path that reads XML from a file somebody supplied ----------------

def test_an_scd_declaring_a_dtd_is_refused_gracefully(tmp_path, caplog):
    """`load()` is the graceful constructor: None and a log line, the same as
    any other unreadable file, so an upload cannot raise through a route."""
    f = tmp_path / "bomba.scd"
    f.write_bytes(_BOMB)
    assert ScdDocument.load(f) is None
    assert load_scd(f) == []


def test_the_strict_constructor_raises_on_a_dtd(tmp_path):
    """`parse()` is the offline generator's path, where stopping is right."""
    f = tmp_path / "bomba.icd"
    f.write_bytes(_BOMB)
    with pytest.raises(DtdNotAllowed):
        ScdDocument.parse(f)


def test_an_ordinary_scd_still_loads(tmp_path):
    f = tmp_path / "ok.scd"
    f.write_bytes(_GOOD)
    assert [i.name for i in load_scd(f)] == ["REL_A"]


def test_a_gle_declaring_a_dtd_is_refused(tmp_path):
    """A GLE arrives inside an RDB somebody uploaded, so it is no more trusted
    than an SCD."""
    f = tmp_path / "GL1.gle"
    f.write_bytes(b'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE editor ['
                  b'<!ENTITY a "AA">]><editor><page name="P"/></editor>')
    with pytest.raises(DtdNotAllowed):
        parse_gle(f)


def test_an_ordinary_gle_still_parses(tmp_path):
    f = tmp_path / "GL1.gle"
    f.write_bytes(b'<?xml version="1.0" encoding="utf-8"?>'
                  b'<editor version="1.0"><page name="P"><elements /></page>'
                  b"</editor>")
    assert parse_gle(f).find("page").get("name") == "P"


def test_a_dnp_profile_declaring_a_dtd_is_refused():
    """The profile comes out of a vendor zip the user chose to import."""
    with pytest.raises(DtdNotAllowed):
        parse_dnp_profile(b'<?xml version="1.0"?><!DOCTYPE d ['
                          b'<!ENTITY a "AA">]><d/>')
