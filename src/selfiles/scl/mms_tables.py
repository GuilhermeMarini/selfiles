"""The shipped bit -> MMS item tables, loaded lazily and memoised.

Third per-model registry in this project, after `data/relay_models/` (GLV and
the GLE tools) and `data/wordbits/` (the DNP map's name check). They come from
different sources and drift; `tests/test_relay_models.py` fails on a model
present in one and missing from another unless the asymmetry is written down.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from selfiles import _paths

_logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: dict = {}
# Um FLAG, e nao a verdade do proprio dicionario. `if _CACHE:` parece a mesma
# coisa e nao e': com o diretorio vazio, cada consulta varria o disco de novo;
# e, pior, quando um arquivo levantava no meio do laco as entradas ja lidas
# ficavam no cache e a chamada SEGUINTE devolvia um registro pela metade sem
# dizer nada -- o 411L achado, o 751 sumido.
_LOADED = False

# Ordered FC preference for collapsing several 61850 points that share one
# Relay Word bit -- or, on the live path, for asking a GetLogicalDeviceDirectory
# which FC an SCD's LN$*$DO$DA actually landed under -- down to one. `CO`
# (control) is last on purpose: it is a command that SETS a point, not a
# reading of it, so picking it over `ST` (status) or `MX` (measurement) would
# poll the wrong thing even though it happens to be reachable through the same
# bit. `tools/mms_tables_from_wordbits.py` (the fallback-table generator) and
# the PAC CT live-SCD resolver both import this same
# tuple -- they must not keep two copies that can drift apart silently.
FC_PREFERENCE = ("ST", "MX", "SP", "CF", "DC", "CO")


def fc_rank(fc: str) -> tuple:
    """Sort key for one candidate's FC: `(0, position in FC_PREFERENCE)` for
    anything that is not `CO`, `(1, 0)` for `CO`. The two-tier shape is
    deliberate -- it keeps `CO` strictly worse than every other FC, including
    one absent from `FC_PREFERENCE` (the corpus also has a handful of `SG`,
    setting-group, points), because `CO` is a command and everything else,
    named or not, is still a reading.
    """
    if fc == "CO":
        return (1, 0)
    if fc in FC_PREFERENCE:
        return (0, FC_PREFERENCE.index(fc))
    return (0, len(FC_PREFERENCE))


# -- which DA is worth reading, and which one wins when a bit has several ----
#
# The GLV paints Relay Word BITS. A point is only useful to it when the leaf
# attribute it names carries a BOOLEAN status, so this is an allowlist and not
# a denylist: measured over the whole tracked corpus (`samples/substation_demo.scd`
# plus all ten shipped `data/mms_map/*.json`), the leaf vocabulary is closed at
# 54 names, and every boolean one in it is below. Everything else is a float
# (`instMag.f`, `*.instCVal.mag.f`), a counter (`actVal`), a setting (`setVal`,
# `setTm`), a quality bitstring (`q`), a tap position (`valWTr.posVal`), an
# enumerated direction (`dirGeneral`) or a COMMAND (`Oper.ctlVal`) -- and
# `int(bool(x))` of any of those is not a bit reading, it is a fabrication.
#
# The one-segment rule does the other half of the work: every multi-segment
# path in the corpus is a float, a control or a quality, so a leaf that has to
# descend through an SDI is out by construction.
BOOLEAN_STATUS_DAS = frozenset({
    "stVal",                                  # SPS/SPC/DPS/INS/ENS
    "general",                                # ACD/ACT
    "phsA", "phsB", "phsC",                   # ACD/ACT per phase
    "phsAB", "phsBC", "phsCA",                # ACD/ACT phase-to-phase
    "neut", "res", "neg", "pos", "zer",       # ACD/ACT sequence/residual
})

# The roots of a control (the `CO` side of a DO). A command SETS a point; it is
# not a reading of it, and polling one would be asking the relay what we last
# told it rather than what it sees.
CONTROL_DA_ROOTS = frozenset({"Oper", "SBOw", "SBO", "Cancel"})


def da_parts(da: str) -> tuple:
    """`"Oper.ctlVal"` / `"Oper$ctlVal"` -> `("Oper", "ctlVal")`.

    SCL writes the descent through an SDI with '.', MMS spells every level
    with '$'; the two sources of a map use one each.
    """
    return tuple(p for p in (da or "").replace("$", ".").split(".") if p)


def is_boolean_status(da) -> bool:
    """Is this leaf a boolean the GLV can paint as a bit?"""
    parts = da_parts(da) if isinstance(da, str) else tuple(da)
    return len(parts) == 1 and parts[0] in BOOLEAN_STATUS_DAS


# -- os DA enumerados, que so' viram bit COM uma regra ----------------------
#
# Um `Pos$stVal` e' um DPS: o valor e' um Dbpos (0 intermediate, 1 off, 2 on,
# 3 bad-state), nao um booleano. Um `Health$stVal` e' um INS, um `dirGeneral`
# e' um enumerado. Nenhum deles se le' com `int(bool(x))` -- e a py61850
# devolve um Dbpos como a STRING "10", cujo `bool()` e' True inclusive para
# "00", ou seja, disjuntor pintado fechado para sempre.
#
# Estes DA so' entram no mapa acompanhados da regra que diz quais bits o valor
# carrega (ver `parse_saddr`). Medido no corpus (SCD do projeto + os 345 ICD
# de fabrica): sao exatamente estas as (DO, DA) que recebem endereco decorado,
# e NENHUM dos 127.225 enderecos lisos cai numa delas -- ou seja, exigir a
# regra nao tira nada de ninguem hoje, e' so' o portao que impede a leitura
# inventada de entrar amanha.
ENUM_STATUS_DAS = frozenset({
    "stVal",                                  # DPS (Pos), INS/ENS (Health, Mod, Beh)
    "dirGeneral",                             # ACD (Dir, Str)
})

# Os DO cujo `stVal` NAO e' booleano. `stVal` sozinho nao distingue um SPS
# (`Ind01$stVal`, um bit) de um DPS ou de um INS (`Pos$stVal`, um Dbpos;
# `Health$stVal`, um enumerado) -- quem distingue e' o DO. Sem esta lista o
# portao de `mms_map` seria satisfeito por acidente: hoje nenhum dos 127.225
# enderecos lisos do corpus cai num destes DO, mas "nao acontece hoje" nao e'
# a mesma coisa que "nao pode passar". Um `Pos$stVal` sem regra lido como
# booleano e' um disjuntor pintado fechado para sempre, e' o pior erro que
# esta ferramenta pode cometer numa tela de comissionamento.
ENUM_STATUS_DOS = frozenset({
    "Pos",                                    # DPC/DPS: posicao de manobra
    "Health", "Mod", "Beh", "TrBeh",          # INS/ENS: estado do IED
    "EEHealth", "PhyHealth", "ExConSt1",
})


def is_enum_do(do: str) -> bool:
    """O `stVal` deste DO e' um enumerado, e nao um booleano?"""
    return (do or "") in ENUM_STATUS_DOS


def is_enum_status(da) -> bool:
    """E' um DA cujo valor pode carregar bits, mas so' com uma regra junto?

    `stVal` e' os dois: o DA de um SPS booleano E o de um DPS enumerado. Quem
    separa e' a decoracao do `sAddr`, nao o nome do DA -- por isso o portao em
    `resolve_map` exige a regra, e nao so' este teste.
    """
    parts = da_parts(da) if isinstance(da, str) else tuple(da)
    return len(parts) == 1 and parts[0] in ENUM_STATUS_DAS


def da_rank(da, decorated: bool = False) -> tuple:
    """Sort key for one candidate DA of a bit: boolean status, then a decorated
    enumerated one, then anything else, then a control -- `Oper.*` strictly
    last.

    The FC preference above cannot rescue this one: an SCD names the DA, and a
    bit whose `Oper.ctlVal` was kept and whose `stVal` was thrown away never
    reaches `fc_rank` with a status candidate to choose. Measured on
    `samples/substation_demo.scd`: `LOCSTA` and 86 other bits of the IED
    `QPC1_LT2_UPC1` resolved to `Oper.ctlVal` under a plain first-wins.

    `decorated` opens a degrau BETWEEN the boolean tier and the rest, and it
    has to exist: um `Pos$stVal` decorado e um `Ind04$stVal` liso sao os dois
    `is_boolean_status`, entao sem ele o desempate entre os dois voltaria a
    ser ordem de documento. Medido em `QPC1_LT1_UPC1`: 10 dos 33 nomes
    decorados tambem tem endereco liso. O degrau e' `(0, 1)` e nao um tier
    novo de propósito -- renumerar `(1,)` e `(2,)` mexeria em quem ja compara
    contra eles.
    """
    parts = da_parts(da) if isinstance(da, str) else tuple(da)
    if parts and (parts[0] in CONTROL_DA_ROOTS
                  or parts[-1].startswith("ctlVal")):
        return (2,)
    if decorated:
        return (0, 1) if is_enum_status(parts) or is_boolean_status(parts) \
            else (1,)
    if is_boolean_status(parts):
        return (0,)
    return (1,)


# -- a gramatica do `sAddr`: um ponto 61850 pode carregar DOIS bits ---------
#
# A SEL escreve a posicao de um disjuntor como UM ponto (`Pos$stVal`, um DPS)
# cujo valor codifica dois bits da Relay Word:
#
#     sAddr="db:52A|52B?0:1:2:3"
#
# As alternativas sao indexadas pela combinacao dos bits, PRIMEIRO NOME COMO
# BIT MAIS SIGNIFICATIVO: (0,0)->0, (0,1)->1, (1,0)->2, (1,1)->3. Isso e'
# exatamente o Dbpos da IEC 61850 -- intermediate, off, on, bad-state -- e a
# mesma leitura reproduz `52A?1:2` (um contato auxiliar so': aberto=1,
# fechado=2) e `RELAY_EN?5:1` (um `Mod$stVal`: off=5, on=1).
#
# Medido no corpus inteiro -- SCD do projeto mais os 345 ICD de fabrica,
# 132.250 enderecos `db:`: 127.225 lisos, 4.322 com um nome e 2 alternativas,
# 703 com dois nomes e 4 alternativas. `len(alternativas) == 2**len(nomes)` em
# 5.025 de 5.025, nunca mais de dois nomes, e as alternativas sao sempre
# inteiros pequenos ({0,1,2,3,5}). Uma forma que quebre a invariante e' uma
# forma que ninguem viu: `parse_saddr` devolve `None` nela em vez de chutar,
# porque o chute aqui e' um disjuntor pintado fechado quando esta aberto.

_SADDR_PREFIX = "db:"


@dataclass(frozen=True)
class BitRule:
    """Como tirar UM bit do valor de um ponto que carrega varios."""
    alternatives: tuple    # valor do DA por combinacao dos bits
    index: int             # qual dos nomes e' este bit
    nbits: int             # quantos nomes o endereco tinha


@dataclass(frozen=True)
class SaddrSpec:
    """Um `sAddr="db:..."` lido: os nomes que ele enderaca e, se houver, as
    alternativas que dizem como o valor os codifica."""
    names: tuple
    alternatives: tuple | None

    def rule_for(self, index: int) -> BitRule | None:
        """A regra do bit `index`, ou `None` num endereco liso (booleano)."""
        if self.alternatives is None:
            return None
        return BitRule(alternatives=self.alternatives, index=index,
                       nbits=len(self.names))


def parse_saddr(sa: str) -> SaddrSpec | None:
    """`"db:52A|52B?0:1:2:3"` -> nomes `("52A", "52B")`, alternativas
    `(0, 1, 2, 3)`. `None` no que nao for um endereco `db:` bem formado.

    Recusar e' de proposito, e nao e' o mesmo que falhar: o chamador segue com
    os outros milhares de enderecos do arquivo. O que nao pode acontecer e'
    uma forma desconhecida virar leitura.
    """
    if not sa or not sa.startswith(_SADDR_PREFIX):
        return None
    body = sa[len(_SADDR_PREFIX):]
    head, sep, tail = body.partition("?")
    names = tuple(n.strip().upper() for n in head.split("|"))
    if not all(names):
        return None
    if not sep:
        return SaddrSpec(names=names, alternatives=None)
    try:
        alternatives = tuple(int(v) for v in tail.split(":"))
    except ValueError:
        return None
    if len(alternatives) != 2 ** len(names):
        return None
    return SaddrSpec(names=names, alternatives=alternatives)


def decode_bit(rule: BitRule | None, value):
    """O valor lido do rele -> `0`/`1` para ESTE bit, ou `None` sem leitura.

    Duas formas chegam aqui, e as duas vem da propria py61850: um BIT-STRING
    (o Dbpos) volta como a string `"10"`, e um INTEGER/enumerado volta como
    `int`. Qualquer outra coisa -- e um valor que nao case com alternativa
    nenhuma, como um Dbpos 3 (bad-state) contra um ponto `?1:2` -- e' `None`,
    que tira o bit do payload e o deixa indeterminado no desenho. Em
    comissionamento "nao consegui ler" e "o rele diz 0" sao coisas diferentes.

    Nunca levanta: mil outros bits dependem da mesma volta do polling.
    """
    if rule is None:
        return None
    if isinstance(value, str):
        try:
            value = int(value, 2)
        except ValueError:
            return None
    elif isinstance(value, bool) or not isinstance(value, int):
        return None
    try:
        index = rule.alternatives.index(value)
    except ValueError:
        return None
    return (index >> (rule.nbits - 1 - rule.index)) & 1


def norm_part(part: str) -> str:
    """`311C-1`, `311C1` and `311c_1` are one peca.

    The ICD file name writes the dash, the SCD's configVersion does not, and
    the RDB writes its own. Folding them is what stopped the 311C matching
    nothing and reporting 100% of its Relay Word as unaddressable.
    """
    return re.sub(r"[^A-Z0-9]", "", (part or "").upper())


@dataclass(frozen=True)
class MmsTable:
    part: str
    group: str
    config_version: str | None
    bits: dict          # BIT -> (ld_suffix, item)


def _load_one(path: Path) -> MmsTable | None:
    """Uma tabela, ou ``None`` se o arquivo nao servir. Nunca levanta.

    Mesma politica que `core.wordbits` e `core.relay_models` ja escreviam nos
    seus proprios loaders: um arquivo ruim nao pode derrubar o registro
    inteiro. Aqui isso vale ainda mais, porque este registro e' consultado no
    caminho do CONECTAR do GLV -- um `data/mms_map/*.json` corrompido tirava
    do ar o modo MMS de TODOS os modelos, e nao so' o do arquivo quebrado.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        part = norm_part(raw["part"])
        if not part:
            raise ValueError("campo 'part' vazio")
        return MmsTable(
            part=part, group=str(raw["group"]),
            config_version=raw.get("config_version"),
            bits={k.upper(): (v[0], v[1]) for k, v in raw["bits"].items()},
        )
    except (OSError, ValueError, TypeError, KeyError, IndexError) as e:
        _logger.warning("[mms_map] %s ignorado: %s", path.name, e)
        return None


def _load() -> dict:
    global _LOADED
    with _LOCK:
        if _LOADED:
            return _CACHE
        for base in _paths.data_dirs("mms_map"):
            for path in sorted(base.glob("*.json")):
                table = _load_one(path)
                if table is None:
                    continue
                # Overlay primeiro: um (part, group) ja' preenchido nao e'
                # substituido pela tabela empacotada.
                _CACHE.setdefault(table.part, {}).setdefault(table.group, table)
        _LOADED = True
        return _CACHE


def groups_for(part: str) -> list:
    return sorted(_load().get(norm_part(part), {}))


def lookup(part: str, group: str | None = None):
    """The table for `part`. Without a group, the newest one.

    Nearest-group is deliberate: firmware moves faster than the ICD corpus, and
    the caller verifies every item against the relay's own directory anyway.
    """
    by_group = _load().get(norm_part(part))
    if not by_group:
        return None
    if group is not None and str(group) in by_group:
        return by_group[str(group)]
    return by_group[max(by_group)]


def invalidate() -> None:
    global _LOADED
    with _LOCK:
        _CACHE.clear()
        _LOADED = False
