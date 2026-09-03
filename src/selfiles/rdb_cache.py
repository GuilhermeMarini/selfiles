"""Cache de extracao de RDB chaveado pelo conteudo.

Antes cada sessao guardava a propria copia do RDB (40-140 MB) e a propria
extracao, porque `process_upload` chaveava por NOME dentro do `base_dir` de
cada ferramenta. Como dois arquivos com o mesmo sha256 SAO o mesmo arquivo, a
extracao passou a morar em `cache/rdb/<sha256>/`, unica no processo:

    cache/rdb/<sha256>/source.rdb
    cache/rdb/<sha256>/extracted/Relays/...
    cache/rdb/<sha256>/meta.json

`meta.json` so e' escrito DEPOIS que a extracao termina. Entrada sem ele e'
extracao interrompida (kill -9, disco cheio) e e' refeita -- e' o que substitui
a comparacao de hash do arquivo em disco que existia antes.

O nome que o usuario ve nao mora aqui: cada sessao carrega o seu em
`RdbInfo.display_name`, senao todo mundo veria o nome de quem subiu primeiro.

Diferente de `cache/sessions/`, este diretorio NAO e' apagado no boot --
sobreviver ao restart e' o motivo dele existir. Em troca, ele nao tem dono e
cresceria pra sempre, entao `sweep()` roda junto com o sweeper das sessoes.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from selfiles import _paths

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# Uma trava por hash: dois visitantes subindo o mesmo RDB ao mesmo tempo
# extraiam por cima um do outro. O segundo espera e reaproveita.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class CacheEntry:
    """Uma extracao no cache. Os caminhos sao derivados do hash, nunca do nome."""

    sha256: str
    root: Path

    @property
    def rdb_path(self) -> Path:
        return self.root / "source.rdb"

    @property
    def extract_dir(self) -> Path:
        return self.root / "extracted"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def complete(self) -> bool:
        """So e' reaproveitavel quem tem meta.json -- ver docstring do modulo."""
        return self.meta_path.is_file() and self.rdb_path.is_file()


def entry_for(sha256: str, root: Path | None = None) -> CacheEntry:
    """Entrada do cache pra esse conteudo. `root` troca a raiz (uso fora da web)."""
    if not _SHA_RE.match(sha256 or ""):
        raise ValueError(f"sha256 invalido: {sha256!r}")
    base = Path(root) if root is not None else _paths.cache_dir()
    return CacheEntry(sha256=sha256, root=base / sha256)


def _forget_lock(sha256: str) -> None:
    """Drop the lock of an entry that no longer exists.

    `lock_for` memoises one `threading.Lock` per sha256 and nothing ever
    removed one -- not even `sweep()`, which deletes the very directory the
    lock guards. A long-running server therefore kept one lock object per RDB
    ever uploaded: small, and the only structure here that grew without bound.

    Only takes it away when nobody holds it. Acquiring without blocking is the
    whole test: if someone is mid-extraction on this hash, the lock stays and
    the next sweep gets it.
    """
    with _LOCKS_GUARD:
        lk = _LOCKS.get(sha256)
        if lk is None:
            return
        if lk.acquire(blocking=False):
            lk.release()
            _LOCKS.pop(sha256, None)


def lock_for(sha256: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lk = _LOCKS.get(sha256)
        if lk is None:
            lk = _LOCKS[sha256] = threading.Lock()
        return lk


def write_meta(entry: CacheEntry, display_name: str, n_relays: int) -> None:
    now = time.time()
    entry.meta_path.write_text(json.dumps({
        "version": 1,
        "sha256": entry.sha256,
        # So pra inspecao humana: o nome que aparece na tela vem de quem subiu.
        "first_name": display_name,
        "relays": n_relays,
        "created": now,
        "last_used": now,
    }, indent=2), encoding="utf-8")


def touch(entry: CacheEntry) -> None:
    """Marca a entrada como em uso -- o sweeper olha `last_used`."""
    try:
        meta = json.loads(entry.meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    meta["last_used"] = time.time()
    try:
        entry.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass


def _last_used(entry_root: Path) -> float:
    try:
        meta = json.loads((entry_root / "meta.json").read_text(encoding="utf-8"))
        return float(meta.get("last_used") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


#: Uploads em andamento (`process_upload_stream` grava aqui antes de conhecer
#: o sha256). Nao casa `_SHA_RE`, entao a varredura de entradas ignora.
INCOMING_DIRNAME = "_incoming"

#: Um upload de 500 MB numa rede de subestacao nao passa disso. Um `.rdb-part`
#: mais velho que isso e' de um processo que morreu no meio.
_INCOMING_MAX_AGE = 6 * 3600


def _sweep_incoming(base: Path, now: float, logger) -> int:
    """Apaga restos de upload interrompido.

    `process_upload_stream` limpa o proprio temporario num `finally`, o que
    cobre erro e desconexao. O que ele nao cobre e' `kill -9`, OOM ou queda de
    energia no meio do recebimento -- e diferente de `cache/sessions/`, que e'
    apagado no boot, `cache/rdb/` sobrevive ao restart de proposito. Sem isto,
    cada upload morto deixaria ate 500 MB parados pra sempre.
    """
    incoming = base / INCOMING_DIRNAME
    if not incoming.is_dir():
        return 0
    n = 0
    for part in incoming.iterdir():
        try:
            if not part.is_file() or now - part.stat().st_mtime < _INCOMING_MAX_AGE:
                continue
            size = part.stat().st_size
            part.unlink()
        except OSError as e:
            logger.warning("[rdb-cache] nao consegui remover %s: %s", part.name, e)
            continue
        n += 1
        logger.info("[rdb-cache] upload interrompido descartado (%s, %.1f MB)",
                    part.name, size / (1 << 20))
    return n


def sweep(logger, max_gb: float = 8.0, max_age_days: float = 30.0,
          min_age_seconds: float = 8 * 3600, root: Path | None = None) -> int:
    """Remove entradas velhas e, se ainda passar do teto, as menos usadas.

    `min_age_seconds` e' o TTL da sessao: uma sessao viva pode nao tocar o RDB
    por horas e ainda voltar a usa-lo, entao nada mais novo que isso sai.
    Devolve quantas entradas foram removidas.
    """
    base = Path(root) if root is not None else _paths.cache_dir()
    if not base.is_dir():
        return 0
    now = time.time()
    entries = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or not _SHA_RE.match(child.name):
            continue
        entries.append((child, _last_used(child), _dir_size(child)))

    removed = 0
    _sweep_incoming(base, now, logger)

    def drop(path: Path, why: str) -> bool:
        nonlocal removed
        try:
            shutil.rmtree(path)
        except OSError as e:
            logger.warning("[rdb-cache] nao consegui remover %s: %s",
                           path.name[:12], e)
            return False
        removed += 1
        _forget_lock(path.name)
        logger.info("[rdb-cache] %s removido (%s)", path.name[:12], why)
        return True

    keep = []
    for path, used, size in entries:
        age = now - used
        if age >= min_age_seconds and age > max_age_days * 86400:
            drop(path, f"ocioso ha {age / 86400:.1f} dias")
            continue
        keep.append((path, used, size))

    cap = int(max_gb * (1 << 30))
    total = sum(s for _, _, s in keep)
    for path, used, size in sorted(keep, key=lambda t: t[1]):
        if total <= cap:
            break
        if now - used < min_age_seconds:
            continue  # sessao viva ainda pode precisar
        if drop(path, f"teto de {max_gb:.1f} GB"):
            total -= size
    return removed
