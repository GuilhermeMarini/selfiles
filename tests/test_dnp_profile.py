"""SEL DNP3 Device Profile parsing."""

from __future__ import annotations

import io
import zipfile

import pytest

from selfiles import dnp_profile

_NS_2010 = "http://www.dnp3.org/DNP3/DeviceProfile/Jan2010"
_NS_2016 = "http://www.dnp3.org/DNP3/DeviceProfile/April2016"


def _doc(ns: str, device_name: str, version: str = "1", points: str = "") -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<DNP3DeviceProfileDocument xmlns="{ns}">
  <configuration><deviceConfig>
    <deviceName><currentValue><value>{device_name}</value></currentValue></deviceName>
    <documentVersionNumber><currentValue><value>{version}</value></currentValue>
    </documentVersionNumber>
  </deviceConfig></configuration>
  <dataPoints>{points}</dataPoints>
</DNP3DeviceProfileDocument>
""".encode()


_POINTS = """
  <binaryInput><index>0</index><name>ENABLED</name></binaryInput>
  <binaryInput><index>1</index><name>TRIP_LED</name></binaryInput>
  <binaryOutput><index>0</index><name>RB01</name></binaryOutput>
  <analogInput><index>0</index><name>IA_MAG</name></analogInput>
  <analogOutput><index>0</index><name>ACTGRP</name></analogOutput>
  <counter><index>0</index><name>BKR1OPA</name></counter>
"""


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_parses_the_jan2010_namespace():
    prof = dnp_profile.parse(_doc(_NS_2010, "SEL-751", points=_POINTS))
    assert prof.models == ["751"]
    assert prof.kinds["BI"] == {"ENABLED", "TRIP_LED"}
    assert prof.kinds["AO"] == {"ACTGRP"}
    assert prof.kinds["CO"] == {"BKR1OPA"}


def test_parses_the_april2016_namespace():
    """Both schema revisions appear in the real corpus and both must read."""
    prof = dnp_profile.parse(_doc(_NS_2016, '"SEL-411L-2 Relay"', points=_POINTS))
    assert prof.models == ["411L-2"]
    assert prof.kinds["BO"] == {"RB01"}


def test_device_name_prose_yields_every_model_it_names():
    prof = dnp_profile.parse(
        _doc(_NS_2016,
             '"SEL-411L-0 Relay", "SEL-411L-1 Relay", "SEL-411L-A Relay"',
             points=_POINTS))
    assert prof.models == ["411L-0", "411L-1", "411L-A"]
    # The base model answers too: a RELAYTYPE is often written without the
    # option digit.
    assert dnp_profile.model_keys(prof.models) == [
        "411L-0", "411L", "411L-1", "411L-A"]


def test_names_are_upper_cased():
    prof = dnp_profile.parse(_doc(
        _NS_2010, "SEL-751",
        points="<binaryInput><index>0</index><name>lop</name></binaryInput>"))
    assert prof.kinds["BI"] == {"LOP"}


def test_reads_the_document_out_of_a_zip_bundle():
    data = _zip({
        "DNP_ReadMe.txt": "instructions",
        "dnp_logo.jpg": b"\xff\xd8",
        "DNP3DeviceProfileJan2010.xsd": "<schema/>",
        "DNP3DeviceProfileJan2010 SEL.xslt": "<xsl/>",
        "dnpDP.xml": _doc(_NS_2010, "SEL-751", points=_POINTS),
    })
    prof = dnp_profile.parse(data, "SEL-751_dnpDP.zip")
    assert prof.models == ["751"]
    assert prof.source_name == "SEL-751_dnpDP.zip"


def test_zip_with_the_document_under_a_folder_and_an_odd_name():
    """The 487E bundle ships `SEL-487E dnp/dnp_487e.xml`, not `dnpDP.xml`."""
    data = _zip({
        "SEL-487E dnp/DNP3DeviceProfileJan2010.xsd": "<schema/>",
        "SEL-487E dnp/DNP3DeviceProfileJan2010 SEL.xslt": "<xsl/>",
        "SEL-487E dnp/dnp_487e.xml": _doc(_NS_2010, "SEL-487E-3, -4 Relay",
                                          points=_POINTS),
    })
    prof = dnp_profile.parse(data)
    assert prof.models == ["487E-3"]


def test_a_zip_without_any_profile_xml_is_rejected():
    with pytest.raises(dnp_profile.DnpProfileError):
        dnp_profile.parse(_zip({"dnp_logo.jpg": b"\xff\xd8"}))


def test_a_non_zip_non_xml_payload_is_rejected():
    with pytest.raises(dnp_profile.DnpProfileError):
        dnp_profile.parse(b"nao sou um perfil")


def test_the_wrong_xml_root_is_rejected():
    with pytest.raises(dnp_profile.DnpProfileError):
        dnp_profile.parse(b"<SCL><Header/></SCL>")


def test_a_profile_with_no_points_is_rejected():
    """An empty point list would install a file that silently validates
    nothing, which is worse than refusing the import."""
    with pytest.raises(dnp_profile.DnpProfileError):
        dnp_profile.parse(_doc(_NS_2010, "SEL-751"))


def test_a_profile_whose_device_name_names_no_model_is_rejected():
    with pytest.raises(dnp_profile.DnpProfileError):
        dnp_profile.parse(_doc(_NS_2010, "Relé genérico", points=_POINTS))
