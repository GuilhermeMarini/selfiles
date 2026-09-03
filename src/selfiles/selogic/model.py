"""
Normalizador familia-aware: linhas de SET_*.TXT -> Variable em forma canonica.

A saida deste modulo eh consumida pelo Comparador de Ajustes do PAC CT
e pelo CLI smoke (`tools/check_settings_compare.py`).

Cada variavel tem N "campos" comparaveis:

  - latch    : 'set', 'reset'
  - timer    : 'input', 'pickup', 'dropout'
  - counter  : 'count_down', 'count_up', 'load', 'preset'
  - direct   : 'value' (uma unica expressao)
  - numeric  : 'value'
  - enum     : 'value'
  - string   : 'value'
  - math     : 'value' (expressao matematica nao-booleana)

Convencao de nomes canonicos por familia (resumo, fonte: SEL-411L IM
Tabela 13.24 + leitura dos RDBs reais):

  3xx (311C/L)  4xx (411L/487E)      7xx (751/787)
  ----------    ---------------      ---------------
  LT1..LT16     PLT01..PLT32         LT01..LT64        (latches)
  SV1..SV16     PSV01.. + PCT01..    SV01..SV64        (variaveis logicas + timers)
  -             ASV001..ASV256       MV01..MV64        (math/auto vars)
  -             AMV001.. + ACT01..   -
  -             PCN01..PCN32         SC01..SC64        (counters)

API publica:

    normalize_relay(relay_dir, family) -> RelayModel
    Variable, VarField, Source              (dataclasses)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from selfiles.selogic import parser as sp
from selfiles.selogic.catalog import (
    Family,
    Group,
    dialect_for_family,
    find_group_by_filename,
    groups_for_family,
)
from selfiles.settings import (
    ParsedSettings,
    parse_relay_settings_dir,
)

VarKind = Literal[
    "latch", "timer", "counter", "direct", "math",
    "number", "enum", "string",
    "ser_list",        # SER1/SER2/... (lista de wordbits para SOE recorder)
    "ser_item",        # SITM<n> (4xx) ou ALIAS<n> (7xx) -- agrupado por wordbit
]


@dataclass(frozen=True)
class Source:
    file: str         # ex.: "SET_L1.TXT" (basename, sem path)
    lineno: int       # 1-based
    raw_key: str      # chave bruta da linha (PROTSEL12 / SET01 / TR)


FieldKind = Literal["logic", "number", "enum", "string", "set_list"]


@dataclass(frozen=True)
class VarField:
    """Um valor comparavel dentro de uma Variavel.

    `kind` decide o caminho do comparator (logic/number/enum/string/set_list).
    `raw` eh o valor como veio do arquivo, antes de qualquer normalizacao
    (inclui comentario `# ...` se houver). `comment` e `body` sao extraidos
    para conveniencia do UI.
    """
    name: str         # 'set' / 'reset' / 'input' / 'pickup' / ...
    kind: FieldKind
    raw: str
    body: str = ""
    comment: str = ""
    source: Source | None = None


@dataclass
class Variable:
    name: str                       # 'PLT11', 'LT05', 'TR', 'SV01', etc.
    kind: VarKind
    fields: list[VarField] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)

    def get_field(self, name: str) -> VarField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


@dataclass
class GroupModel:
    """Variaveis canonicas de um grupo (uma "aba" no UI)."""
    group_key: str
    group_label: str
    file_basename: str        # ex.: "SET_L1.TXT"
    variables: dict[str, Variable] = field(default_factory=dict)


@dataclass
class RelayModel:
    """Snapshot normalizado de um rele (todos os SET_*.TXT)."""
    relay_name: str
    relaytype: str | None
    family: Family
    dialect: sp.Dialect
    groups: dict[str, GroupModel] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers comuns
# ---------------------------------------------------------------------------

_ASSIGN_RE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*:=\s*(.*)$', re.DOTALL)


def _split_assignment(raw_value: str) -> tuple[str | None, str]:
    """Para 4xx: separa `LHS := RHS [# comment]` em (LHS, "RHS [# comment]").

    Se nao tem `:=`, retorna (None, raw_value)."""
    m = _ASSIGN_RE.match(raw_value)
    if not m:
        return None, raw_value
    return m.group(1), m.group(2)


def _split_body_comment(raw: str) -> tuple[str, str]:
    h = raw.find("#")
    if h < 0:
        return raw.strip(), ""
    return raw[:h].strip(), raw[h + 1 :].strip()


_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_ENUM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:,[A-Za-z][A-Za-z0-9_]*)*$")
# Valores tipicos: "Y", "N", "OFF", "S,T", "DAB", "1PH", "ABC"


#: O `kind` que `_infer_kind` devolve -> o `kind` da Variable. Estava escrito
#: inline em quatro lugares; um dicionario com quatro entradas repetido quatro
#: vezes e' quatro lugares pra divergir.
_VAR_KIND_FOR_FIELD: dict[str, VarKind] = {
    "logic": "direct",
    "number": "number",
    "enum": "enum",
    "string": "string",
}


def _infer_kind(value: str, dialect: sp.Dialect) -> Literal["logic", "number", "enum", "string"]:
    """Heuristica leve sobre o conteudo de uma linha k/v generica (fora de
    SET_L*/SET_A*)."""
    s = value.strip()
    if s == "":
        return "string"
    if _NUM_RE.match(s):
        return "number"
    # Algumas chaves carregam multi-token enum: "S,T" / "DAB" / "ABC" / "Y" / "N"
    if _ENUM_RE.match(s) and len(s) <= 12 and "AND" not in s.upper() and "OR" not in s.upper():
        return "enum"
    # Tenta parsear como booleano. Se passa, eh logic.
    if sp.is_boolean_expression(s, dialect):
        return "logic"
    return "string"


def _mk_logic_field(name: str, raw: str, source: Source) -> VarField:
    body, comment = _split_body_comment(raw)
    return VarField(
        name=name, kind="logic", raw=raw, body=body, comment=comment, source=source,
    )


def _mk_field(
    name: str, kind: FieldKind,
    raw: str, source: Source,
) -> VarField:
    body, comment = _split_body_comment(raw)
    return VarField(
        name=name, kind=kind, raw=raw,
        body=body if kind == "logic" else raw.strip(),
        comment=comment,
        source=source,
    )


# ---------------------------------------------------------------------------
# 4xx normalizer (PROTSEL/AUTO via `LHS := RHS`)
# ---------------------------------------------------------------------------

# Sufixos que identificam papeis dentro de uma variavel composta.
_4XX_LATCH_RE = re.compile(r"^(?P<base>[A-Z]+\d+)(?P<role>[SR])$")
_4XX_TIMER_RE = re.compile(r"^(?P<base>P?CT\d+|ACT\d+)(?P<role>IN|PU|DO)$")
_4XX_COUNTER_RE = re.compile(
    r"^(?P<base>P?CN\d+|ACN\d+)(?P<role>IN|CU|CD|LD|PV|R)$"
)
# PSV/ASV/AMV/PMV sao "direct" (uma equacao = uma variavel)


# ---------------------------------------------------------------------------
# Reports (SOE / SER) -- tratamento especial
# ---------------------------------------------------------------------------
#
# Os ajustes de Sequence of Events Recorder (SER) tem semantica diferente do
# resto: a *posicao no arquivo nao importa*. O que importa eh quais wordbits
# o rele esta registrando no SOE e qual o alias atribuido a cada um.
#
#   4xx (SET_R1.TXT): slots numerados de 5 campos por wordbit --
#     SITM<n>="wordbit", SNAME<n>="nome", SSET<n>="alias asserted",
#     SCLR<n>="alias deasserted", SHMI<n>="Y/N pro alarme do HMI".
#     Reagrupado pelo VALOR de SITM<n> (o wordbit eh a chave estavel).
#
#   7xx (set_R.txt): listas SER1..SER4 (CSV de wordbits) + ALIAS<n> com 4
#     tokens space-separated: wordbit, nome, alias_asserted, alias_deasserted.
#     SER<n> comparado como conjunto; ALIAS reagrupado pelo wordbit.
#
#   3xx (SET_R.TXT): apenas SER1..SERn (CSV); sem aliases.
#
# Padrao: o usuario quer ver UMA linha por wordbit registrado, comparando os
# aliases que cada rele atribui (e detectando quando o mesmo bit esta
# registrado em posicoes diferentes entre os reles).

import re as _re

_SITM_RE = _re.compile(r"^(SITM|SNAME|SSET|SCLR|SHMI)(\d+)$")
_SER_RE = _re.compile(r"^SER(\d+)$")
_ALIAS_RE = _re.compile(r"^ALIAS(\d+)$")


#: (SITM|SNAME|SSET|SCLR|SHMI) -> (nome do campo, kind).
_SER_FIELDS_4XX: dict[str, tuple[str, FieldKind]] = {
    "SITM":  ("wordbit",      "enum"),
    "SNAME": ("alias_nome",   "string"),
    "SSET":  ("alias_set",    "string"),
    "SCLR":  ("alias_clear",  "string"),
    "SHMI":  ("hmi_alarm",    "enum"),
}


def _ser_field_for_4xx(role: str) -> tuple[str, FieldKind]:
    """Mapeia (SITM|SNAME|SSET|SCLR|SHMI) -> (field_name, kind)."""
    return _SER_FIELDS_4XX[role]


def _reports_normalize(
    ps: ParsedSettings, group: Group, family: Family,
) -> GroupModel:
    """Normaliza a secao de relatorios (SER) com tratamento especial.

    Retorna um GroupModel onde os SER items aparecem agrupados pelo wordbit
    (nao pelo slot) e as listas SER<n> sao kind=ser_list.
    """
    gm = GroupModel(
        group_key=group.key, group_label=group.label,
        file_basename=group.file_basename,
    )

    # Buffer pra coletar slots SITM antes de agrupar
    sitm_slots: dict[int, dict[str, tuple[str, Source]]] = {}

    for line in ps.lines:
        key = line.key
        value = line.value
        source = Source(file=ps.path.name, lineno=line.lineno, raw_key=key)

        # 4xx: slots SITM/SNAME/SSET/SCLR/SHMI
        m = _SITM_RE.match(key) if family == "4xx" else None
        if m:
            role = m.group(1)
            slot = int(m.group(2))
            sitm_slots.setdefault(slot, {})[role] = (value, source)
            continue

        # SER<n> (3xx/4xx/7xx)
        m = _SER_RE.match(key)
        if m:
            slot = int(m.group(1))
            fld = _mk_field("value", "set_list", value, source)
            gm.variables[key] = Variable(
                name=key, kind="ser_list", fields=[fld], sources=[source],
            )
            continue

        # 7xx: ALIAS<n> -- 4 tokens space-separated
        m = _ALIAS_RE.match(key) if family == "7xx" else None
        if m:
            # Pula entradas vazias ("NA" ou "NA NA NA NA" eh comum)
            tokens = value.split()
            if not tokens or (len(tokens) == 1 and tokens[0].upper() == "NA"):
                continue
            wordbit = tokens[0]
            slot = int(m.group(1))
            # Variable name = wordbit; multiple aliases para o mesmo wordbit
            # (raro mas possivel) sao mesclados, o ultimo vence.
            var_name = wordbit
            v = gm.variables.get(var_name)
            if v is None:
                v = Variable(name=var_name, kind="ser_item")
                gm.variables[var_name] = v
            # Campos: nome, asserted, deasserted -- os 3 tokens restantes
            asserted = tokens[2] if len(tokens) > 2 else ""
            deasserted = tokens[3] if len(tokens) > 3 else ""
            friendly = tokens[1] if len(tokens) > 1 else ""
            v.fields = [
                _mk_field("alias_nome", "string", friendly, source),
                _mk_field("alias_asserted", "string", asserted, source),
                _mk_field("alias_deasserted", "string", deasserted, source),
                # Slot original pra mostrar como metadado no UI
                _mk_field("slot", "number", str(slot), source),
            ]
            v.sources = [source]
            continue

        # Outras chaves da secao R: tratamento generico
        dialect: sp.Dialect = ("symbolic" if family == "3xx"
                               else "keyword")
        kind = _infer_kind(value, dialect)
        fld = _mk_field("value", kind, value, source)
        gm.variables[key] = Variable(
            name=key,
            kind=_VAR_KIND_FOR_FIELD[kind],
            fields=[fld],
            sources=[source],
        )

    # Pos-processamento dos slots SITM (4xx): re-agrupa por wordbit
    for slot, parts in sitm_slots.items():
        sitm_pair = parts.get("SITM")
        if sitm_pair is None:
            continue
        wordbit, sitm_source = sitm_pair
        wordbit = wordbit.strip()
        if not wordbit:
            # Slot vazio -- nao registra
            continue
        # Variable name = wordbit. Conflito (mesmo wordbit em 2 slots) -- raro,
        # mas se acontecer, o ultimo vence.
        var_name = wordbit
        fields: list[VarField] = []
        for role in ("SITM", "SNAME", "SSET", "SCLR", "SHMI"):
            if role not in parts:
                continue
            val, src = parts[role]
            fname, fkind = _ser_field_for_4xx(role)
            fields.append(_mk_field(fname, fkind, val, src))
        # Slot original como metadado
        fields.append(_mk_field("slot", "number", str(slot), sitm_source))
        gm.variables[var_name] = Variable(
            name=var_name, kind="ser_item", fields=fields,
            sources=[sitm_source],
        )

    return gm


# ---------------------------------------------------------------------------
# 4xx normalizer (PROTSEL/AUTO via `LHS := RHS`)
# ---------------------------------------------------------------------------

# Sufixos que identificam papeis dentro de uma variavel composta.
def _4xx_role_of(lhs: str) -> tuple[str, str, VarKind]:
    """Mapeia o LHS de 4xx para (variavel_canonica, papel, kind).

    Papeis possiveis: 'set'/'reset' (latch), 'input'/'pickup'/'dropout' (timer),
    'count_up'/'count_down'/'load'/'preset'/'reset' (counter), 'value' (direct/math).
    """
    m = _4XX_LATCH_RE.match(lhs)
    if m:
        base = m.group("base")
        # PLT01S -> PLT01 / role=set ; PLT01R -> PLT01 / role=reset
        # PSV30 nao casa (nao tem S/R sufixo nesse formato).
        # Mas LATCH_RE casaria PSV30S/PSV30R se existissem.
        if base.startswith(("PLT", "ALT")):
            role = "set" if m.group("role") == "S" else "reset"
            return base, role, "latch"
        # Outros sufixos S/R nao definem latch -- nao agrupar.
    m = _4XX_TIMER_RE.match(lhs)
    if m:
        base = m.group("base")
        role_map = {"IN": "input", "PU": "pickup", "DO": "dropout"}
        return base, role_map[m.group("role")], "timer"
    m = _4XX_COUNTER_RE.match(lhs)
    if m:
        base = m.group("base")
        role_map = {
            "IN": "input", "CU": "count_up", "CD": "count_down",
            "LD": "load", "PV": "preset", "R": "reset",
        }
        return base, role_map[m.group("role")], "counter"
    # Default: a variavel eh o proprio LHS, e o campo eh 'value'.
    return lhs, "value", "direct"


_4XX_PROTSEL_KEY_RE = re.compile(r"^(PROTSEL|AUTO_)\d+$", re.IGNORECASE)


def _4xx_normalize_section(
    ps: ParsedSettings, group: Group,
) -> GroupModel:
    if group.key == "R1":
        return _reports_normalize(ps, group, "4xx")
    gm = GroupModel(
        group_key=group.key, group_label=group.label, file_basename=group.file_basename,
    )
    for line in ps.lines:
        source = Source(
            file=ps.path.name, lineno=line.lineno, raw_key=line.key,
        )
        if _4XX_PROTSEL_KEY_RE.match(line.key) and group.has_logic:
            # Linha PROTSEL<N> ou AUTO_<N>: parse `LHS := RHS`
            lhs, rhs = _split_assignment(line.value)
            if lhs is None:
                # Linha vazia ou mal-formada -- ignora silenciosamente.
                if line.value.strip():
                    # Conteudo mas sem `:=` -- registra como variavel anonima.
                    pass
                continue
            base_name, role, var_kind = _4xx_role_of(lhs)
            # Campos de timer/counter podem ser numericos (PU/DO/PV).
            field_kind: Literal["logic", "number", "enum", "string"]
            if role in ("pickup", "dropout", "preset"):
                field_kind = "number"
            else:
                field_kind = "logic"
            fld = _mk_field(role, field_kind, rhs, source)

            # Cria ou atualiza Variable
            v = gm.variables.get(base_name)
            if v is None:
                v = Variable(name=base_name, kind=var_kind)
                gm.variables[base_name] = v
            v.fields.append(fld)
            v.sources.append(source)
        else:
            # Linha k/v "direta". A variavel eh o proprio nome.
            value = line.value
            # Algumas chaves em SET_S* etc. tem comentario embutido raro;
            # tratamos como string/enum/number.
            kind = _infer_kind(value, "keyword")
            fld = _mk_field("value", kind, value, source)
            v = Variable(
                name=line.key,
                kind=_VAR_KIND_FOR_FIELD[kind],
                fields=[fld],
                sources=[source],
            )
            # Sobre-escreve em caso de duplicata (raro e nao-significante).
            gm.variables[line.key] = v
    return gm


# ---------------------------------------------------------------------------
# 7xx normalizer (slot-indexed; sem `:=`)
# ---------------------------------------------------------------------------

_7XX_SLOT_LATCH_RE = re.compile(r"^(SET|RST)(\d+)$")
_7XX_SLOT_SV_RE = re.compile(r"^SV(\d+)(PU|DO)?$")  # SV01 (input) / SV01PU / SV01DO
_7XX_SLOT_MV_RE = re.compile(r"^MV(\d+)$")
_7XX_SLOT_SC_RE = re.compile(r"^SC(\d+)(CU|CD|LD|PV)$")
_7XX_SLOT_OUT_RE = re.compile(r"^(OUT\d+)(FS)?$")
_7XX_SLOT_TMB_RE = re.compile(r"^(TMB\d+[AB])$")


def _7xx_normalize_section(
    ps: ParsedSettings, group: Group,
) -> GroupModel:
    if group.key == "R":
        return _reports_normalize(ps, group, "7xx")
    gm = GroupModel(
        group_key=group.key, group_label=group.label, file_basename=group.file_basename,
    )

    for line in ps.lines:
        key = line.key
        value = line.value
        source = Source(file=ps.path.name, lineno=line.lineno, raw_key=key)

        if group.has_logic:
            # set_L<n>.txt -> slots
            m = _7XX_SLOT_LATCH_RE.match(key)
            if m:
                role = "set" if m.group(1) == "SET" else "reset"
                base = f"LT{int(m.group(2)):02d}"
                fld = _mk_field(role, "logic", value, source)
                v = gm.variables.get(base)
                if v is None:
                    v = Variable(name=base, kind="latch")
                    gm.variables[base] = v
                v.fields.append(fld)
                v.sources.append(source)
                continue

            m = _7XX_SLOT_SV_RE.match(key)
            if m:
                base = f"SV{int(m.group(1)):02d}"
                role = m.group(2)
                if role == "PU":
                    fld = _mk_field("pickup", "number", value, source)
                elif role == "DO":
                    fld = _mk_field("dropout", "number", value, source)
                else:
                    fld = _mk_field("input", "logic", value, source)
                v = gm.variables.get(base)
                if v is None:
                    v = Variable(name=base, kind="timer")
                    gm.variables[base] = v
                v.fields.append(fld)
                v.sources.append(source)
                continue

            m = _7XX_SLOT_MV_RE.match(key)
            if m:
                base = f"MV{int(m.group(1)):02d}"
                fld = _mk_field("value", "logic", value, source)
                # Math var -- a expressao pode ser matematica, comparator
                # caira em texto-com-tolerancia.
                v = Variable(name=base, kind="math", fields=[fld], sources=[source])
                gm.variables[base] = v
                continue

            m = _7XX_SLOT_SC_RE.match(key)
            if m:
                base = f"SC{int(m.group(1)):02d}"
                role_map = {"CU": "count_up", "CD": "count_down",
                            "LD": "load", "PV": "preset"}
                role = role_map[m.group(2)]
                fld = _mk_field(role, "logic" if role in ("count_up", "count_down", "load") else "number",
                                value, source)
                v = gm.variables.get(base)
                if v is None:
                    v = Variable(name=base, kind="counter")
                    gm.variables[base] = v
                v.fields.append(fld)
                v.sources.append(source)
                continue

            m = _7XX_SLOT_OUT_RE.match(key)
            if m:
                base = m.group(1)            # OUT101
                role = "fail_safe" if m.group(2) else "value"
                kind: FieldKind = ("enum" if role == "fail_safe"
                                   else "logic")
                fld = _mk_field(role, kind, value, source)
                v = gm.variables.get(base)
                if v is None:
                    v = Variable(name=base, kind="direct")
                    gm.variables[base] = v
                v.fields.append(fld)
                v.sources.append(source)
                continue

            m = _7XX_SLOT_TMB_RE.match(key)
            if m:
                # TMB1A, TMB1B etc. -- cada um eh uma variavel independente.
                fld = _mk_field("value", "logic", value, source)
                v = Variable(name=key, kind="direct", fields=[fld], sources=[source])
                gm.variables[key] = v
                continue

        # Fallback: linha k/v direta. Tipo inferido.
        kind = _infer_kind(value, "keyword")
        fld = _mk_field("value", kind, value, source)
        gm.variables[key] = Variable(
            name=key,
            kind=_VAR_KIND_FOR_FIELD[kind],
            fields=[fld],
            sources=[source],
        )

    return gm


# ---------------------------------------------------------------------------
# 3xx normalizer (slot-indexed pra LT; resto eh direto)
# ---------------------------------------------------------------------------

_3XX_SLOT_LATCH_RE = re.compile(r"^(SET|RST)(\d+)$")
_3XX_SV_TIMER_RE = re.compile(r"^SV(\d+)(PU|DO)$")  # SV1PU / SV1DO (3xx)


def _3xx_normalize_section(
    ps: ParsedSettings, group: Group,
) -> GroupModel:
    if group.key == "R":
        return _reports_normalize(ps, group, "3xx")
    gm = GroupModel(
        group_key=group.key, group_label=group.label, file_basename=group.file_basename,
    )

    for line in ps.lines:
        key = line.key
        value = line.value
        source = Source(file=ps.path.name, lineno=line.lineno, raw_key=key)

        if group.has_logic:
            m = _3XX_SLOT_LATCH_RE.match(key)
            if m:
                role = "set" if m.group(1) == "SET" else "reset"
                base = f"LT{int(m.group(2))}"
                fld = _mk_field(role, "logic", value, source)
                v = gm.variables.get(base)
                if v is None:
                    v = Variable(name=base, kind="latch")
                    gm.variables[base] = v
                v.fields.append(fld)
                v.sources.append(source)
                continue

            m = _3XX_SV_TIMER_RE.match(key)
            if m:
                base = f"SV{int(m.group(1))}"
                role = "pickup" if m.group(2) == "PU" else "dropout"
                fld = _mk_field(role, "number", value, source)
                v = gm.variables.get(base)
                if v is None:
                    v = Variable(name=base, kind="timer")
                    gm.variables[base] = v
                v.fields.append(fld)
                v.sources.append(source)
                continue

        kind = _infer_kind(value, "symbolic")
        fld = _mk_field("value", kind, value, source)
        gm.variables[key] = Variable(
            name=key,
            kind=_VAR_KIND_FOR_FIELD[kind],
            fields=[fld],
            sources=[source],
        )

    return gm


# ---------------------------------------------------------------------------
# Driver principal
# ---------------------------------------------------------------------------

_NORMALIZERS = {
    "3xx": _3xx_normalize_section,
    "4xx": _4xx_normalize_section,
    "7xx": _7xx_normalize_section,
}


def normalize_relay(relay_dir: Path, family: Family, relay_name: str | None = None) -> RelayModel:
    """Le todos os SET_*.TXT do diretorio e produz um RelayModel canonico.

    `family` deve ser inferida antes pelo caller via `family_from_relaytype`.
    `relay_name` default eh o basename do diretorio.
    """
    if relay_name is None:
        relay_name = relay_dir.name

    norm = _NORMALIZERS[family]
    parsed_files = parse_relay_settings_dir(relay_dir)
    relaytype: str | None = None
    groups: dict[str, GroupModel] = {}

    for ps in parsed_files:
        if relaytype is None:
            relaytype = ps.relaytype
        group = find_group_by_filename(family, ps.path.name)
        if group is None:
            # Arquivo nao listado no catalogo (e.g., um SET futuro). Ignora.
            continue
        gm = norm(ps, group)
        groups[group.key] = gm

    return RelayModel(
        relay_name=relay_name,
        relaytype=relaytype,
        family=family,
        dialect=dialect_for_family(family),
        groups=groups,
    )


def iter_groups_in_catalog_order(model: RelayModel) -> Iterator[GroupModel]:
    """Itera grupos na ordem do catalogo (pra renderizar abas)."""
    for g in groups_for_family(model.family):
        if g.key in model.groups:
            yield model.groups[g.key]
