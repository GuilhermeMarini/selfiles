"""
Loader de configuracoes por modelo de rele.

Cada arquivo `data/relay_models/<MODELO>.json` descreve um modelo de rele
(SEL-411L, SEL-751, etc.) e contem:

  - O caminho relativo do arquivo SET_P*.TXT onde o IPADDR aparece (e a chave
    a procurar nesse arquivo).
  - Um catalogo de blocos do GLE: para cada tipo, o template de bit derivado
    da porta de saida (ex.: PCNDTIMER -> "PCT{instance:02d}Q"), o mapeamento
    pro tipo real no XML do GLE (ex.: PCNDTIMER -> TIMER), e metadados de
    layout/avaliacao usados pelo renderer.

API publica:

    load_relay_models() -> dict[str, RelayModel]      (chave = MODEL upper)
    lookup(relaytype: str) -> RelayModel | None       (RELAYTYPE do Cfg.txt)
    RelayModel.ip_address_file -> str | None
    RelayModel.derived_bit_for(xml_type, instance_no, name) -> str | None
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from selfiles import _paths

_logger = logging.getLogger(__name__)


# Sentinela do output_bit_pattern: o bit eh o proprio physical_instance_name
# do elemento (apos strip de underscore inicial). Casos: SYMBOL, AND, OR, ...
PATTERN_IDENTITY = "IDENTITY"


@dataclass(frozen=True)
class AnalogAliasRule:
    """Regra de aliasing de nome de canal analogico.

    Alguns reles (ex.: SEL-487E) chamam o mesmo canal de jeitos diferentes na
    Relay Word / GLE versus no Fast Meter: IAS (S = winding 1) na Relay Word
    vira IA1 no Fast Meter. A regra eh aplicada via `regex.fullmatch` sobre
    o nome do SYMBOL no GLE (uppercase); se casa, `template.format(...)` gera
    o nome correspondente no Fast Meter.

    Argumentos disponiveis no template:
      - posicionais: {0} = match inteiro, {1..N} = grupos do regex (1-based
        igual a regex), na mesma ordem que `m.groups()`.
      - keyword `winding`: a letra do enrolamento (group 2 do regex) mapeada
        via `winding_map` (ex.: S -> "1"). Se group 2 nao existe ou nao casa
        com nenhuma chave, vale a letra original.
      - kwargs adicionais: grupos nomeados do regex (`(?P<x>...)`).
    """
    regex: re.Pattern
    template: str
    winding_map: tuple   # tuple[tuple[str, str], ...] (frozen-friendly)

    def apply(self, name_upper: str) -> str | None:
        m = self.regex.fullmatch(name_upper)
        if not m:
            return None
        winding_letter = ""
        try:
            winding_letter = (m.group(2) or "").upper()
        except IndexError:
            pass
        winding_num = dict(self.winding_map).get(winding_letter, winding_letter)
        try:
            return self.template.format(
                m.group(0),
                *m.groups(),
                winding=winding_num,
                **m.groupdict(),
            ).upper()
        except (KeyError, IndexError, ValueError) as e:
            _logger.warning(
                "analog alias: template %r falhou em %r: %s",
                self.template, name_upper, e,
            )
            return None


@dataclass(frozen=True)
class AnalogGroup:
    """Familia de SYMBOLs analogicos (ex.: AMV, PMV, MV, MAG).

    Reune SYMBOLs cujo physical_instance_name representa uma MEDIDA do rele
    (continuo) -- nao um bit da Relay Word. O dashboard renderiza esses blocos
    com aparencia distinta e mostra o valor inline a partir de
    fm_data['analogs'][NAME].

    `patterns` sao regex compiladas (re.Pattern) prontas para .fullmatch();
    o JSON guarda strings.
    """
    key: str               # chave canonica curta ("AMV", "MAG", "MV"...)
    label: str             # label exibido no painel ("Analog Math Variables")
    patterns: tuple        # tuple[re.Pattern, ...]


@dataclass(frozen=True)
class BlockDef:
    """Definicao por tipo de bloco. Apenas os campos consumidos hoje sao tipados;
    o restante do JSON (geometry, css_class, etc.) fica em `extra` pra leitura
    informacional ou refactor futuro."""
    key: str                              # chave canonica (ex.: "PCNDTIMER")
    gle_xml_types: tuple[str, ...]        # 1+ tipos no XML que mapeiam pra esse bloco
    kind: str                             # categoria ("timer", "latch", "gate"...)
    output_bit_pattern: str | None     # template / "IDENTITY" / None
    label_fallback: str | None = None
    # Rotulos fixos das portas (na ordem do port_index do GLE). None = bloco
    # sem rotulos pre-definidos (gates simples como AND/OR/SYMBOL).
    # Tuple vazia = JSON declarou explicitamente que nao ha rotulos.
    # Item string vazia ("") = porta existe mas o pin nao recebe glyph -- caso
    # do output de LATCH no 7xx, em que o nome do bit ja eh suficiente.
    input_sublabels: tuple[str, ...] | None = None
    output_sublabels: tuple[str, ...] | None = None
    extra: dict = field(default_factory=dict, repr=False)

    @property
    def gle_xml_type(self) -> str:
        """Compat: o primeiro tipo XML mapeado (preferencial)."""
        return self.gle_xml_types[0] if self.gle_xml_types else self.key

    def port_label(self, side: str, index: int) -> str | None:
        """Rotulo do pin para (side, index). `side` ∈ {"input","output"}.

        Retorna None se o bloco nao tem rotulos definidos para aquele lado
        (caller pode mostrar "in 0", "out 1", etc.). Retorna "" quando o
        bloco declara explicitamente que a porta nao tem glyph (porta sem
        rotulo, ainda assim presente).
        """
        seq = self.input_sublabels if side == "input" else self.output_sublabels
        if seq is None:
            return None
        if 0 <= index < len(seq):
            return seq[index]
        return None


@dataclass(frozen=True)
class IdentifierSource:
    """Onde encontrar um identificador unico do rele dentro do RDB extraido.

    `file`  -> nome do arquivo SET_*.TXT (case-insensitive) dentro do
               diretorio do rele.
    `key`   -> chave a procurar nesse arquivo (ex.: 'IPADDR', 'RID', 'MAC').
               O parsing assume linhas no formato `KEY,"VALUE"` (formato
               nativo dos SET_*.TXT do AcSELerator).
    """
    file: str
    key: str


@dataclass(frozen=True)
class RelayModel:
    """Configuracao de um modelo de rele (corresponde a um JSON)."""
    model: str
    model_aliases: tuple[str, ...]
    ip_address_file: str | None    # ex.: "set_p5.txt" (case-insensitive)
    ip_address_key: str               # ex.: "IPADDR"
    blocks: dict[str, BlockDef]       # key (canonica) -> BlockDef
    # Como o rele expoe a Relay Word:
    #   "target_region"        -> 4xx (411L/487E): banco Fast Message TARGET
    #                             eh autoridade; A5D1 traz so um subset.
    #                             Dashboard usa AsciiTargetReader.
    #   "fast_meter_digitals"  -> 7xx (751/787): Relay Word vai TODA dentro da
    #                             resposta A5D1 (numdigitalbank/digitaloffset).
    #                             Dashboard consome fm_data['digitals'] direto.
    #   "tar_digitals"         -> 3xx (311C/311L): analogicos vem do A5D1, mas
    #                             os digitais NAO. Medido num SEL-311C-1-R509:
    #                             `VIEW 1:TARGET` e `MAP 1 TARGET BL` respondem
    #                             "Invalid Command", e o A5D1 anuncia
    #                             numdigitalbank=111 mas o bloco DNA volta com
    #                             111 linhas de "*" -- nenhum bit nomeado. Os
    #                             digitais so saem por `TAR <linha>` ASCII, que
    #                             devolve 8 bits nomeados por round-trip.
    #                             Dashboard usa AsciiTargetReader.read_via_tar_rows.
    # Default = "target_region" preserva o comportamento atual pra reles que
    # ainda nao tem JSON especifico.
    fast_read: str = "target_region"
    # Index reverso construido no load: xml_type -> BlockDef.
    blocks_by_xml_type: dict[str, BlockDef] = field(default_factory=dict, repr=False)
    # Familias de SYMBOLs analogicos (ex.: AMV/PMV pra 4xx, MV/MAG/PHASE pra 7xx).
    # Ordem preservada do JSON -> determina em qual grupo cai um nome que
    # casaria com mais de um pattern (primeiro vence).
    analog_groups: tuple = field(default_factory=tuple, repr=False)
    # Regras de aliasing de nome (GLE/Relay Word -> Fast Meter). Tupla porque
    # ordem importa (primeira regra que casa vence). Vazia -> resolve_analog_name
    # eh identidade.
    analog_name_aliases: tuple = field(default_factory=tuple, repr=False)
    # Identificadores unicos adicionais (alem do IP) usados pra cruzar com SCD.
    # `relay_id` eh o RID/TID que o engenheiro coloca no rele -- normalmente
    # casa com iedName do SCD por convencao. `mac_address` eh o MAC unicast
    # quando o firmware expoe em settings (raro no 4xx).
    relay_id: IdentifierSource | None = None
    mac_address: IdentifierSource | None = None
    source_path: Path | None = field(default=None, repr=False)

    @property
    def uses_target_region(self) -> bool:
        return self.fast_read == "target_region"

    @property
    def digitals_via_tar(self) -> bool:
        """Digitais lidos por `TAR <linha>` ASCII (familia 3xx)."""
        return self.fast_read == "tar_digitals"

    @property
    def needs_ascii_reader(self) -> bool:
        """Precisa do AsciiTargetReader (mapa nome -> linha/bit da Relay Word)."""
        return self.fast_read in ("target_region", "tar_digitals")

    def identifier_sources(self) -> dict[str, IdentifierSource]:
        """Retorna {kind -> IdentifierSource} pra todos os identificadores
        unicos configurados. Os kinds canonicos sao 'ip', 'rid', 'mac'.
        Entradas com file/key ausente sao omitidas.
        """
        out: dict[str, IdentifierSource] = {}
        if self.ip_address_file:
            out["ip"] = IdentifierSource(
                file=self.ip_address_file,
                key=self.ip_address_key or "IPADDR",
            )
        if self.relay_id is not None:
            out["rid"] = self.relay_id
        if self.mac_address is not None:
            out["mac"] = self.mac_address
        return out

    def analog_group_for(self, symbol_name: str) -> AnalogGroup | None:
        """Retorna o AnalogGroup que casa com `symbol_name`, ou None se o nome
        for um bit digital (Relay Word). Usa fullmatch case-insensitive.
        Primeiro grupo cujo pattern casa vence.
        """
        if not symbol_name:
            return None
        nm = symbol_name.strip().upper()
        for grp in self.analog_groups:
            for pat in grp.patterns:
                if pat.fullmatch(nm):
                    return grp
        return None

    def is_analog_symbol(self, symbol_name: str) -> bool:
        return self.analog_group_for(symbol_name) is not None

    def resolve_analog_name(self, name: str) -> str:
        """Traduz nome do GLE/Relay Word -> nome no Fast Meter.

        Ex.: no SEL-487E, IAS (winding S) na Relay Word vira IA1 no fast_meter.
        Aplica `analog_name_aliases` em ordem; primeira regra que casa vence.
        Se nenhuma casa, retorna o proprio `name` (uppercase). Comparacao
        sempre case-insensitive.
        """
        if not name:
            return name
        nm = name.strip().upper()
        for rule in self.analog_name_aliases:
            mapped = rule.apply(nm)
            if mapped is not None:
                return mapped
        return nm

    def block_for_xml_type(self, xml_type: str) -> BlockDef | None:
        """Resolve XML type (ex.: 'PLT', 'LATCH', 'PCNDTIMER') -> BlockDef.
        None se nao houver bloco registrado pra esse tipo no modelo."""
        if not xml_type:
            return None
        return (self.blocks_by_xml_type.get(xml_type)
                or self.blocks_by_xml_type.get(xml_type.upper())
                or self.blocks.get(xml_type)
                or self.blocks.get(xml_type.upper()))

    def port_label(self, xml_type: str, side: str, index: int) -> str | None:
        """Rotulo fixo do pin (side ∈ {"input","output"}, index = port_index do GLE).

        None = sem rotulo declarado (caller decide formato fallback).
        "" = bloco declara que a porta nao tem glyph mas existe.
        """
        block = self.block_for_xml_type(xml_type)
        if block is None:
            return None
        return block.port_label(side, index)

    def derived_bit_for(
        self,
        xml_type: str,
        instance: int,
        name: str = "",
    ) -> str | None:
        """Resolve o nome do bit de saida DERIVADO de um elemento do GLE.

        Bits "derivados" sao os que nao aparecem como SYMBOL nomeado no
        diagrama, mas existem na Relay Word do rele. Ex.: um bloco
        PCNDTIMER instancia 3 nao tem um SYMBOL "PCT03Q" no GLE, mas o
        bit existe no rele.

        - Se o bloco usa template (`"PCT{instance:02d}Q"`), formata com
          `instance`. Se `instance == 0` o bit nao existe (bit derivado so
          eh estavel para physical_instance_number >= 1).
        - Se o pattern eh "IDENTITY" ou None, retorna None: esses blocos
          (SYMBOL, AND, OR, ...) ja sao cobertos por `collect_bit_names()`
          que escaneia os SYMBOLs do diagrama -- nao ha bit derivado novo.
        - Bloco nao mapeado: None.
        """
        block = self.blocks_by_xml_type.get(xml_type) or self.blocks.get(xml_type)
        if block is None or block.output_bit_pattern is None:
            return None
        pat = block.output_bit_pattern
        if pat == PATTERN_IDENTITY:
            return None
        if instance <= 0:
            return None
        try:
            return pat.format(instance=instance).upper()
        except (KeyError, ValueError, IndexError) as e:
            _logger.warning(
                "modelo %s: template invalido para %s (%r): %s",
                self.model, xml_type, pat, e,
            )
            return None


def _parse_sublabels(raw, field_name: str, source_key: str) -> tuple[str, ...] | None:
    """Aceita: None/ausente -> None. list[str] -> tuple. Outras formas -> None
    com aviso (mantem retrocompat com JSONs antigos que tinham "sublabels"
    apenas declarado e nao consumido)."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return tuple(("" if x is None else str(x)) for x in raw)
    _logger.warning(
        "block %s: campo %r deve ser lista de strings ou null, ignorando.",
        source_key, field_name,
    )
    return None


def _parse_block(canonical_key: str, raw: dict) -> BlockDef:
    consumed = {
        "kind", "gle_xml_type", "output_bit_pattern", "label_fallback",
        "sublabels", "output_sublabels",
    }
    # gle_xml_type aceita string ou lista de strings (aliases). Ex.:
    #   "PLT"                -> casa apenas XML type="PLT"
    #   ["PLT", "LATCH"]     -> casa XML "PLT" OU "LATCH" (mesmo bloco, em
    #                          firmwares diferentes do mesmo modelo).
    raw_xml = raw.get("gle_xml_type")
    xml_types: tuple[str, ...]
    if raw_xml is None:
        xml_types = (canonical_key,)
    elif isinstance(raw_xml, (list, tuple)):
        xml_types = tuple(str(x) for x in raw_xml if str(x).strip())
        if not xml_types:
            xml_types = (canonical_key,)
    else:
        xml_types = (str(raw_xml),)
    # `sublabels` historico = rotulo de INPUT (eh isso que o gle renderer faz);
    # `output_sublabels` foi adicionado depois pra simetria.
    input_subs = _parse_sublabels(raw.get("sublabels"), "sublabels", canonical_key)
    output_subs = _parse_sublabels(
        raw.get("output_sublabels"), "output_sublabels", canonical_key,
    )
    return BlockDef(
        key=canonical_key,
        gle_xml_types=xml_types,
        kind=str(raw.get("kind") or "unknown"),
        output_bit_pattern=raw.get("output_bit_pattern"),
        label_fallback=raw.get("label_fallback"),
        input_sublabels=input_subs,
        output_sublabels=output_subs,
        extra={k: v for k, v in raw.items() if k not in consumed},
    )


def _load_one(path: Path) -> RelayModel | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("nao consegui ler %s: %s", path, e)
        return None
    model = str(data.get("model") or path.stem)
    aliases = tuple(str(a) for a in (data.get("model_aliases") or []))
    ip = data.get("ip_address") or {}
    ip_file = ip.get("file")
    ip_key = ip.get("key") or "IPADDR"
    relay_id_src = _parse_identifier(data.get("relay_id"), path, "relay_id")
    mac_src = _parse_identifier(data.get("mac_address"), path, "mac_address")
    blocks_raw = data.get("blocks") or {}
    blocks: dict[str, BlockDef] = {}
    by_xml: dict[str, BlockDef] = {}
    for k, v in blocks_raw.items():
        if not isinstance(v, dict):
            continue
        bd = _parse_block(k, v)
        blocks[k.upper()] = bd
        # Registra todos os aliases de XML type. Se dois blocos colidem no
        # mesmo XML type, o ultimo definido vence; log de warn pra denunciar.
        for xml_t in bd.gle_xml_types:
            uk = xml_t.upper()
            prev = by_xml.get(uk)
            if prev is not None and prev.key != bd.key:
                _logger.warning(
                    "relay model %s: XML type %r mapeado por %r e %r; "
                    "o ultimo (%r) vence.",
                    data.get("model") or path.stem, xml_t, prev.key, bd.key, bd.key,
                )
            by_xml[uk] = bd
    fast_read = str(data.get("fast_read") or "target_region")
    _VALID_FAST_READ = ("target_region", "fast_meter_digitals", "tar_digitals")
    if fast_read not in _VALID_FAST_READ:
        _logger.warning(
            "relay model %s: fast_read %r desconhecido; usando 'target_region'. "
            "Valores validos: %s",
            data.get("model") or path.stem, fast_read, ", ".join(_VALID_FAST_READ),
        )
        fast_read = "target_region"
    analog_groups = _parse_analog_groups(data.get("analog_symbols"), path)
    analog_name_aliases = _parse_analog_name_aliases(
        data.get("analog_name_aliases"), path,
    )
    return RelayModel(
        model=model,
        model_aliases=aliases,
        ip_address_file=ip_file,
        ip_address_key=ip_key,
        blocks=blocks,
        fast_read=fast_read,
        blocks_by_xml_type=by_xml,
        analog_groups=analog_groups,
        analog_name_aliases=analog_name_aliases,
        relay_id=relay_id_src,
        mac_address=mac_src,
        source_path=path,
    )


def _parse_identifier(raw, source: Path, field_name: str) -> IdentifierSource | None:
    """Parse `{ "file": ..., "key": ... }` -> IdentifierSource. None se ausente
    ou invalido. Aceita JSON null pra explicitar 'nao disponivel pra esse modelo'.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _logger.warning(
            "relay model %s: campo %r deve ser objeto {file, key} ou null, "
            "ignorando.", source.name, field_name,
        )
        return None
    f = raw.get("file")
    k = raw.get("key")
    if not f or not k:
        _logger.warning(
            "relay model %s: campo %r exige 'file' e 'key' nao-vazios, "
            "ignorando.", source.name, field_name,
        )
        return None
    return IdentifierSource(file=str(f), key=str(k))


def _parse_analog_groups(raw, source: Path) -> tuple:
    """Parse analog_symbols list -> tuple[AnalogGroup, ...].

    Schema:
      "analog_symbols": [
        { "key": "AMV", "label": "Analog Math Variables (AMV)",
          "patterns": ["^AMV\\d+$"] },
        ...
      ]
    Patterns invalidos sao logados e descartados; grupos sem patterns validos
    sao descartados tambem.
    """
    if not raw:
        return ()
    if not isinstance(raw, (list, tuple)):
        _logger.warning("relay model %s: analog_symbols deve ser lista, ignorando.",
                        source.name)
        return ()
    out: list[AnalogGroup] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        label = str(entry.get("label") or key)
        pats_raw = entry.get("patterns") or []
        if not key or not isinstance(pats_raw, (list, tuple)):
            continue
        compiled = []
        for p in pats_raw:
            try:
                compiled.append(re.compile(str(p), re.IGNORECASE))
            except re.error as e:
                _logger.warning(
                    "relay model %s: pattern %r invalido em analog_symbols[%s]: %s",
                    source.name, p, key, e,
                )
        if not compiled:
            continue
        out.append(AnalogGroup(key=key, label=label, patterns=tuple(compiled)))
    return tuple(out)


def _parse_analog_name_aliases(raw, source: Path) -> tuple:
    """Parse analog_name_aliases -> tuple[AnalogAliasRule, ...].

    Schema:
      "analog_name_aliases": {
        "winding_map": { "S": "1", "T": "2", ... },
        "rules": [
          { "regex": "^(I[ABC])([STUWXY])$", "template": "{1}{winding}" },
          ...
        ]
      }

    Regras com regex invalido ou template ausente sao descartadas (com warn).
    Ausencia ou estrutura invalida -> retorna () sem erro.
    """
    if not raw:
        return ()
    if not isinstance(raw, dict):
        _logger.warning(
            "relay model %s: analog_name_aliases deve ser dict, ignorando.",
            source.name,
        )
        return ()
    wmap_raw = raw.get("winding_map") or {}
    if not isinstance(wmap_raw, dict):
        wmap_raw = {}
    # Normaliza pra uppercase nas chaves e str nos valores.
    wmap_items = tuple(
        (str(k).upper(), str(v)) for k, v in wmap_raw.items() if str(k).strip()
    )
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, (list, tuple)):
        return ()
    out: list[AnalogAliasRule] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            continue
        rx = entry.get("regex")
        tpl = entry.get("template")
        if not rx or not tpl:
            continue
        try:
            compiled = re.compile(str(rx), re.IGNORECASE)
        except re.error as e:
            _logger.warning(
                "relay model %s: regex invalido em analog_name_aliases %r: %s",
                source.name, rx, e,
            )
            continue
        out.append(AnalogAliasRule(
            regex=compiled,
            template=str(tpl),
            winding_map=wmap_items,
        ))
    return tuple(out)


_CACHE: dict[str, RelayModel] = {}
_ALIAS_INDEX: dict[str, RelayModel] = {}
_LOADED = False


def _normalize(key: str) -> str:
    """Normaliza pra lookup: upper + strip de espacos."""
    return (key or "").strip().upper()


def _key_variants(name: str) -> list[str]:
    """Gera variantes de busca pro RELAYTYPE.

    RELAYTYPE no Cfg.txt aparece como "SEL-411L-A". Queremos casar com chaves
    do tipo "SEL-411L-A", "411L-A", "SEL-411L", "411L". Ordem: do mais
    especifico ao mais generico.
    """
    n = _normalize(name)
    if not n:
        return []
    variants = [n]
    if n.startswith("SEL-"):
        variants.append(n[len("SEL-"):])
    elif not n.startswith("SEL-"):
        variants.append(f"SEL-{n}")
    # tira sufixos "-A", "-1" etc.: SEL-411L-A -> SEL-411L
    for base in list(variants):
        m = re.match(r"^(.*?)(-[A-Z0-9])+$", base)
        if m:
            variants.append(m.group(1))
    # dedupe preservando ordem
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def load_relay_models(models_dir: Path | None = None,
                       force: bool = False) -> dict[str, RelayModel]:
    """Carrega (e cacheia) todos os JSONs de modelo de rele.

    Sem `models_dir`, procura no overlay do host (se houver) e depois nos
    arquivos que acompanham o pacote -- nessa ordem, e o PRIMEIRO que define um
    modelo vence. E' o que deixa acrescentar um modelo em tempo de execucao sem
    que o overlay precise repetir todos os outros.
    """
    global _LOADED, _CACHE, _ALIAS_INDEX
    if _LOADED and not force:
        return _CACHE
    bases = ([Path(models_dir)] if models_dir is not None
             else _paths.data_dirs("relay_models"))
    cache: dict[str, RelayModel] = {}
    alias: dict[str, RelayModel] = {}
    for base in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.json")):
            m = _load_one(path)
            if m is None:
                continue
            # setdefault: o overlay vem primeiro e nao pode ser sobrescrito
            # pelo arquivo empacotado do mesmo modelo.
            cache.setdefault(_normalize(m.model), m)
            for a in (m.model, *m.model_aliases):
                alias.setdefault(_normalize(a), m)
    _CACHE = cache
    _ALIAS_INDEX = alias
    _LOADED = True
    return cache


def lookup(relaytype: str) -> RelayModel | None:
    """Acha o RelayModel a partir do RELAYTYPE (ex.: 'SEL-411L-A')."""
    load_relay_models()
    for key in _key_variants(relaytype):
        m = _ALIAS_INDEX.get(key)
        if m is not None:
            return m
    return None


# Casa `KEY,"value"` ou `KEY,value` (sem aspas) no formato dos SET_*.TXT.
# Strip de CIDR opcional ("/24") em IPs eh feito pelo chamador.
_SETTING_RE_TEMPLATE = r'^\s*{key}\s*,\s*"?([^",\r\n]+)"?'


def _read_setting(relay_dir: Path, src: IdentifierSource) -> str | None:
    """Le um setting `key` do arquivo `file` dentro de `relay_dir`. Case-
    insensitive no nome do arquivo e na chave. Retorna o valor cru (sem aspas).
    """
    target = src.file.lower()
    found: Path | None = None
    if not relay_dir.is_dir():
        return None
    for child in relay_dir.iterdir():
        if child.is_file() and child.name.lower() == target:
            found = child
            break
    if found is None:
        return None
    try:
        text = found.read_text(encoding="latin-1", errors="ignore")
    except OSError:
        return None
    pattern = re.compile(
        _SETTING_RE_TEMPLATE.format(key=re.escape(src.key)),
        re.IGNORECASE | re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return None
    return m.group(1).strip()


def read_identifiers(
    relay_dir: Path,
    model: str | None,
) -> dict[str, str | None]:
    """Le todos os identificadores unicos configurados pro modelo do rele.

    Retorna `{kind: value}` com chaves 'ip', 'rid', 'mac' (presentes apenas
    quando o JSON do modelo define a fonte E o arquivo existe no diretorio).
    Para IPs, tira o sufixo CIDR ('/24') do valor cru se houver.

    Se nao houver RelayModel pro `model`, retorna dict vazio.
    """
    rm = lookup(model or "")
    if rm is None:
        return {}
    out: dict[str, str | None] = {}
    for kind, src in rm.identifier_sources().items():
        val = _read_setting(relay_dir, src)
        if val and kind == "ip":
            # Aceita "192.0.2.60/24" -> "192.0.2.60"
            val = val.split("/", 1)[0].strip()
        out[kind] = val
    return out
