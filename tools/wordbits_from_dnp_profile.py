#!/usr/bin/env python3
"""Build data/wordbits/<MODEL>.json from SEL DNP3 Device Profile bundles.

The profile is the vendor's own statement of what a model names in each DNP
block, so it is the authority for the AO, CO and (with the Relay Word) BO
domains. It is *not* the authority for BI: a BI point maps to any Relay Word
bit and the profile lists only the factory default map, so ``bits`` -- seeded
separately by ``tools/wordbits_from_glv_cache.py`` -- stays what BI is judged
against. See ``sellib/models/wordbits.py`` for the measured numbers.

    python3 tools/wordbits_from_dnp_profile.py docs/*.zip
    python3 tools/wordbits_from_dnp_profile.py --check docs/*.zip

Merging is the default: an existing file's ``bits``, ``patterns`` and
``always_valid`` survive untouched and only the profile-derived parts are
rewritten, so a re-import never discards a Relay Word harvest or a hand edit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sellib import dnp_profile  # noqa: E402
from sellib._paths import PACKAGE_DATA  # noqa: E402
from sellib.models.wordbits import (  # noqa: E402
    KINDS,
    base_model,
    entry_from_profiles,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profiles", nargs="+", type=Path,
                    help="SEL DNP device profile zips (or bare dnpDP.xml)")
    ap.add_argument("--out", type=Path, default=PACKAGE_DATA / "wordbits")
    ap.add_argument("--check", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args()

    by_model: dict[str, list] = {}
    for path in args.profiles:
        try:
            prof = dnp_profile.parse_path(path)
        except dnp_profile.DnpProfileError as e:
            print(f"  !! {path.name}: {e}")
            continue
        base = base_model(prof)
        by_model.setdefault(base, []).append(prof)
        print(f"  {path.name:24s} -> {base:8s} "
              + " ".join(f"{k}={len(prof.kinds[k])}" for k in KINDS))

    if not by_model:
        print("nenhum perfil válido.")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    for model, profs in sorted(by_model.items()):
        dest = args.out / f"SEL-{model}.json"
        existing = {}
        if dest.is_file():
            try:
                existing = json.loads(dest.read_text(encoding="utf-8"))
            except ValueError:
                print(f"  !! {dest.name} ilegível; será sobrescrito")
        entry = entry_from_profiles(profs, existing)
        note = (f"bits={len(entry['bits'])} "
                f"check={','.join(entry['check_kinds']) or '-'}")
        if args.check:
            print(f"  = {dest.name:20s} {note} (não gravado)")
            continue
        dest.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"  > {dest.name:20s} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
