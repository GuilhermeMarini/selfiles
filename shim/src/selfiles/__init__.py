"""`selfiles` is now `SELlib`. This package only forwards to it.

`selfiles` 1.1.1 was the last real release under this name; 2.0.0 is the same
library called `SELlib`, imported as `sellib`. This 1.1.2 exists so that an
install pinned to `selfiles` keeps working and says why, instead of resolving
to a version that predates every fix made since.

It is a forwarder, not a copy. Every submodule is aliased into `sys.modules`
so that `selfiles.rdb is sellib.rdb` -- the same module object, not a second
import of the same file. That identity is the whole point: `sellib`'s
registries and the RDB extraction cache memoise per module, and two copies of
`rdb.py` in one process would mean two caches disagreeing about what a relay
model is.

Nothing here will be updated. Change the import.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import warnings

import sellib

__version__ = "1.1.2"

warnings.warn(
    "`selfiles` was renamed to `SELlib` at 2.0.0. Import `sellib` instead; "
    "this shim forwards to it and receives no further changes.",
    DeprecationWarning,
    stacklevel=2,
)

# Import every submodule, then alias it under the old name. Eager rather than
# lazy on purpose: a lazy alias would have to guess which submodules exist,
# and this package is dead code whose only job is to be correct.
for _finder, _name, _ispkg in pkgutil.walk_packages(sellib.__path__, "sellib."):
    try:
        _module = importlib.import_module(_name)
    except ImportError:                      # pragma: no cover - optional deps
        continue
    sys.modules["selfiles" + _name[len("sellib"):]] = _module

# And the root's own names, so `selfiles.configure` and `selfiles.rdb` resolve
# as attributes too -- `sys.modules` alone does not bind them here.
globals().update({_k: _v for _k, _v in vars(sellib).items()
                  if not _k.startswith("_")})

__all__ = list(getattr(sellib, "__all__", []))
