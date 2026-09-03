"""
The catalogue of settings groups, per SEL relay family.

Each family organises its SET_*.TXT files into different "groups". This module
exposes:

  - the canonical group list per family (with a pt-BR label and a priority)
  - resolution of a file name -> a group key
  - the SELOGIC dialect each family speaks

The family is inferred from the RELAYTYPE in `[INFO]` (see
`family_from_relaytype`).

The labels stay in Portuguese on purpose: they are shown on screen, and the
people commissioning these relays read Portuguese.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Family = Literal["3xx", "4xx", "7xx"]
Dialect = Literal["symbolic", "keyword"]


@dataclass(frozen=True)
class Group:
    key: str             # ex.: "L1", "S1", "1", "DNPA"
    label: str           # rotulo amigavel em PT-BR
    file_basename: str   # ex.: "SET_L1.TXT" (case-insensitive na resolucao)
    has_logic: bool      # se True, equacoes booleanas estao presentes
    sort_order: int      # ordem nas abas


# The catalogues are ordered lists, and the dashboard follows that order.
# File names follow the QuickSet convention (uppercase on the 4xx, lowercase
# on the 3xx and 7xx) -- but resolution here is case-insensitive.

_CAT_4XX: tuple[Group, ...] = (
    Group("S1",  "Grupo 1 (Sistema)",        "SET_S1.TXT",  False, 100),
    Group("S2",  "Grupo 2 (Sistema)",        "SET_S2.TXT",  False, 101),
    Group("S3",  "Grupo 3 (Sistema)",        "SET_S3.TXT",  False, 102),
    Group("S4",  "Grupo 4 (Sistema)",        "SET_S4.TXT",  False, 103),
    Group("S5",  "Grupo 5 (Sistema)",        "SET_S5.TXT",  False, 104),
    Group("S6",  "Grupo 6 (Sistema)",        "SET_S6.TXT",  False, 105),
    Group("L1",  "Logica de Protecao 1",     "SET_L1.TXT",  True,  200),
    Group("L2",  "Logica de Protecao 2",     "SET_L2.TXT",  True,  201),
    Group("L3",  "Logica de Protecao 3",     "SET_L3.TXT",  True,  202),
    Group("L4",  "Logica de Protecao 4",     "SET_L4.TXT",  True,  203),
    Group("L5",  "Logica de Protecao 5",     "SET_L5.TXT",  True,  204),
    Group("L6",  "Logica de Protecao 6",     "SET_L6.TXT",  True,  205),
    Group("A1",  "Automacao 1",              "SET_A1.TXT",  True,  300),
    Group("A2",  "Automacao 2",              "SET_A2.TXT",  True,  301),
    Group("A3",  "Automacao 3",              "SET_A3.TXT",  True,  302),
    Group("A4",  "Automacao 4",              "SET_A4.TXT",  True,  303),
    Group("A5",  "Automacao 5",              "SET_A5.TXT",  True,  304),
    Group("A6",  "Automacao 6",              "SET_A6.TXT",  True,  305),
    Group("A7",  "Automacao 7",              "SET_A7.TXT",  True,  306),
    Group("A8",  "Automacao 8",              "SET_A8.TXT",  True,  307),
    Group("A9",  "Automacao 9",              "SET_A9.TXT",  True,  308),
    Group("A10", "Automacao 10",             "SET_A10.TXT", True,  309),
    Group("G1",  "Global",                   "SET_G1.TXT",  False, 400),
    Group("R1",  "Relatorios",               "SET_R1.TXT",  False, 410),
    Group("B1",  "Bay Screen",               "SET_B1.TXT",  False, 420),
    Group("F1",  "Front Panel",              "SET_F1.TXT",  False, 430),
    Group("T1",  "Timers",                   "SET_T1.TXT",  False, 440),
    Group("M1",  "Modbus",                   "SET_M1.TXT",  False, 450),
    Group("N1",  "Notas",                    "SET_N1.TXT",  False, 460),
    Group("O1",  "Saidas",                   "SET_O1.TXT",  False, 470),
    Group("P1",  "Porta 1",                  "SET_P1.TXT",  False, 500),
    Group("P2",  "Porta 2",                  "SET_P2.TXT",  False, 501),
    Group("P3",  "Porta 3",                  "SET_P3.TXT",  False, 502),
    Group("P5",  "Porta 5 (Ethernet)",       "SET_P5.TXT",  False, 504),
    Group("PF",  "Painel Frontal",           "SET_PF.TXT",  False, 510),
    Group("D1",  "DNP 1",                    "SET_D1.TXT",  False, 600),
    Group("D2",  "DNP 2",                    "SET_D2.TXT",  False, 601),
    Group("D3",  "DNP 3",                    "SET_D3.TXT",  False, 602),
    Group("D4",  "DNP 4",                    "SET_D4.TXT",  False, 603),
    Group("D5",  "DNP 5",                    "SET_D5.TXT",  False, 604),
)


_CAT_7XX: tuple[Group, ...] = (
    Group("1",   "Grupo 1",                  "set_1.txt",   False, 100),
    Group("2",   "Grupo 2",                  "set_2.txt",   False, 101),
    Group("3",   "Grupo 3",                  "set_3.txt",   False, 102),
    Group("4",   "Grupo 4",                  "set_4.txt",   False, 103),
    Group("L1",  "Logica 1",                 "set_L1.txt",  True,  200),
    Group("L2",  "Logica 2",                 "set_L2.txt",  True,  201),
    Group("L3",  "Logica 3",                 "set_L3.txt",  True,  202),
    Group("L4",  "Logica 4",                 "set_L4.txt",  True,  203),
    Group("G",   "Global",                   "set_G.txt",   False, 400),
    Group("M",   "Modbus",                   "set_M.txt",   False, 410),
    Group("R",   "Relatorios",               "set_R.txt",   False, 420),
    Group("F",   "Front Panel",              "set_F.txt",   False, 430),
    Group("I",   "Info",                     "set_I.txt",   False, 440),
    Group("P1",  "Porta 1",                  "set_P1.txt",  False, 500),
    Group("P2",  "Porta 2",                  "set_P2.txt",  False, 501),
    Group("P3",  "Porta 3",                  "set_P3.txt",  False, 502),
    Group("P4",  "Porta 4 (Ethernet)",       "set_P4.txt",  False, 503),
    Group("PF",  "Painel Frontal",           "set_PF.txt",  False, 510),
    Group("HMI", "HMI",                      "SET_HMI.TXT", False, 520),
    Group("D1",  "DNP 1",                    "set_D1.txt",  False, 600),
    Group("D2",  "DNP 2",                    "set_D2.txt",  False, 601),
    Group("D3",  "DNP 3",                    "set_D3.txt",  False, 602),
    Group("E1",  "Ethernet 1",               "set_E1.txt",  False, 700),
    Group("E2",  "Ethernet 2",               "set_E2.txt",  False, 701),
    Group("E3",  "Ethernet 3",               "set_E3.txt",  False, 702),
)


_CAT_3XX: tuple[Group, ...] = (
    Group("1",    "Grupo 1",                 "SET_1.TXT",   False, 100),
    Group("2",    "Grupo 2",                 "SET_2.TXT",   False, 101),
    Group("3",    "Grupo 3",                 "SET_3.TXT",   False, 102),
    Group("4",    "Grupo 4",                 "SET_4.TXT",   False, 103),
    Group("5",    "Grupo 5",                 "SET_5.TXT",   False, 104),
    Group("6",    "Grupo 6",                 "SET_6.TXT",   False, 105),
    Group("L1",   "Logica 1",                "SET_L1.TXT",  True,  200),
    Group("L2",   "Logica 2",                "SET_L2.TXT",  True,  201),
    Group("L3",   "Logica 3",                "SET_L3.TXT",  True,  202),
    Group("L4",   "Logica 4",                "SET_L4.TXT",  True,  203),
    Group("L5",   "Logica 5",                "SET_L5.TXT",  True,  204),
    Group("L6",   "Logica 6",                "SET_L6.TXT",  True,  205),
    Group("G",    "Global",                  "SET_G.TXT",   False, 400),
    Group("M",    "Modbus",                  "SET_M.TXT",   False, 410),
    Group("R",    "Relatorios",              "SET_R.TXT",   False, 420),
    Group("T",    "Testes",                  "SET_T.TXT",   False, 430),
    Group("P1",   "Porta 1",                 "SET_P1.TXT",  False, 500),
    Group("P2",   "Porta 2",                 "SET_P2.TXT",  False, 501),
    Group("P3",   "Porta 3",                 "SET_P3.TXT",  False, 502),
    Group("P4",   "Porta 4",                 "SET_P4.TXT",  False, 503),
    Group("P5",   "Porta 5 (Ethernet)",      "SET_P5.TXT",  False, 504),
    Group("PF",   "Painel Frontal",          "SET_PF.TXT",  False, 510),
    Group("D1",   "DNP 1",                   "SET_D1.TXT",  False, 600),
    Group("D2",   "DNP 2",                   "SET_D2.TXT",  False, 601),
    Group("D3",   "DNP 3",                   "SET_D3.TXT",  False, 602),
    Group("DNPA", "DNP A (311L)",            "set_DNPA.txt",False, 610),
    Group("DNPB", "DNP B (311L)",            "set_DNPB.txt",False, 611),
    Group("X",    "Terminal Remoto X (87L)", "set_X.txt",   False, 700),
    Group("Y",    "Terminal Remoto Y (87L)", "set_Y.txt",   False, 701),
)


_CATALOGS: dict[Family, tuple[Group, ...]] = {
    "3xx": _CAT_3XX,
    "4xx": _CAT_4XX,
    "7xx": _CAT_7XX,
}


_DIALECTS: dict[Family, Dialect] = {
    "3xx": "symbolic",
    "4xx": "keyword",
    "7xx": "keyword",
}


# Padrao para descobrir a familia do RELAYTYPE: SEL-487E-3 -> 4xx, 0311L -> 3xx,
# SEL-751 -> 7xx. Aceita variacoes: "411L", "SEL-411L-A", "0311C-1", "751".
_FAMILY_RE = re.compile(r"^(?:SEL-?)?0?([3-9])\d{2}", re.IGNORECASE)


def family_from_relaytype(relaytype: str | None) -> Family | None:
    """Infer the family (3xx/4xx/7xx) from the RELAYTYPE in [INFO].

    Returns None when the RELAYTYPE matches no known pattern -- an SEL-2414,
    say, which is automation and communications hardware, not a protection
    relay. Callers use that None to filter.
    """
    if not relaytype:
        return None
    m = _FAMILY_RE.match(relaytype.strip())
    if not m:
        return None
    d = m.group(1)
    if d == "3":
        return "3xx"
    if d == "4":
        return "4xx"
    if d == "7":
        return "7xx"
    return None


def is_relay_device(relaytype: str | None) -> bool:
    """True when the RELAYTYPE names a protection relay (3xx/4xx/7xx)."""
    return family_from_relaytype(relaytype) is not None


def dialect_for_family(family: Family) -> Dialect:
    return _DIALECTS[family]


def groups_for_family(family: Family) -> tuple[Group, ...]:
    return _CATALOGS[family]


def find_group(family: Family, group_key: str) -> Group | None:
    for g in _CATALOGS[family]:
        if g.key == group_key:
            return g
    return None


def find_group_by_filename(family: Family, filename: str) -> Group | None:
    """Resolve a file name (case-insensitively) to its Group."""
    target = filename.lower()
    for g in _CATALOGS[family]:
        if g.file_basename.lower() == target:
            return g
    return None
