"""
Cruzamento entre reles de um RDB e IEDs de um SCD.

Pra que serve: durante comissionamento, voce tem um RDB (settings dos reles
gerados pelo AcSELerator QuickSet) e um SCD (configuracao IEC 61850 vinda
do Architect). Cada rele do RDB precisa corresponder a um IED do SCD --
nem sempre o nome bate, entao usamos identificadores unicos.

Chave de match (em ordem de prioridade):

  1. IP address  -- IPADDR no SET_P*.TXT do RDB versus
                    <ConnectedAP><Address><P type="IP"> do SCD. Chave
                    primaria, casa quase sempre apos o rele ter recebido
                    o IP definitivo do projeto.

  2. RID (Relay ID) -- RID no SET_G*/SET_1.txt do RDB versus IED@name no
                       SCD. Fallback usado quando o rele ainda nao foi
                       comissionado (IP factory-default tipo 192.168.x.x)
                       mas o engenheiro ja preencheu o RID.

Os arquivos/keys onde cada identificador mora dependem do modelo do rele
(4xx usa SET_P5.TXT/SET_G1.TXT, 7xx usa SET_P1.TXT/SET_1.TXT) e ficam
declarados em `data/relay_models/<MODEL>.json`. Adicionar suporte a
um modelo novo eh so adicionar o JSON.

API publica:

    compare_rdb_to_scd(rdb_path, scd_path) -> MatchReport
    compare_relays_to_scd(relays, extract_dir, scd_path) -> MatchReport
    MatchReport.to_dict() / .print_summary()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from selfiles import rdb as rdb_loader
from selfiles.models import relay_models
from selfiles.scl import read as scd_loader
from selfiles.scl.read import IedInfo

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RelayIdentifiers:
    """Identificadores extraidos de um rele do RDB."""
    name: str                       # nome da pasta no RDB
    model: str | None            # ex.: "487E-3" do RELAYTYPE
    ip: str | None
    rid: str | None
    mac: str | None


@dataclass(frozen=True)
class Match:
    """Match RDB<->SCD com diagnostico de consistencia."""
    rdb_name: str
    scd_name: str
    matched_by: str                 # "ip" | "rid"
    ip: str | None
    rid: str | None
    rdb_model: str | None
    scd_type: str | None
    model_consistent: bool          # rdb_model casa com scd_type?
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "rdb_name": self.rdb_name,
            "scd_name": self.scd_name,
            "matched_by": self.matched_by,
            "ip": self.ip,
            "rid": self.rid,
            "rdb_model": self.rdb_model,
            "scd_type": self.scd_type,
            "model_consistent": self.model_consistent,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class UnmatchedRdb:
    rdb_name: str
    model: str | None
    ip: str | None
    rid: str | None
    reason: str

    def to_dict(self) -> dict:
        return {
            "rdb_name": self.rdb_name,
            "model": self.model,
            "ip": self.ip,
            "rid": self.rid,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class UnmatchedScd:
    scd_name: str
    ip: str | None
    scd_type: str | None

    def to_dict(self) -> dict:
        return {
            "scd_name": self.scd_name,
            "ip": self.ip,
            "scd_type": self.scd_type,
        }


@dataclass
class MatchReport:
    matched: list[Match] = field(default_factory=list)
    unmatched_rdb: list[UnmatchedRdb] = field(default_factory=list)
    unmatched_scd: list[UnmatchedScd] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "matched": [m.to_dict() for m in self.matched],
            "unmatched_rdb": [u.to_dict() for u in self.unmatched_rdb],
            "unmatched_scd": [u.to_dict() for u in self.unmatched_scd],
            "warnings": list(self.warnings),
        }

    def print_summary(self) -> None:
        print(f"=== RDB <-> SCD match ({len(self.matched)} matched, "
              f"{len(self.unmatched_rdb)} RDB-only, "
              f"{len(self.unmatched_scd)} SCD-only) ===\n")
        if self.matched:
            print(f"{'RDB relay':<35} {'IP':<16} {'SCD iedName':<32} {'by':<4} model?")
            print("-" * 100)
            for m in self.matched:
                flag = "ok" if m.model_consistent else "MISMATCH"
                print(f"{m.rdb_name:<35} {(m.ip or '-'):<16} "
                      f"{m.scd_name:<32} {m.matched_by:<4} {flag}")
            print()
        if self.unmatched_rdb:
            print(f"--- RDB only ({len(self.unmatched_rdb)}) ---")
            for u in self.unmatched_rdb:
                bits = []
                if u.ip:
                    bits.append(f"ip={u.ip}")
                if u.rid:
                    bits.append(f"rid={u.rid}")
                if u.model:
                    bits.append(f"model={u.model}")
                meta = ", ".join(bits) or "(no identifiers)"
                print(f"  {u.rdb_name:<35} {meta}  [{u.reason}]")
            print()
        if self.unmatched_scd:
            print(f"--- SCD only ({len(self.unmatched_scd)}) ---")
            for s in self.unmatched_scd:
                print(f"  {s.scd_name:<35} ip={s.ip or '-'}  type={s.scd_type or '-'}")
            print()
        if self.warnings:
            print(f"--- warnings ({len(self.warnings)}) ---")
            for w in self.warnings:
                print(f"  - {w}")


def _normalize_model(s: str | None) -> str:
    """`SEL-487E-3` / `SEL_487E_A` / `487E` -> `487E`. Vazio quando None.

    Tolera os dois separadores que aparecem na pratica: `-` no RELAYTYPE do
    RDB (`SEL-487E-3`) e `_` no atributo `type` do SCD (`SEL_487E_A`). Tira
    o prefixo `SEL` e, se sobrou um sufixo curto alfanumerico (revisao tipo
    `-3` / `_A`), tira tambem -- `487E-3` e `487E_A` ambos viram `487E`.
    """
    if not s:
        return ""
    t = s.strip().upper().replace("_", "-")
    if t.startswith("SEL-"):
        t = t[4:]
    # corta sufixo "-N" curto (revisao) -- "487E-3" -> "487E", "411L-A" -> "411L"
    if "-" in t:
        head, tail = t.rsplit("-", 1)
        if tail.isalnum() and len(tail) <= 2:
            t = head
    return t


def _model_consistent(rdb_model: str | None, scd_type: str | None) -> bool:
    a = _normalize_model(rdb_model)
    b = _normalize_model(scd_type)
    if not a or not b:
        # sem dado de um dos lados, nao da pra contradizer
        return True
    return a == b


def _collect_rdb_identifiers(
    relays: list[rdb_loader.RelayEntry],
    extract_dir: Path,
) -> list[RelayIdentifiers]:
    out: list[RelayIdentifiers] = []
    for r in relays:
        relay_dir = extract_dir / "Relays" / r.name
        ids = relay_models.read_identifiers(relay_dir, r.model)
        out.append(RelayIdentifiers(
            name=r.name,
            model=r.model,
            ip=ids.get("ip") or r.ip,    # fallback pro IP que rdb_loader ja leu
            rid=ids.get("rid"),
            mac=ids.get("mac"),
        ))
    return out


def compare_relays_to_scd(
    relays: list[rdb_loader.RelayEntry],
    extract_dir: str | Path,
    scd_path: str | Path,
) -> MatchReport:
    """Versao que aceita a lista de reles ja-extraidos.

    Util quando voce ja tem um `RdbInfo` em maos e nao quer re-extrair.
    `extract_dir` deve apontar pro diretorio que contem `Relays/<relay>/`.
    """
    extract_dir = Path(extract_dir)
    scd_path = Path(scd_path)

    rdb_ids = _collect_rdb_identifiers(relays, extract_dir)
    ieds = scd_loader.load_scd(scd_path)
    by_ip = scd_loader.index_by_ip(ieds)
    by_name = scd_loader.index_by_name(ieds)

    report = MatchReport()
    if not ieds:
        report.warnings.append(f"SCD vazio ou ilegivel: {scd_path}")
        for r in rdb_ids:
            report.unmatched_rdb.append(UnmatchedRdb(
                rdb_name=r.name, model=r.model, ip=r.ip, rid=r.rid,
                reason="SCD vazio",
            ))
        return report

    used_scd_names: set[str] = set()
    for r in rdb_ids:
        ied: IedInfo | None = None
        matched_by = ""
        notes: list[str] = []

        # 1) match por IP
        #
        # O `used_scd_names` vale aqui tanto quanto no ramo do RID: o
        # casamento e' um-pra-um, e `unmatched_scd` la embaixo e' montado
        # partindo desse conjunto. Sem a guarda, dois reles do RDB com o mesmo
        # IP casavam os dois com o MESMO IED e o relatorio mostrava dois pares
        # confirmados, escondendo a duplicata em vez de denuncia-la. Pasta de
        # rele copiada dentro do RDB -- ou um IPADDR que ninguem trocou depois
        # de clonar os ajustes -- e' o jeito normal de cair nisso, e e'
        # exatamente o erro de comissionamento que esta ferramenta existe pra
        # mostrar.
        if r.ip and r.ip in by_ip and by_ip[r.ip].name not in used_scd_names:
            ied = by_ip[r.ip]
            matched_by = "ip"
        # 2) fallback: match por RID == iedName (case-insensitive)
        if ied is None and r.rid:
            cand = by_name.get(r.rid.upper())
            if cand and cand.name not in used_scd_names:
                ied = cand
                matched_by = "rid"
                if r.ip and cand.ip and r.ip != cand.ip:
                    notes.append(
                        f"RID casou ({r.rid}) mas IPs divergem: "
                        f"RDB={r.ip} SCD={cand.ip}"
                    )
                elif r.ip and not cand.ip:
                    notes.append("RID casou mas SCD nao tem IP pro IED.")
                elif not r.ip:
                    notes.append("RDB nao tem IP; matched so pelo RID.")

        if ied is None:
            reason = _no_match_reason(r, by_ip, by_name)
            report.unmatched_rdb.append(UnmatchedRdb(
                rdb_name=r.name, model=r.model, ip=r.ip, rid=r.rid,
                reason=reason,
            ))
            continue

        used_scd_names.add(ied.name)
        ok = _model_consistent(r.model, ied.relay_type)
        if not ok:
            notes.append(
                f"modelo divergente: RDB={r.model!r} vs SCD={ied.relay_type!r}"
            )
        report.matched.append(Match(
            rdb_name=r.name,
            scd_name=ied.name,
            matched_by=matched_by,
            ip=r.ip,
            rid=r.rid,
            rdb_model=r.model,
            scd_type=ied.relay_type,
            model_consistent=ok,
            notes=tuple(notes),
        ))

    for ied in ieds:
        if ied.name in used_scd_names:
            continue
        report.unmatched_scd.append(UnmatchedScd(
            scd_name=ied.name, ip=ied.ip, scd_type=ied.relay_type,
        ))
    return report


def _no_match_reason(
    r: RelayIdentifiers,
    by_ip: dict[str, IedInfo],
    by_name: dict[str, IedInfo],
) -> str:
    bits = []
    if not r.ip and not r.rid:
        return "sem IP nem RID legiveis no RDB"
    if r.ip and r.ip not in by_ip:
        bits.append(f"IP {r.ip} nao existe em nenhum ConnectedAP do SCD")
    if r.rid and r.rid.upper() not in by_name:
        bits.append(f"RID {r.rid!r} nao bate com nenhum IED@name")
    if not bits:
        bits.append("identificadores presentes mas ja consumidos por outro match")
    return "; ".join(bits)


def compare_rdb_to_scd(
    rdb_path: str | Path,
    scd_path: str | Path,
    base_dir: str | Path | None = None,
) -> MatchReport:
    """Pipeline completo: extrai o RDB (ou reaproveita extracao em cache via
    sha256) e compara com o SCD.

    A extracao vai pro cache por conteudo (`cache/rdb/<sha256>/`), e nao mais
    pra um diretorio ao lado do arquivo. `base_dir`, quando dado, vira a RAIZ
    alternativa desse cache -- util pra rodar isolado fora da web. Aceita
    strings ou Path.
    """
    rdb_path = Path(rdb_path)
    data = rdb_path.read_bytes()
    info = rdb_loader.process_upload(
        data, rdb_path.name,
        cache_root=Path(base_dir) if base_dir is not None else None,
    )
    return compare_relays_to_scd(info.relays, info.extract_dir, scd_path)
