"""
Leitura/extracao de arquivos RDB (AcSELerator QuickSet) usando olefile.

Um RDB e um OLE compound document com a estrutura:

    Relays/
        <relay name>/
            Misc/
                GL1.gle, GL2.gle, ...
                Cfg.txt, Device.txt, ...
            SET_*.TXT, BAY_SCREEN.TXT, ...

Este modulo recebe o conteudo bruto de um RDB enviado pelo usuario e extrai
todos os streams preservando a hierarquia, listando os reles com seus arquivos
GLE.

A extracao mora num cache chaveado pelo CONTEUDO (`cache/rdb/<sha256>/`, ver
`selfiles.rdb_cache`), e nao mais num diretorio por ferramenta chaveado
pelo nome. Dois arquivos iguais sao o mesmo arquivo: colisao por nome deixou de
existir, e o reaproveitamento vale entre sessoes e entre reinicios.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import olefile

from selfiles import _paths, rdb_cache
from selfiles.models import relay_models

# Permite letras/digitos/ponto/hifen/underscore/espaco em nomes que viram
# caminhos de filesystem. Tudo o resto vira "_".
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._\- ]")


# Fallback por familia, usado SO quando nao ha JSON em `relay_models/` pro
# modelo especifico. A fonte de verdade eh `data/relay_models/<MODEL>.json`
# (campo ip_address.file). Aqui ficam defaults conservadores baseados em onde
# o IPADDR costuma aparecer pra cada familia SEL.
RELAY_FAMILY_IP_FILE: dict[str, str] = {
    "3xx": "set_p5.txt",
    "4xx": "set_p5.txt",   # SEL-411L, SEL-487E, etc.
    "7xx": "set_p1.txt",   # SEL-751, SEL-787, etc.
}

_RELAYTYPE_RE = re.compile(r"RELAYTYPE\s*=\s*(.+)", re.IGNORECASE)
_MODEL_RE = re.compile(r"SEL-?\s*([0-9][0-9A-Za-z\-]*)", re.IGNORECASE)
# Casa "IPADDR,..." mas NAO "IPADDRE,...". Captura o IPv4 (sem mascara CIDR).
_IPADDR_RE = re.compile(
    r'^\s*IPADDR\s*,\s*"?(\d{1,3}(?:\.\d{1,3}){3})',
    re.IGNORECASE | re.MULTILINE,
)


def _read_relay_model(relay_dir: Path) -> str | None:
    cfg = relay_dir / "Misc" / "Cfg.txt"
    if not cfg.is_file():
        return None
    try:
        text = cfg.read_text(encoding="latin-1", errors="ignore")
    except OSError:
        return None
    m = _RELAYTYPE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()        # ex.: "SEL-487E-3"
    mo = _MODEL_RE.search(raw)
    return mo.group(1) if mo else raw


def _family_from_model(model: str | None) -> str | None:
    """487E -> '4xx', 751 -> '7xx'. Retorna None se nao for digito reconhecido."""
    if not model:
        return None
    first = model.lstrip().lstrip("-")[:1]
    return f"{first}xx" if first.isdigit() else None


def _find_case_insensitive(directory: Path, filename: str) -> Path | None:
    target = filename.lower()
    if not directory.is_dir():
        return None
    for child in directory.iterdir():
        if child.is_file() and child.name.lower() == target:
            return child
    return None


def _resolve_ip_file(model: str | None) -> tuple[str | None, str | None]:
    """Resolve (filename, key) pra achar o IPADDR. Prioriza JSON do modelo;
    cai pra fallback por familia se nao houver."""
    rm = relay_models.lookup(model or "")
    if rm is not None and rm.ip_address_file:
        return rm.ip_address_file, (rm.ip_address_key or "IPADDR")
    fam = _family_from_model(model)
    if fam:
        f = RELAY_FAMILY_IP_FILE.get(fam)
        if f:
            return f, "IPADDR"
    return None, None


def _read_relay_ip(relay_dir: Path, model: str | None) -> str | None:
    fname, _ = _resolve_ip_file(model)
    if not fname:
        return None
    fpath = _find_case_insensitive(relay_dir, fname)
    if fpath is None:
        return None
    try:
        text = fpath.read_text(encoding="latin-1", errors="ignore")
    except OSError:
        return None
    m = _IPADDR_RE.search(text)
    return m.group(1) if m else None


def _relay_meta(extract_dir: Path, relay_name: str) -> tuple[str | None, str | None]:
    relay_dir = extract_dir / "Relays" / relay_name
    model = _read_relay_model(relay_dir)
    ip = _read_relay_ip(relay_dir, model)
    return model, ip


def sanitize_name(name: str) -> str:
    s = (name or "").strip()
    s = _UNSAFE_CHARS.sub("_", s)
    return s or "unknown"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: Leitura de upload em pedacos. 1 MB e' grande o bastante pra que o custo por
#: chamada suma num arquivo de 140 MB e pequeno o bastante pra caber folgado na
#: memoria de um notebook de campo com varios uploads ao mesmo tempo.
UPLOAD_CHUNK = 1 << 20


def stream_to_file(source, length: int, dest: Path,
                   on_progress=None, chunk_size: int = UPLOAD_CHUNK) -> str:
    """Copia `length` bytes de `source` pra `dest` e devolve o sha256.

    Um RDB tem de 40 a 140 MB e o teto e' 500. Lendo tudo de uma vez, esse
    tamanho inteiro ficava residente -- duas vezes, na verdade, porque o hash
    percorria os mesmos bytes e a gravacao passava por eles de novo. Aqui a
    memoria e' um pedaco de cada vez, o hash cresce junto com a leitura e o
    arquivo ja vai pro disco: as tres passadas viraram uma.

    Le exatamente `length` bytes -- um `read(n)` de socket pode voltar curto,
    entao o laco insiste. Levanta `ValueError` se a origem acabar antes.

    `on_progress(lidos, total, etapa)` sai a cada pedaco, entao a barra do
    cliente anda durante o recebimento em vez de congelar em "processando".
    """
    if length <= 0:
        raise ValueError("arquivo vazio")
    h = hashlib.sha256()
    read_total = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while read_total < length:
            chunk = source.read(min(chunk_size, length - read_total))
            if not chunk:
                raise ValueError(
                    f"upload interrompido: {read_total} de {length} bytes")
            out.write(chunk)
            h.update(chunk)
            read_total += len(chunk)
            if on_progress is not None:
                on_progress(read_total, length, "Recebendo arquivo")
    return h.hexdigest()


def short_sha(sha256: str) -> str:
    """The 12-char prefix every screen shows instead of a 64-char digest.

    One definition because it is an IDENTIFIER, not a display detail: the DNP
    map editor keys its per-session edits by it and the Settings Compare keys
    its RDB registry by it, so two tools disagreeing on the length would be
    two tools disagreeing on which RDB is which.
    """
    return sha256[:12]


def sha256_file(path: Path, chunk_size: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class GleEntry:
    name: str           # ex.: "GL1"
    filename: str       # ex.: "GL1.gle"
    rel_path: str       # ex.: "Relays/QPC1_TR1_UPC1/Misc/GL1.gle"
    fs_path: Path       # caminho absoluto apos extracao


@dataclass
class RelayEntry:
    name: str
    gles: list[GleEntry] = field(default_factory=list)
    model: str | None = None   # ex.: "487E-3" extraido de RELAYTYPE
    ip: str | None = None      # IPADDR encontrado no SET_P? da familia


@dataclass
class RdbInfo:
    rdb_path: Path
    extract_dir: Path
    sha256: str
    reused: bool                 # True se nao foi necessario re-escrever/extrair
    relays: list[RelayEntry]
    # Nome que o usuario subiu, e nao o do cache: guardando por hash, o arquivo
    # em disco e' o mesmo pra todo mundo, e sem isto todas as telas mostrariam
    # o nome de quem subiu primeiro.
    display_name: str = ""


def _extract_and_collect(rdb_path: Path, target_dir: Path,
                         on_progress=None) -> list[RelayEntry]:
    """Extrai todos os streams para `target_dir` e retorna a lista de reles.

    `on_progress(feitos, total, etapa)` e' chamado durante a extracao. Um RDB
    real tem milhares de streams e leva alguns segundos; sem isso a barra de
    progresso do cliente ficaria parada em "processando" ate o fim.
    """
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    relays: dict[str, list[GleEntry]] = {}

    ole = olefile.OleFileIO(str(rdb_path))
    try:
        entries = ole.listdir(streams=True, storages=False)
        total = len(entries) or 1
        # Reportar a cada stream seria dominado pelo custo do callback; de 64
        # em 64 ja da uma barra suave.
        step = max(1, total // 64)
        for i, entry in enumerate(entries):
            if on_progress is not None and (i % step == 0):
                on_progress(i, total, "Extraindo arquivos do RDB")
            out_path = target_dir.joinpath(*entry)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with ole.openstream(entry) as stream:
                out_path.write_bytes(stream.read())

            # GLE em Relays/<relay>/Misc/<arquivo>.gle
            if (len(entry) == 4
                    and entry[0] == "Relays"
                    and entry[2] == "Misc"
                    and entry[3].lower().endswith(".gle")):
                relay_name = entry[1]
                fname = entry[3]
                stem = fname.rsplit(".", 1)[0]
                relays.setdefault(relay_name, []).append(GleEntry(
                    name=stem,
                    filename=fname,
                    rel_path="/".join(entry),
                    fs_path=out_path,
                ))
    finally:
        ole.close()

    out: list[RelayEntry] = []
    items = sorted(relays.items())
    for i, (name, gles) in enumerate(items):
        if on_progress is not None:
            on_progress(i, len(items) or 1, "Lendo dados dos reles")
        model, ip = _relay_meta(target_dir, name)
        out.append(RelayEntry(
            name=name,
            gles=sorted(gles, key=lambda g: g.name),
            model=model,
            ip=ip,
        ))
    return out


def _scan_existing(extract_dir: Path) -> list[RelayEntry]:
    """Escaneia uma extracao existente para descobrir reles e GLEs."""
    relays_dir = extract_dir / "Relays"
    out: list[RelayEntry] = []
    if not relays_dir.is_dir():
        return out
    for relay_path in sorted(relays_dir.iterdir()):
        if not relay_path.is_dir():
            continue
        misc = relay_path / "Misc"
        gles: list[GleEntry] = []
        if misc.is_dir():
            for gle_path in sorted(misc.iterdir()):
                if gle_path.is_file() and gle_path.suffix.lower() == ".gle":
                    rel = f"Relays/{relay_path.name}/Misc/{gle_path.name}"
                    gles.append(GleEntry(
                        name=gle_path.stem,
                        filename=gle_path.name,
                        rel_path=rel,
                        fs_path=gle_path,
                    ))
        if gles:
            model, ip = _relay_meta(extract_dir, relay_path.name)
            out.append(RelayEntry(
                name=relay_path.name, gles=gles, model=model, ip=ip,
            ))
    return out


def _safe_rdb_name(filename: str) -> str:
    safe_name = sanitize_name(filename)
    if not safe_name.lower().endswith(".rdb"):
        safe_name = safe_name + ".rdb"
    return safe_name


def process_upload_stream(source, length: int, filename: str,
                          cache_root: Path | None = None,
                          on_progress=None) -> RdbInfo:
    """`process_upload`, lendo de um stream em vez de um `bytes` na memoria.

    Mesmo contrato e mesmo cache por conteudo; a diferenca e' que os 40-140 MB
    do RDB nunca ficam residentes. `source` e' qualquer objeto com `.read(n)`
    -- na web e' o `rfile` da requisicao.

    A ordem muda por causa disso: o sha256 so existe DEPOIS de ler tudo, entao
    o arquivo vai primeiro pra um temporario ao lado do cache e so entao
    descobrimos se aquele conteudo ja estava extraido. Se estava, o temporario
    e' descartado; se nao, ele e' movido pro lugar com `os.replace` -- mesmo
    sistema de arquivos, entao a troca e' atomica e nunca existe um
    `source.rdb` pela metade pra outra sessao encontrar.
    """
    def _report(done, total, stage):
        if on_progress is not None:
            on_progress(done, total, stage)

    if length <= 0:
        raise ValueError("arquivo RDB vazio")

    safe_name = _safe_rdb_name(filename)
    base = Path(cache_root) if cache_root is not None else _paths.cache_dir()
    incoming = base / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(incoming), suffix=".rdb-part")
    os.close(fd)
    tmp: Path | None = Path(tmp_name)
    try:
        sha = stream_to_file(source, length, Path(tmp_name),
                             on_progress=on_progress)
        entry = rdb_cache.entry_for(sha, root=cache_root)

        # A trava e' por hash: dois visitantes subindo o mesmo arquivo ao mesmo
        # tempo extraiam por cima um do outro. O segundo espera e reaproveita.
        with rdb_cache.lock_for(sha):
            relays: list[RelayEntry] = []
            reused = False
            if entry.complete:
                _report(0, 1, "Reaproveitando extracao existente")
                relays = _scan_existing(entry.extract_dir)
                # Edge case: extracao prevista existe mas esta vazia/incompleta
                reused = bool(relays)
            if not reused:
                _report(0, 1, "Gravando RDB em disco")
                entry.root.mkdir(parents=True, exist_ok=True)
                # Sem meta.json ate a extracao terminar: se o processo morrer no
                # meio, a proxima passada refaz em vez de servir meia extracao.
                entry.meta_path.unlink(missing_ok=True)
                os.replace(Path(tmp_name), entry.rdb_path)
                tmp = None                    # o temporario virou o definitivo
                relays = _extract_and_collect(entry.rdb_path, entry.extract_dir,
                                              on_progress)
                rdb_cache.write_meta(entry, safe_name, len(relays))
            else:
                rdb_cache.touch(entry)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)

    return RdbInfo(
        rdb_path=entry.rdb_path,
        extract_dir=entry.extract_dir,
        sha256=sha,
        reused=reused,
        relays=relays,
        display_name=safe_name,
    )


def process_upload(data: bytes, filename: str, cache_root: Path | None = None,
                   on_progress=None) -> RdbInfo:
    """Extrai o RDB no cache por conteudo e devolve o que ele contem.

    O arquivo vai pra `cache/rdb/<sha256>/source.rdb` e a extracao pra
    `.../extracted/`. Dois uploads do mesmo conteudo -- do mesmo usuario ou de
    outro, hoje ou depois de um restart -- reaproveitam a mesma extracao.
    `cache_root` troca a raiz do cache (uso fora da web); None usa
    o diretorio de cache configurado (`selfiles.configure`).

    `display_name` sai do nome que ESTE upload trouxe, e nao do cache: senao
    todo mundo veria na tela o nome de quem subiu primeiro.

    `on_progress(feitos, total, etapa)` alimenta a barra de progresso do
    cliente durante as fases lentas (hash, gravacao e extracao).

    Recebe os bytes prontos. Quem tem um stream -- o upload da web -- deve usar
    `process_upload_stream`, que nao carrega o arquivo inteiro na memoria. Esta
    aqui continua porque varios chamadores JA tem os bytes em maos
    (`derived.adopt` depois de uma exportacao, os matchers, os testes), e pra
    esses um BytesIO e' o caminho honesto.
    """
    if not data:
        raise ValueError("arquivo RDB vazio")
    return process_upload_stream(io.BytesIO(data), len(data), filename,
                                 cache_root=cache_root, on_progress=on_progress)


def find_gle(info: RdbInfo, relay_name: str, gle_name: str) -> GleEntry | None:
    """Resolve (relay, gle) -> GleEntry; aceita 'GL1' ou 'GL1.gle' em gle_name."""
    for r in info.relays:
        if r.name != relay_name:
            continue
        for g in r.gles:
            if g.name == gle_name or g.filename == gle_name:
                return g
        return None
    return None


def relays_to_dict(relays: list[RelayEntry]) -> list[dict]:
    """Serializa a lista de reles para JSON (consumo pelo frontend)."""
    return [
        {
            "name": r.name,
            "model": r.model,
            "ip": r.ip,
            "gles": [
                {"name": g.name, "filename": g.filename, "rel_path": g.rel_path}
                for g in r.gles
            ],
        }
        for r in relays
    ]
