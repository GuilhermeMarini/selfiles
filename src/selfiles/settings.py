"""
Parser for the SET_*.TXT (and set_*.txt) files extracted from a QuickSet RDB.

The common layout, whatever the relay family:

    [INFO]                              <- header; KEY=VALUE pairs, unquoted
    RELAYTYPE=SEL-487E-3
    FID=...
    [<SECTION>]                         <- L1, S1, G1, 1, P5, ...
    KEY,"VALUE"                         <- most lines
    KEY,VALUE                           <- occasionally unquoted (numeric)

This module is deliberately *family-agnostic*. It only tokenises into
structured lines. What each line MEANS (a latch slot, a SET/RST pair, an
equation with `:=`) is `selfiles.selogic.model`'s problem, not this one's.

Public API:

    parse_settings_file(path) -> ParsedSettings
    parse_relay_settings_dir(relay_dir) -> list[ParsedSettings]
    iter_settings_files(relay_dir) -> Iterator[Path]
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Matches lines like:
#   KEY,"VALUE"
#   KEY,VALUE
#   KEY,
# Captures KEY (G1) and the raw VALUE (G2 or G3). A VALUE may contain quotes,
# commas and arbitrary SELOGIC punctuation. Blank and comment lines are
# filtered out before this runs.
_KV_QUOTED_RE = re.compile(r'^\s*([^,\s]+)\s*,\s*"((?:[^"\\]|\\.)*)"\s*$')
_KV_BARE_RE = re.compile(r'^\s*([^,\s]+)\s*,\s*(.*?)\s*$')

# INFO header: KEY=VALUE, unquoted.
_INFO_RE = re.compile(r'^\s*([^=\s]+)\s*=\s*(.*?)\s*$')

# Section header: [NAME]
_SECTION_RE = re.compile(r'^\s*\[\s*([^\]\s]+)\s*\]\s*$')


@dataclass(frozen=True)
class Line:
    """One tokenised line of a SET_*.TXT.

    Attributes:
        key:   the raw key name (e.g. 'PROTSEL1', 'CTRS', 'SET01').
        value: the raw value as it appears in the file, with the surrounding
               quotes already removed (but any inner `#` or comment kept).
        lineno: 1-based, in the source file.
    """
    key: str
    value: str
    lineno: int


@dataclass
class ParsedSettings:
    """The content of one SET_*.TXT.

    Attributes:
        path:    absolute path of the file inside the extraction.
        section: name of the main section (e.g. 'L1', 'S1', 'G1', '1', 'PF').
                 It is the last `[...]` header before the data lines, and
                 None when the file carries only the `[INFO]` block.
        info:    the `[INFO]` pairs (RELAYTYPE, FID, BFID, PARTNO).
        lines:   data lines in read order. Blank lines are dropped.
    """
    path: Path
    section: str | None
    info: dict[str, str] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)

    @property
    def relaytype(self) -> str | None:
        return self.info.get("RELAYTYPE")


def _read_text(path: Path) -> str:
    """Read as latin-1 (any byte is legal) and strip a BOM if there is one."""
    raw = path.read_bytes()
    # A UTF-8 BOM: rare in an RDB, cheap to drop.
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("latin-1", errors="replace")


def parse_settings_file(path: Path) -> ParsedSettings:
    """Tokenise one SET_*.TXT.

    Accepts both `KEY,"VALUE"` and `KEY,VALUE` (unquoted). A line matching
    neither pattern is dropped in silence, and that is defensive on purpose:
    QuickSet occasionally emits a malformed line or a proprietary comment, and
    a settings reader has no business failing over one.
    """
    text = _read_text(path)
    result = ParsedSettings(path=path, section=None)
    current_section: str | None = None
    in_info = False

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        m = _SECTION_RE.match(line)
        if m:
            section_name = m.group(1)
            if section_name.upper() == "INFO":
                in_info = True
                continue
            in_info = False
            current_section = section_name
            # The first "real" section is the file's canonical one. Some
            # SET_*.TXT carry `[INFO]` and then a single data section; a few
            # carry several (rare). Keep the first.
            if result.section is None:
                result.section = section_name
            continue

        if in_info:
            mi = _INFO_RE.match(line)
            if mi:
                result.info[mi.group(1)] = mi.group(2)
            continue

        # A data line, inside a real section.
        if current_section is None:
            # A stray line before any section: ignore it.
            continue

        mq = _KV_QUOTED_RE.match(line)
        if mq:
            key, value = mq.group(1), mq.group(2)
            # Escaped quotes inside a value: QuickSet almost never writes
            # them, but if one shows up it is handed back exactly as it came
            # (not unescaped), so nothing is lost.
            result.lines.append(Line(key=key, value=value, lineno=lineno))
            continue

        mb = _KV_BARE_RE.match(line)
        if mb:
            key, value = mb.group(1), mb.group(2)
            # Strip leftover quotes from values like `KEY,"x"` that escaped
            # the first regex because of odd whitespace.
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            result.lines.append(Line(key=key, value=value, lineno=lineno))
            continue

        # Matched nothing -- ignore it (defensive).

    return result


# What marks a settings file inside a relay's directory. The 4xx write
# `SET_*.TXT` (uppercase); the 3xx and 7xx write `set_*.txt` (lowercase). Both
# end up as `.TXT` inside the OLE, which is case-insensitive, so the
# difference only appears once the files are extracted.
_SETTINGS_NAME_RE = re.compile(r"^set_[a-z0-9]+\.txt$", re.IGNORECASE)


def is_settings_filename(name: str) -> bool:
    """True when `name` looks like a settings file (SET_*.TXT or set_*.txt).

    Deliberately excludes auxiliary files such as `BAY_SCREEN.TXT`, which has
    no `SET_` prefix. `SET_HMI.TXT` is kept: some families use it for the HMI
    rather than for protection settings, but it is a settings file all the
    same.
    """
    return _SETTINGS_NAME_RE.match(name) is not None


def iter_settings_files(relay_dir: Path) -> Iterator[Path]:
    """Iterate the relay directory's SET_*.TXT files (top level only).

    Does NOT descend into `Misc/` -- what lives there is GLE, Cfg and Device,
    not settings.
    """
    if not relay_dir.is_dir():
        return
    for child in sorted(relay_dir.iterdir()):
        if child.is_file() and is_settings_filename(child.name):
            yield child


def parse_relay_settings_dir(relay_dir: Path) -> list[ParsedSettings]:
    """Parse every SET_*.TXT of the relay, in alphabetical order of file name."""
    return [parse_settings_file(p) for p in iter_settings_files(relay_dir)]
