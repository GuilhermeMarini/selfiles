"""Where the library finds its data, and where it may write.

A library must not reach for an application's directory layout. This module is
the whole of the coupling that used to exist -- every registry here imported
the host's own path module and read a constant off it -- and it is now two questions the
host answers, once, at start-up:

``configure(user_data_dir=...)``
    A directory searched **before** the packaged data. It is how a host lets
    someone add a relay model at runtime without shipping a new release: PAC
    CT's "Importar perfil DNP" writes a ``wordbits/<MODEL>.json`` there. Files
    that are not in the overlay still come from the package, per model, so an
    overlay never has to be complete.

``configure(cache_dir=...)``
    Where RDB extractions go. Content-addressed and shared on purpose, so it
    belongs to the host, not to a process. Defaults under the system temp
    directory, which is the honest default for a library nobody configured.

Both are process-wide, because the data they name is: the registries memoise,
and two answers to "which relay models exist" is a bug, not a feature.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

#: The data that ships inside the wheel: one directory per registry.
PACKAGE_DATA: Path = Path(__file__).resolve().parent / "data"

_LOCK = threading.Lock()
_user_data_dir: Path | None = None
_cache_dir: Path | None = None


def configure(*, user_data_dir: Path | str | None = None,
              cache_dir: Path | str | None = None) -> None:
    """Point the library at a host's directories. Call once, at start-up."""
    global _user_data_dir, _cache_dir
    with _LOCK:
        if user_data_dir is not None:
            _user_data_dir = Path(user_data_dir).expanduser().resolve()
        if cache_dir is not None:
            _cache_dir = Path(cache_dir).expanduser().resolve()


def user_data_dir() -> Path | None:
    """The overlay, or ``None`` when the host never set one."""
    return _user_data_dir


def data_dirs(name: str) -> list[Path]:
    """Directories to search for registry ``name``, most specific first.

    A missing directory is dropped rather than raising: an overlay that does
    not exist yet is the normal state on a fresh install, not an error.
    """
    out: list[Path] = []
    if _user_data_dir is not None:
        candidate = _user_data_dir / name
        if candidate.is_dir():
            out.append(candidate)
    packaged = PACKAGE_DATA / name
    if packaged.is_dir():
        out.append(packaged)
    return out


def writable_data_dir(name: str) -> Path:
    """Where a host-supplied registry file should be written.

    Raises if no overlay was configured: writing into the installed package is
    not a fallback, it is a different and worse thing to do.
    """
    if _user_data_dir is None:
        raise RuntimeError(
            "selfiles.configure(user_data_dir=...) was never called, so there "
            "is nowhere to write a supplied profile."
        )
    out = _user_data_dir / name
    out.mkdir(parents=True, exist_ok=True)
    return out


def cache_dir() -> Path:
    """Where RDB extractions live. Content-addressed, shared, host-owned."""
    if _cache_dir is not None:
        return _cache_dir
    return Path(tempfile.gettempdir()) / "selfiles" / "rdb"
