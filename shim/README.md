# selfiles — renamed to SELlib

This distribution is a forwarder. The library it used to contain is published
as [SELlib](https://pypi.org/project/SELlib/) and imported as `sellib`.

```python
import sellib                    # not: import selfiles
from sellib.rdb import process_upload
```

Installing `selfiles` 1.1.2 installs `SELlib` and makes `import selfiles` keep
working with a `DeprecationWarning`; every submodule is the same object as its
`sellib` counterpart, so nothing double-loads. It will not be updated again —
`selfiles` 1.1.1 was the last release with code of its own.
