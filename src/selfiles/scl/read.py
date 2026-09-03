"""
Leitura de arquivos SCD (IEC 61850 Substation Configuration Description).

Um SCD eh um XML com a estrutura:

    <SCL>
      <Communication>
        <SubNetwork>
          <ConnectedAP iedName="..." apName="..."> <Address>
            <P type="IP">192.0.2.60</P>
            ...
          </Address> </ConnectedAP>
          ...
        </SubNetwork>
      </Communication>
      <IED name="QPC1_TR1_UPC1" type="SEL_487E" manufacturer="SEL" ...>
        ...
      </IED>
      ...
    </SCL>

Este modulo extrai por IED os campos uteis pra cruzar com um RDB:
  - name           (iedName / IED@name)
  - ip             (primeiro ConnectedAP do IED com <P type="IP">)
  - relay_type     (atributo `type` do <IED>, ex.: "SEL_487E")
  - manufacturer   (atributo `manufacturer` do <IED>, ex.: "SEL")
  - description    (atributo `desc` do <IED>)
  - config_version (atributo `configVersion`)

O parsing usa `xml.etree.ElementTree` namespace-aware. Falha graciosamente
em XML invalido (retorna lista vazia + log).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from selfiles.scl.mms_tables import da_rank, parse_saddr

_logger = logging.getLogger(__name__)

# Namespace padrao do SCL/IEC 61850-6.
_SCL_NS = "http://www.iec.ch/61850/2003/SCL"
_NS = {"scl": _SCL_NS}


@dataclass(frozen=True)
class IedInfo:
    """Snapshot de um IED como ele aparece no SCD."""
    name: str
    ip: str | None
    relay_type: str | None
    manufacturer: str | None
    description: str | None
    config_version: str | None


def _strip_ns(tag: str) -> str:
    """{ns}LocalName -> LocalName."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_local(root: ET.Element, local_name: str):
    """Itera elementos com nome local == local_name, ignorando o namespace.
    Util porque alguns SCDs gerados manualmente nao declaram namespace.
    """
    for el in root.iter():
        if _strip_ns(el.tag) == local_name:
            yield el


def _collect_ip_by_ied(root: ET.Element) -> dict[str, str]:
    """Le <Communication> e retorna {iedName: primeiro IP encontrado}."""
    out: dict[str, str] = {}
    for ap in _iter_local(root, "ConnectedAP"):
        ied = ap.attrib.get("iedName") or ap.attrib.get("iedname")
        if not ied or ied in out:
            continue
        for p in _iter_local(ap, "P"):
            ptype = (p.attrib.get("type") or "").upper()
            if ptype == "IP" and (p.text or "").strip():
                out[ied] = p.text.strip()
                break
    return out


def load_scd(scd_path: Path) -> list[IedInfo]:
    """Parsa um SCD e retorna a lista de IEDs com seus campos identificadores.

    Retorna lista vazia (com log) em caso de erro de IO/parsing.
    """
    p = Path(scd_path)
    if not p.is_file():
        _logger.warning("SCD nao encontrado: %s", p)
        return []
    try:
        tree = ET.parse(str(p))
    except (OSError, ET.ParseError) as e:
        _logger.warning("erro lendo SCD %s: %s", p, e)
        return []
    root = tree.getroot()
    ip_by_ied = _collect_ip_by_ied(root)
    ieds: list[IedInfo] = []
    seen: set[str] = set()
    for el in _iter_local(root, "IED"):
        name = el.attrib.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        ieds.append(IedInfo(
            name=name,
            ip=ip_by_ied.get(name),
            relay_type=el.attrib.get("type"),
            manufacturer=el.attrib.get("manufacturer"),
            description=el.attrib.get("desc"),
            config_version=el.attrib.get("configVersion"),
        ))
    return ieds


def index_by_ip(ieds: list[IedInfo]) -> dict[str, IedInfo]:
    """{ip -> IedInfo} (apenas IEDs com IP). Em caso de IP duplicado, o
    primeiro vence -- duplicidades sao logadas como warning.
    """
    out: dict[str, IedInfo] = {}
    for ied in ieds:
        if not ied.ip:
            continue
        if ied.ip in out:
            _logger.warning(
                "SCD: IP duplicado %s em IEDs %r e %r",
                ied.ip, out[ied.ip].name, ied.name,
            )
            continue
        out[ied.ip] = ied
    return out


def index_by_name(ieds: list[IedInfo]) -> dict[str, IedInfo]:
    """{iedName.upper() -> IedInfo}. Lookup case-insensitive."""
    return {ied.name.upper(): ied for ied in ieds if ied.name}


# -----------------------------------------------------------------------------
# GOOSE / VLAN extraction
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class GseAddress:
    """Endereco GOOSE de um <GSE> sob <ConnectedAP iedName=...>.

    Identifica univocamente um GOOSE Control Block via (publisher_ied, ld_inst,
    cb_name). Os campos de Address vem do bloco <Address><P type=...>.
    """
    publisher_ied: str          # iedName do <ConnectedAP> que contem o <GSE>
    ld_inst: str                # atributo `ldInst` do <GSE>
    cb_name: str                # atributo `cbName` do <GSE>
    mac_address: str | None  # P type="MAC-Address"
    appid: str | None        # P type="APPID"
    vlan_id: str | None      # P type="VLAN-ID" (string -- hex/decimal varia)
    vlan_priority: str | None  # P type="VLAN-PRIORITY"


@dataclass(frozen=True)
class GooseSubscription:
    """Uma assinatura de GOOSE feita por um IED (um <ExtRef serviceType="GOOSE">).

    Aponta pro GOOSE Control Block (publisher_ied, src_ld_inst, src_cb_name).
    Pode ou nao resolver pra um GseAddress no <Communication> -- subscricoes
    sem GSE correspondente sao mantidas com `address=None` pra diagnostico.
    """
    publisher_ied: str
    src_ld_inst: str
    src_cb_name: str
    desc: str | None = None         # `desc` do ExtRef (informativo)
    int_addr: str | None = None     # `intAddr` do ExtRef (informativo)


def _gse_key(publisher_ied: str, ld_inst: str, cb_name: str) -> tuple[str, str, str]:
    """Chave canonica de um GOOSE Control Block."""
    return (publisher_ied, ld_inst, cb_name)


def extract_gse_communication_map(scd_path: Path) -> dict[tuple[str, str, str], GseAddress]:
    """Le um SCD e retorna {(publisher_ied, ld_inst, cb_name): GseAddress}.

    Cada <GSE ldInst=... cbName=...> sob <ConnectedAP iedName=...> vira uma
    entrada com seu MAC/APPID/VLAN-ID/VLAN-PRIORITY (campos opcionais ficam
    None quando ausentes).
    """
    p = Path(scd_path)
    out: dict[tuple[str, str, str], GseAddress] = {}
    if not p.is_file():
        _logger.warning("SCD nao encontrado: %s", p)
        return out
    try:
        tree = ET.parse(str(p))
    except (OSError, ET.ParseError) as e:
        _logger.warning("erro lendo SCD %s: %s", p, e)
        return out
    root = tree.getroot()
    for ap in _iter_local(root, "ConnectedAP"):
        publisher = ap.attrib.get("iedName") or ap.attrib.get("iedname") or ""
        if not publisher:
            continue
        for gse in _iter_local(ap, "GSE"):
            ld_inst = gse.attrib.get("ldInst") or ""
            cb_name = gse.attrib.get("cbName") or ""
            if not cb_name:
                continue
            params: dict[str, str] = {}
            for p_el in _iter_local(gse, "P"):
                ptype = (p_el.attrib.get("type") or "").upper()
                if ptype and (p_el.text or "").strip():
                    params[ptype] = p_el.text.strip()
            out[_gse_key(publisher, ld_inst, cb_name)] = GseAddress(
                publisher_ied=publisher,
                ld_inst=ld_inst,
                cb_name=cb_name,
                mac_address=params.get("MAC-ADDRESS"),
                appid=params.get("APPID"),
                vlan_id=params.get("VLAN-ID"),
                vlan_priority=params.get("VLAN-PRIORITY"),
            )
    return out


def extract_goose_subscriptions_by_ied(
    scd_path: Path,
) -> dict[str, list[GooseSubscription]]:
    """Le um SCD e retorna {ied_name: [GooseSubscription, ...]}.

    Para cada <IED>, pega todos os <ExtRef serviceType="GOOSE"> que tenham
    `iedName` (publisher) e `srcCBName` (control block) preenchidos. ExtRefs
    sem publisher/cb sao ignorados (sao templates vazios, comuns em SCDs
    exportados antes de algumas conexoes serem fechadas).

    Subscricoes duplicadas para o mesmo (publisher, ldInst, cbName) sao
    deduplicadas (mantemos a primeira ocorrencia -- as outras sao apenas
    intAddrs adicionais do mesmo dataset).
    """
    p = Path(scd_path)
    out: dict[str, list[GooseSubscription]] = {}
    if not p.is_file():
        _logger.warning("SCD nao encontrado: %s", p)
        return out
    try:
        tree = ET.parse(str(p))
    except (OSError, ET.ParseError) as e:
        _logger.warning("erro lendo SCD %s: %s", p, e)
        return out
    root = tree.getroot()
    for ied_el in _iter_local(root, "IED"):
        ied_name = ied_el.attrib.get("name") or ""
        if not ied_name:
            continue
        seen: set[tuple[str, str, str]] = set()
        subs: list[GooseSubscription] = []
        for ext in _iter_local(ied_el, "ExtRef"):
            stype = (ext.attrib.get("serviceType") or "").upper()
            if stype != "GOOSE":
                continue
            pub = (ext.attrib.get("iedName") or "").strip()
            cb = (ext.attrib.get("srcCBName") or "").strip()
            if not pub or not cb:
                # Template/placeholder ExtRef -- nao representa uma assinatura real.
                continue
            ld = (ext.attrib.get("srcLDInst") or "").strip()
            key = _gse_key(pub, ld, cb)
            if key in seen:
                continue
            seen.add(key)
            subs.append(GooseSubscription(
                publisher_ied=pub,
                src_ld_inst=ld,
                src_cb_name=cb,
                desc=(ext.attrib.get("desc") or None),
                int_addr=(ext.attrib.get("intAddr") or None),
            ))
        if subs:
            out[ied_name] = subs
    return out


# -- sAddr: o nome da Relay Word dentro do SCL ------------------------------
#
# A SEL grava o nome do bit em `sAddr="db:NOME"` no DAI. Esse atributo e' de
# SCL e o rele NAO o serve por MMS, entao esta e' a unica ponte entre o nome
# que o GLE desenha e o item MMS que o rele responde.
#
# O FC nao esta aqui: ele mora no DA do DOType, dentro de DataTypeTemplates.
# Nao resolvemos essa cadeia -- quem da o FC e' o proprio rele, casando
# `LN$*$DO$DA` contra o GetLogicalDeviceDirectory. Ver `web/glv/mms_map.py`.

@dataclass(frozen=True)
class ScdPoint:
    """Onde um bit da Relay Word mora no modelo 61850, menos o FC."""
    bit: str
    ld_inst: str
    ln: str          # prefix + lnClass + inst, como o MMS soletra
    do: str
    da: str          # 'stVal', ou 'Oper.ctlVal' quando vem de um SDI
    # Como tirar ESTE bit do valor do ponto, quando o ponto carrega mais de um
    # (`sAddr="db:52A|52B?0:1:2:3"` num DPS). `None` num endereco liso, que e'
    # a esmagadora maioria -- 127.225 dos 132.250 do corpus. Ver
    # `mms_tables.parse_saddr` / `mms_tables.decode_bit`.
    rule: object | None = None


def _ln_name(ln: ET.Element) -> str:
    if _strip_ns(ln.tag) == "LN0":
        return "LLN0"
    return (f'{ln.get("prefix") or ""}{ln.get("lnClass") or ""}'
            f'{ln.get("inst") or ""}')


def _walk_dais(node: ET.Element, trail: list):
    """Rende (caminho_do_da, elemento) para cada DAI sob um DOI, entrando em SDI."""
    for child in node:
        tag = _strip_ns(child.tag)
        if tag == "DAI":
            yield ".".join(trail + [child.get("name") or ""]), child
        elif tag == "SDI":
            yield from _walk_dais(child, trail + [child.get("name") or ""])


def _type_index(root: ET.Element) -> tuple:
    """`DataTypeTemplates` -> (`{lnType: {DO: DOType}}`, `{DOType: {DA: fc}}`).

    So' os `DA` de PRIMEIRO nivel entram no segundo indice: o FC mora ali. Um
    `Oper.ctlVal` herda o `CO` do proprio `Oper`, que e' como a IEC 61850
    define -- o functional constraint e' do DA raiz, e o que desce por dentro
    dele desce junto.
    """
    dos_by_lntype: dict = {}
    fcs_by_dotype: dict = {}
    for tpl in _iter_local(root, "DataTypeTemplates"):
        for lnt in _iter_local(tpl, "LNodeType"):
            dos_by_lntype[lnt.get("id")] = {
                do.get("name"): do.get("type")
                for do in lnt if _strip_ns(do.tag) == "DO"}
        for dot in _iter_local(tpl, "DOType"):
            fcs_by_dotype[dot.get("id")] = {
                da.get("name"): da.get("fc")
                for da in dot if _strip_ns(da.tag) == "DA"}
    return dos_by_lntype, fcs_by_dotype


def sel_da_fcs(scd_path: Path) -> dict:
    """`{IED: {(ld_inst, ln, do, da): fc}}`, resolvido nos DataTypeTemplates.

    O caminho VIVO nao usa isto e nao deve usar: la o FC vem do proprio rele,
    casando `LN$*$DO$DA` contra o `GetLogicalDeviceDirectory`, o que resolve o
    FC e confere a entrada de uma vez so' (ver `web/glv/mms_map.py`).

    Aqui nao ha' rele: a tabela de fabrica em `data/mms_map/` e' gerada
    offline a partir dos ICD, e o item precisa do FC gravado dentro dele.
    Medido nos 146 ICD do corpus, os 2.030 enderecos decorados caem todos em
    `ST` -- resolver mesmo assim, em vez de gravar `ST` na marra, e' o que faz
    um ICD futuro que discorde falhar alto em vez de gerar um item errado.

    Um DA que nao resolve fica FORA do dicionario. Chutar um FC produz um item
    que o rele nao serve, e ai o bit some calado la' na frente.
    """
    root = ET.parse(scd_path).getroot()
    dos_by_lntype, fcs_by_dotype = _type_index(root)
    out: dict = {}
    for ied in _iter_local(root, "IED"):
        fcs: dict = {}
        for ldev in _iter_local(ied, "LDevice"):
            ld_inst = ldev.get("inst") or ""
            for ln in list(_iter_local(ldev, "LN0")) + list(_iter_local(ldev, "LN")):
                ln_name = _ln_name(ln)
                dos = dos_by_lntype.get(ln.get("lnType"), {})
                for doi in _iter_local(ln, "DOI"):
                    do = doi.get("name") or ""
                    by_da = fcs_by_dotype.get(dos.get(do), {})
                    for da_path, _dai in _walk_dais(doi, []):
                        # O FC e' do DA RAIZ: `Oper.ctlVal` e' `CO` porque
                        # `Oper` e' `CO`.
                        fc = by_da.get(da_path.split(".")[0])
                        if fc:
                            fcs[(ld_inst, ln_name, do, da_path)] = fc
        out[ied.get("name") or ""] = fcs
    return out


def sel_short_addresses(scd_path: Path) -> dict:
    """`{nome_do_IED: {NOME_DO_BIT: ScdPoint}}` para todo sAddr="db:...".

    Um nome aparece varias vezes no mesmo IED: o mesmo bit sai no `stVal` do
    lado ST e no `Oper.ctlVal` do lado CO, por exemplo. Quem fica e' o de
    MENOR `da_rank` -- status booleano primeiro, status enumerado DECORADO
    depois, comando por ultimo -- e o primeiro da ordem do documento
    desempata.

    Nao da' pra deixar isso pro `fc_rank` mais adiante: ele escolhe entre FCs
    de UM MESMO da, e nesse ponto o candidato de status ja' teria sido
    jogado fora. Medido em `samples/substation_demo.scd`: com o first-wins
    puro, `LOCSTA` e `IPRST` (entre 87 pontos do IED `QPC1_LT2_UPC1`)
    resolviam pro `CFG/LLN0.LocSta.Oper.ctlVal`, ou seja, o GLV leria o
    comando em vez do estado.

    Um `sAddr` pode enderecar DOIS bits num ponto so' -- `db:52A|52B?0:1:2:3`
    num `Pos$stVal`, cujo Dbpos codifica os dois contatos auxiliares. Cada
    nome vira um `ScdPoint` proprio, com a `rule` que diz como tirar o seu bit
    do valor lido; a gramatica e a invariante `len(alt) == 2**len(nomes)` vivem
    em `mms_tables.parse_saddr`, e uma forma que a quebre e' descartada em vez
    de chutada. Antes disto a chave virava a string literal
    `52A|52B?0:1:2:3` e a forma inteira sumia calada: 55 dos 7.524 bits
    desenhados dos 25 reles da subestacao, todos posicao de disjuntor ou de
    seccionadora.
    """
    root = ET.parse(scd_path).getroot()
    out: dict = {}
    for ied in _iter_local(root, "IED"):
        name = ied.get("name") or ""
        bits: dict = {}
        best: dict = {}          # BIT -> rank do candidato que esta valendo
        for ldev in _iter_local(ied, "LDevice"):
            ld_inst = ldev.get("inst") or ""
            for ln in list(_iter_local(ldev, "LN0")) + list(_iter_local(ldev, "LN")):
                ln_name = _ln_name(ln)
                for doi in _iter_local(ln, "DOI"):
                    do = doi.get("name") or ""
                    for da_path, dai in _walk_dais(doi, []):
                        spec = parse_saddr(dai.get("sAddr") or "")
                        if spec is None:
                            continue
                        decorated = spec.alternatives is not None
                        rank = da_rank(da_path, decorated=decorated)
                        for i, bit in enumerate(spec.names):
                            if bit in best and rank >= best[bit]:
                                continue
                            best[bit] = rank
                            bits[bit] = ScdPoint(
                                bit=bit, ld_inst=ld_inst, ln=ln_name,
                                do=do, da=da_path, rule=spec.rule_for(i))
        out[name] = bits
    return out
