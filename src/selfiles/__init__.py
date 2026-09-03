"""The file formats an SEL protective relay project is made of.

Everything here reads (and, where it must, rewrites) files that AcSELerator
QuickSet produces. Nothing here talks to a relay, opens a socket, or knows
what a web request is.

    import selfiles
    from selfiles.rdb import process_upload
    from selfiles.dnp_map import parse

    selfiles.configure(user_data_dir="~/.pacct/data", cache_dir="/var/cache/rdb")

What is inside:

``selfiles.rdb`` / ``selfiles.rdb_cache``
    Extract an RDB (an OLE compound database) into a content-addressed cache,
    and list the relays, models and addresses it holds.
``selfiles.settings``
    ``SET_*.TXT`` relay settings, tokenised faithfully.
``selfiles.dnp_map``
    ``SET_D<n>.TXT``, the DNP3 point map, with a byte-for-byte round-trip
    contract: ``parse(b).serialize() == b``. These bytes go back into a
    protection relay, which is why that contract is the module's whole point.
``selfiles.gle``
    QuickSet logic diagrams: parse, and render a page to SVG.
``selfiles.selogic``
    SELOGIC control equations: parse, compare by equivalence rather than text,
    and normalise a relay's settings into a comparable model.
``selfiles.models``
    Per-relay-model registries: block/bit conventions, and the Relay Word names
    a DNP map may legally use.
``selfiles.scl``
    IEC 61850 SCL/SCD: IEDs, GOOSE control blocks and VLANs, ExtRef
    subscriptions, functional constraints, and SEL's ``sAddr`` addressing.
    Vendor-neutral apart from the ``db:`` grammar, which is SEL's convention
    living inside a standard format.
``selfiles.match``
    Cross-match the relays in an RDB against the IEDs in an SCD.
``selfiles.dnp_profile``
    Read an SEL DNP3 device profile bundle.

Writing a Compound File back out is `cfbwrite`, a separate library.
"""

from __future__ import annotations

from selfiles._paths import (
    cache_dir,
    configure,
    data_dirs,
    user_data_dir,
    writable_data_dir,
)

__all__ = [
    "configure",
    "cache_dir",
    "data_dirs",
    "user_data_dir",
    "writable_data_dir",
]

__version__ = "1.0.0"
