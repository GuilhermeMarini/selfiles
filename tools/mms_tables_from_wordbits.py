#!/usr/bin/env python3
"""Split the ICD-derived map into the per-part tables the GLV ships.

    python3 tools/mms_tables_from_wordbits.py            # corpus parts only
    python3 tools/mms_tables_from_wordbits.py --all      # every part (9.0 MB)

Source is `fixtures/ICD files/SEL/wordbits.json`, which is NOT in git (231 MB
of vendor ICDs behind it). The output IS in git, because the app needs it at
runtime and a clean clone has no ICDs.

TWO sources, and the second one exists because the first cannot reach it.
`wordbits.json` is built by ANOTHER project (`61850Stack/commissioning-app`),
whose own `sAddr` walk keeps only `db:NOME` and drops the DECORATED form --
`db:52A|52B?0:1:2:3`, one 61850 point carrying two Relay Word bits. Fixing
this repository's parser does not reach that file, so the decorated half is
read straight out of the ICD named by each model entry's own `path`: the same
file the plain rows came from, so no revision is ever mixed. `wordbits.json`
is never rewritten, and the plain rows come out identical.

A decorated row is `[ld_suffix, item, [alternativas, indice, nbits]]`. The
third element is optional -- the tables already published keep two -- and a
plain row always wins over a decorated one for the same bit, which is the same
rule the live SCD path applies in `mms_tables.da_rank`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from selfiles._paths import PACKAGE_DATA  # noqa: E402
from selfiles.scl.mms_tables import fc_rank as _fc_rank  # noqa: E402
from selfiles.scl.read import sel_da_fcs, sel_short_addresses  # noqa: E402

# The factory ICD corpus is not in any repository (231 MB of vendor
# files); point SELFILES_ICD_FIXTURES at a local copy.
FIXTURES_DIR = Path(os.environ.get("SELFILES_ICD_FIXTURES", "fixtures"))
MMS_MAP_OUT = PACKAGE_DATA / "mms_map"  # noqa: E402

CORPUS = {"411L", "451", "487E", "311C1", "751", "2414", "2440"}

# FC_PREFERENCE and its rank helper now live in selfiles/scl/mms_tables.py --
# the library side of the same data -- so this generator and
# PAC CT's live-SCD resolver import ONE definition
# instead of keeping two copies that can drift apart silently.


def norm(part: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (part or "").upper())


def _fc(item: str) -> str:
    """The functional constraint is the second `$`-separated field of an MMS
    item (`PLT1GGIO1$ST$Ind01$stVal` -> `ST`)."""
    parts = item.split("$")
    return parts[1] if len(parts) > 1 else ""


def _collapse(rows: list) -> dict:
    """One `(ld, item)` per bit, chosen by an explicit, deterministic rule.

    Several 61850 points can share one `sAddr` -- SEL mirrors a status point
    onto every logical device and breaker node that cares about it, so a bit
    like LOCSTA in 451/010 legitimately backs 28 different items. They are
    all fed from the same Relay Word bit and therefore all read the same
    value: there is no "correct" point to pick among them when they share an
    FC, only a predictable one. But a `CO` point is not a reading of the bit
    at all -- it is the command that sets it -- so the choice is not a bare
    tiebreak, it is a ranked preference:

      1. Prefer by functional constraint, in `FC_PREFERENCE` order (`ST`
         before `MX` before ... before `CO` last). A bit that exists only as
         `CO` -- a remote-bit-style control point with no status
         representation, e.g. RB01 -- still gets its `CO` item; ranking last
         is not filtering out.
      2. Within one FC, take the lexicographically lowest (ld, item), which
         also tends to keep a bit in a predictable logical device (fewer,
         packed LD/FC containers means fewer round trips per poll cycle --
         30 requests / ~180 ms for a whole diagram on the bench 451).
    """
    by_bit: dict = {}
    for row in rows:
        by_bit.setdefault(row["bit"].upper(), []).append((row["ld"], row["item"]))
    chosen = {}
    for bit, candidates in by_bit.items():
        best = min(candidates, key=lambda c: (_fc_rank(_fc(c[1])), c))
        chosen[bit] = list(best)
    return chosen


def decorated_rows(scl_path: Path) -> dict:
    """`{BIT: [ld_suffix, item, [alternativas, indice, nbits]]}` de um ICD.

    So' os enderecos DECORADOS -- os que `wordbits.json` nao tem. O FC sai do
    `DataTypeTemplates` do proprio arquivo e nao de uma constante: medido nos
    146 ICD do corpus, os 2.030 enderecos decorados sao todos `ST`, e resolver
    mesmo assim e' o que faz um ICD futuro que discorde falhar alto em vez de
    gravar um item que o rele nao serve.

    Um ponto sem FC resolvivel nao vira linha: chutar produz um item que some
    calado la' na frente, quando `resolve_map` o conferir contra o diretorio.
    """
    fcs_by_ied = sel_da_fcs(scl_path)
    best: dict = {}
    for ied, points in sel_short_addresses(scl_path).items():
        fcs = fcs_by_ied.get(ied, {})
        for bit, p in points.items():
            rule = getattr(p, "rule", None)
            if rule is None:
                continue
            fc = fcs.get((p.ld_inst, p.ln, p.do, p.da))
            if not fc:
                continue
            da = "$".join(p.da.split("."))
            item = f"{p.ln}${fc}${p.do}${da}"
            key = (_fc_rank(fc), p.ld_inst, item)
            if bit in best and key >= best[bit][0]:
                continue
            best[bit] = (key, [p.ld_inst, item,
                              [list(rule.alternatives), rule.index,
                               rule.nbits]])
    return {bit: row for bit, (_key, row) in best.items()}


def merge_decorated(plain: dict, decorated: dict) -> dict:
    """As linhas lisas, mais as decoradas que elas nao cobrem.

    Nunca sobrescreve: um bit com endereco booleano fica com ele. E' a mesma
    preferencia do caminho vivo (`mms_tables.da_rank`), e tambem o que impede
    esta passagem de mudar o item de uma linha ja publicada.
    """
    out = dict(plain)
    for bit, row in decorated.items():
        out.setdefault(bit, row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path,
                    default=FIXTURES_DIR / "ICD files" / "SEL" / "wordbits.json")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--icd-dir", type=Path,
                    default=FIXTURES_DIR / "ICD files" / "SEL",
                    help="onde procurar o ICD de cada modelo, pelo basename "
                         "que o proprio wordbits.json registra em `path`")
    ap.add_argument("-o", "--out", type=Path, default=MMS_MAP_OUT)
    args = ap.parse_args()

    models = json.loads(args.src.read_text(encoding="utf-8"))["models"]
    args.out.mkdir(parents=True, exist_ok=True)
    written = total = decorated_total = 0
    missing_icd: list = []
    for key, entry in models.items():
        if not entry.get("bits"):
            continue
        part, group = key.split("/", 1)
        if not args.all and norm(part) not in CORPUS:
            continue
        bits = _collapse(entry["bits"])
        # A metade decorada vem do ICD que ESTE modelo nomeia, e nao de uma
        # varredura do diretorio: e' o que garante que as duas metades da
        # tabela venham da mesma revisao de firmware.
        icd = args.icd_dir / Path(entry.get("path", "")).name
        source = "ICD de fabrica, via fixtures/ICD files/SEL/wordbits.json"
        if entry.get("path") and icd.is_file():
            found = decorated_rows(icd)
            if found:
                bits = merge_decorated(bits, found)
                source += f"; enderecos decorados lidos de {icd.name}"
                decorated_total += len(found)
        elif entry.get("path"):
            missing_icd.append(Path(entry["path"]).name)
        doc = {
            "part": part,
            "group": group,
            "config_version": entry.get("config_version"),
            "source": source,
            # bit -> [ld_suffix, item] ou, num ponto decorado,
            # [ld_suffix, item, [alternativas, indice, nbits]]. O item ja traz
            # o FC do ICD; o modo MMS confere contra o diretorio do rele antes
            # de usar.
            "bits": bits,
        }
        path = args.out / f"{norm(part)}-{group}.json"
        path.write_text(json.dumps(doc, separators=(",", ":"),
                                   ensure_ascii=False) + "\n", encoding="utf-8")
        written += 1
        total += path.stat().st_size
    print(f"{written} tabelas em {args.out} ({total / 1e6:.1f} MB), "
          f"{decorated_total} endereco(s) decorado(s)")
    if missing_icd:
        # Nao e' erro: um clone sem os ICD gera as tabelas lisas do mesmo
        # jeito. Mas em silencio ninguem descobre por que a metade decorada
        # sumiu de uma regeracao pra outra.
        print(f"AVISO: {len(missing_icd)} ICD nao encontrado(s) em "
              f"{args.icd_dir} -- sem a metade decorada: "
              f"{', '.join(sorted(missing_icd)[:5])}"
              f"{' ...' if len(missing_icd) > 5 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
