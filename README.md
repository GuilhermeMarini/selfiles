# selfiles

Read and write the file formats an SEL protective relay project is made of —
the ones AcSELerator QuickSet produces.

Nothing here talks to a relay, opens a socket, or knows what a web request is.

```python
import selfiles
from selfiles.rdb import process_upload
from selfiles.dnp_map import parse, discover

selfiles.configure(user_data_dir="~/.pacct/data", cache_dir="/var/cache/rdb")

info = process_upload(open("substation.rdb", "rb").read(), "substation.rdb")
for relay in info.relays:
    print(relay.name, relay.model, relay.ip)
```

| module | what it reads |
|---|---|
| `selfiles.rdb`, `.rdb_cache` | the RDB (an OLE compound database): relays, models, addresses, extracted into a content-addressed cache |
| `selfiles.settings` | `SET_*.TXT` relay settings |
| `selfiles.dnp_map` | `SET_D<n>.TXT`, the DNP3 point map |
| `selfiles.gle` | QuickSet logic diagrams — parse, and render a page to SVG |
| `selfiles.selogic` | SELOGIC equations: parse, compare by equivalence, normalise settings |
| `selfiles.models` | per-model registries: block conventions, and valid Relay Word names |
| `selfiles.scl` | IEC 61850 SCL/SCD: IEDs, GOOSE, VLANs, ExtRefs, functional constraints, SEL `sAddr` |
| `selfiles.match` | cross-match an RDB's relays against an SCD's IEDs |
| `selfiles.dnp_profile` | SEL DNP3 device profile bundles |

## The one contract to know about

`selfiles.dnp_map` guarantees `parse(b).serialize() == b`, byte for byte, for
every `SET_D` in the reference corpus. These bytes go back into a protection
relay: a settings file that round-trips imperfectly is a relay that behaves in
a way nobody asked for. The `0x1C` field separator lives *inside* the line,
before the CRLF, and is preserved literally, along with each model's own
index padding (`BI_1` on a 411L, `BI_00` on a 751).

Writing a Compound File back out — needed whenever an edit grows a stream — is
[`cfbwrite`](https://github.com/GuilhermeMarini/cfbwrite), a separate library.

## Data

The per-model registries ship with the package. `configure(user_data_dir=...)`
adds an overlay searched **first**, per model, so a host can add a relay model
at runtime without shipping a new release and without repeating the rest.

## Install

```bash
pip install selfiles
```

Python 3.10+. Extracted from
[PAC CT](https://github.com/GuilhermeMarini/pac-ct), where all of it is
exercised against a real substation corpus.

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).
