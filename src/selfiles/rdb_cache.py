"""RDB extraction cache, keyed by content.

Each session used to keep its own copy of the RDB (40-140 MB) and its own
extraction, because the upload was keyed by NAME inside each tool's own base
directory. Since two files with the same sha256 ARE the same file, the
extraction moved to one directory per content hash:

    <cache>/<sha256>/source.rdb
    <cache>/<sha256>/extracted/Relays/...
    <cache>/<sha256>/meta.json

`meta.json` is written only AFTER the extraction finishes. An entry without it
is an interrupted extraction (kill -9, a full disk) and gets redone -- that is
what replaced the on-disk hash comparison this module used to do.

The name a user sees does not live here: each session carries its own in
`RdbInfo.display_name`, or everyone would see whichever name was uploaded
first.

Unlike a per-session directory, this one is NOT wiped at start-up -- surviving
a restart is the reason it exists. In exchange it has no owner and would grow
forever, so `sweep()` is meant to run on a schedule.
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

# One lock per hash: two callers extracting the same RDB at the same time
# used to write over each other. The second waits and reuses the result.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class CacheEntry:
    """One extraction in the cache. Every path derives from the hash, never from
    the file name."""

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
    """The cache entry for this content. `root` overrides the cache root."""
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
        # For human inspection only: the name shown on screen comes from
        # whoever uploaded it, not from here.
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


#: Uploads in flight (`process_upload_stream` writes here before the sha256
#: is known). It does not match `_SHA_RE`, so the entry scan skips it.
INCOMING_DIRNAME = "_incoming"

#: A 500 MB upload over a substation network does not take longer than this.
#: A `.rdb-part` older than that belongs to a process that died mid-transfer.
_INCOMING_MAX_AGE = 6 * 3600


def _sweep_incoming(base: Path, now: float, logger) -> int:
    """Delete what an interrupted upload left behind.

    `process_upload_stream` removes its own temporary file in a `finally`,
    which covers an error or a dropped connection. What it cannot cover is
    `kill -9`, an OOM kill or a power cut mid-transfer -- and unlike a
    per-session directory, this cache deliberately survives a restart. Without
    this, every dead upload would strand up to 500 MB forever.
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
    """Remove stale entries and, if still over the ceiling, the least recently
    used ones.

    `min_age_seconds` is the host's session TTL: a live session may not touch
    its RDB for hours and still come back to it, so nothing younger than that
    is ever removed. Returns how many entries went.
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
