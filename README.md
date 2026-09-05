# SELlib

Read and write the file formats an SEL protective relay project is made of —
the ones AcSELerator QuickSet produces.

Nothing here talks to a relay, opens a socket, or knows what a web request is.

> **Renamed.** This library was published as `selfiles` for 1.x. From 2.0.0 it
> is `SELlib`, and the import moved with it — `import sellib`, not
> `import selfiles`. Nothing else changed in 2.0.0; the major is the rename.
> The `selfiles` distribution has been removed from PyPI, so there is no
> version of it left to install: `import selfiles` does not resolve to
> anything, and a requirements file naming it will fail rather than
> quietly install something older.

```python
import sellib
from sellib.rdb import process_upload
from sellib.dnp_map import parse, discover

sellib.configure(user_data_dir="~/.pacct/data", cache_dir="/var/cache/rdb")

info = process_upload(open("substation.rdb", "rb").read(), "substation.rdb")
for relay in info.relays:
    print(relay.name, relay.model, relay.ip)
```

| module | what it reads |
|---|---|
| `sellib.rdb`, `.rdb_cache` | the RDB (an OLE compound database): relays, models, addresses, extracted into a content-addressed cache |
| `sellib.settings` | `SET_*.TXT` relay settings |
| `sellib.dnp_map` | `SET_D<n>.TXT`, the DNP3 point map |
| `sellib.gle` | QuickSet logic diagrams — parse, and render a page to SVG |
| `sellib.selogic` | SELOGIC equations: parse, compare by equivalence, normalise settings |
| `sellib.models` | per-model registries: block conventions, and valid Relay Word names |
| `sellib.scl` | IEC 61850 SCL/SCD: IEDs, GOOSE, VLANs, ExtRefs, functional constraints, SEL `sAddr` |
| `sellib.match` | cross-match an RDB's relays against an SCD's IEDs |
| `sellib.dnp_profile` | SEL DNP3 device profile bundles |

## The one contract to know about

`sellib.dnp_map` guarantees `parse(b).serialize() == b`, byte for byte, for
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
pip install sellib
```

Python 3.10+. Extracted from
[PAC CT](https://github.com/GuilhermeMarini/pac-ct), where all of it is
exercised against a real substation corpus.

## Licence

AGPL-3.0-or-later — see [LICENSE](LICENSE).
