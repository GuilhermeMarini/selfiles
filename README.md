# selfiles

Read and write the file formats an SEL protective relay project is made of —
the ones AcSELerator QuickSet produces.

- **RDB** — OLE compound database: extract every relay, its settings and its
  logic diagrams; a content-addressed extraction cache.
- **`SET_*.TXT`** — relay settings, tokenised faithfully.
- **`SET_D<n>.TXT`** — the DNP3 point map, with a byte-for-byte round-trip
  contract: `parse(b).serialize() == b`. These bytes go back into a protection
  relay.
- **GLE** — QuickSet logic diagrams: parse, and render a page to SVG.
- **SELOGIC** — parse and compare control equations by equivalence, not text.
- **SCL / IEC 61850** (`selfiles.scl`) — IEDs, GOOSE control blocks and VLANs,
  ExtRef subscriptions, functional constraints from `DataTypeTemplates`, and
  SEL's `sAddr` Relay Word addressing.
- **Model registries** — per-relay-model profiles, valid Relay Word names, and
  bit → MMS item tables.

Extracted from [PAC CT](https://github.com/GuilhermeMarini/pac-ct), where all
of it is exercised against a real substation corpus.

> **Status: scaffold.** Code lands here per `docs/MIGRATION.md` §4.2 of PAC CT.

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).
