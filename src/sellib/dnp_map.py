"""Parser/serializer of a SEL DNP map file (``SET_D<n>.TXT``) from an RDB.

Each relay in an RDB carries one ``SET_D<n>.TXT`` per DNP session. The file is
a settings file with an ``[INFO]`` header and a single ``[D<n>]`` section whose
data lines are ``KEY,"VALUE"`` followed by ``0x1C`` (File Separator) and CRLF.

This module is deliberately NOT built on ``sellib.settings``: that
one treats the trailing ``0x1C`` as part of the value, which is fine for
reading and fatal for writing. Here round-trip fidelity is the contract --
``parse(data).serialize() == data`` for every SET_D in the reference RDBs --
because these bytes go back into a protection relay's settings.

Nothing here knows about the web, the session, or OLE.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# Data line: KEY,"VALUE" or KEY,VALUE. The terminator (0x1C, CR, LF) is split
# off before this runs, so `$` is the real end of the payload.
_KV_RE = re.compile(r'^([^,]+),(.*)$', re.DOTALL)
_SECTION_RE = re.compile(r'^\[([^\]]+)\]$')
_INFO_RE = re.compile(r'^([^=]+)=(.*)$', re.DOTALL)

# SET_D1.TXT / set_D3.txt. The 4xx extract uppercase, the 3xx/7xx lowercase --
# the OLE is case-insensitive, the filesystem after extraction is not.
_SETD_NAME_RE = re.compile(r'^set_d([0-9]+)\.txt$', re.IGNORECASE)


@dataclass(frozen=True)
class RawLine:
    """One physical line, kept faithfully enough to rebuild it byte for byte.

    ``raw`` is the line without its terminator, exactly as read. It is what
    ``serialize`` emits for any line that was never edited, which is how
    unknown or malformed lines survive a round trip untouched.
    """

    raw: bytes
    terminator: bytes          # b"\x1c\r\n", b"\r\n", b"\n", or b""
    key: str = ""              # "" when the line is not KEY,VALUE
    value: str = ""
    quoted: bool = False
    edited: bool = False

    def emit(self) -> bytes:
        if not self.edited:
            return self.raw + self.terminator
        payload = f'"{self.value}"' if self.quoted else self.value
        return f"{self.key},{payload}".encode("latin-1") + self.terminator


@dataclass
class SetDnpFile:
    """The parsed content of one SET_D<n>.TXT."""

    info: dict[str, str] = field(default_factory=dict)
    section: str = ""
    lines: list[RawLine] = field(default_factory=list)

    def index_of(self, key: str) -> int:
        # A falsy key would otherwise match ``RawLine.key``'s own default
        # ("" for headers, blank lines and anything unparsed) and address
        # whichever such line comes first -- typically ``[INFO]`` itself.
        # ``set_value``'s docstring promises unknown keys are refused; ""
        # is the one key that would defeat that promise.
        if not key:
            raise KeyError(key)
        for i, line in enumerate(self.lines):
            if line.key == key:
                return i
        raise KeyError(key)

    def get_value(self, key: str) -> str:
        return self.lines[self.index_of(key)].value

    def set_value(self, key: str, value: str) -> None:
        """Replace one key's value. Raises KeyError for a key the file lacks.

        Refusing unknown keys is deliberate: a DNP map has a fixed key set
        decided by the relay's firmware, so a key that isn't there is a bug on
        the caller's side, never a new point to invent.
        """
        i = self.index_of(key)
        old = self.lines[i]
        if old.value == value:
            return
        self.lines[i] = RawLine(
            raw=old.raw, terminator=old.terminator, key=old.key,
            value=value, quoted=old.quoted, edited=True,
        )

    def serialize(self) -> bytes:
        return b"".join(line.emit() for line in self.lines)

    def points(self) -> list[DnpPoint]:
        """The editable rows, in file order.

        ``AI_SCAn``/``AI_DBDn``/``CO_DBDn`` are not rows: they fold into the
        row of the point they qualify, which is how the relay means them.
        """
        mods: dict[tuple[str, int, str], tuple[str, str]] = {}
        base: list[tuple[str, int, str, str]] = []
        for line in self.lines:
            m = _POINT_KEY_RE.match(line.key) if line.key else None
            if m is None:
                continue
            # Extract kind and mod from whichever groups matched in the regex.
            kind = m.group("kind") or m.group("co") or m.group("base")
            index = int(m.group("index"))
            mod = m.group("mod") or m.group("co_mod")
            if mod:
                mods[(kind, index, mod)] = (line.key, line.value)
            else:
                base.append((kind, index, line.key, line.value))

        out: list[DnpPoint] = []
        for kind, index, key, value in base:
            sca = mods.get((kind, index, "SCA"))
            dbd = mods.get((kind, index, "DBD"))
            out.append(DnpPoint(
                kind=kind, index=index, key=key, value=value,
                sca_key=sca[0] if sca else None,
                sca=sca[1] if sca else None,
                dbd_key=dbd[0] if dbd else None,
                dbd=dbd[1] if dbd else None,
            ))
        return out

    def point_keys(self) -> set[str]:
        """Keys the DNP map editor is allowed to write: point values, plus
        their SCA/DBD columns. Excludes MINDIST/MAXDIST and anything else
        ``extras()`` reports -- those configure the DNP session, not the
        map, and are shown read-only in the editor.
        """
        keys: set[str] = set()
        for p in self.points():
            keys.add(p.key)
            if p.sca_key:
                keys.add(p.sca_key)
            if p.dbd_key:
                keys.add(p.dbd_key)
        return keys

    def blocks(self) -> dict[str, list[DnpPoint]]:
        """Points grouped by kind. Only the blocks this file actually has."""
        out: dict[str, list[DnpPoint]] = {}
        for p in self.points():
            out.setdefault(p.kind, []).append(p)
        return {k: out[k] for k in POINT_KINDS if k in out}

    def extras(self) -> list[tuple[str, str]]:
        """Data keys that are not points: MINDIST, MAXDIST, anything unknown.

        Shown read-only. They configure the DNP session, not the map, and this
        tool has no business changing them. Also includes orphaned modifiers
        (e.g., AI_SCA5 without a corresponding AI_5).
        """
        # Collect the set of (kind, index) pairs that exist as base points.
        pts = self.points()
        base_points: set[tuple[str, int]] = set()
        for p in pts:
            base_points.add((p.kind, p.index))

        out = []
        for line in self.lines:
            # Skip lines without keys (headers, empty lines, etc.)
            if not line.key:
                continue

            m = _POINT_KEY_RE.match(line.key)
            if m is None:
                # Non-point line: include in extras.
                out.append((line.key, line.value))
            else:
                # Point-like line: extract kind, mod, index.
                kind = m.group("kind") or m.group("co") or m.group("base")
                index = int(m.group("index"))
                mod = m.group("mod") or m.group("co_mod")

                # Only modifiers (with mod != None) are extras.
                # Base points are handled by points(), not extras().
                if mod:
                    # Include unconsumed modifiers (those whose base point doesn't exist).
                    if (kind, index) not in base_points:
                        out.append((line.key, line.value))
        return out


# The five DNP point blocks, in the order the UI shows them as tabs.
POINT_KINDS: tuple[str, ...] = ("BI", "BO", "AI", "AO", "CO")

# BI_1, BO_00, AI_SCA12, CO_DBD3 -> (kind, modifier, index). The modifier is
# what makes AI_SCA1 a column of AI_1 instead of a point of its own.
# Modifiers are restricted: SCA and DBD only after AI; DBD only after CO.
_POINT_KEY_RE = re.compile(
    r'^(?:(?P<kind>AI)_(?P<mod>SCA|DBD)?|(?P<co>CO)_(?P<co_mod>DBD)?|(?P<base>BI|BO|AO)_)(?P<index>[0-9]+)$'
)


@dataclass(frozen=True)
class DnpPoint:
    """One row of the editor: a DNP point plus its auxiliary columns."""

    kind: str                    # "BI" | "BO" | "AI" | "AO" | "CO"
    index: int
    key: str                     # the literal key, original padding intact
    value: str
    sca_key: str | None = None
    sca: str | None = None
    dbd_key: str | None = None
    dbd: str | None = None


def _split_terminator(line: bytes) -> tuple[bytes, bytes]:
    """Peel b"\\x1c\\r\\n" / b"\\r\\n" / b"\\n" off the end of a raw line."""
    for term in (b"\x1c\r\n", b"\x1c\n", b"\r\n", b"\n"):
        if line.endswith(term):
            return line[: -len(term)], term
    return line, b""


def check_value(value: str) -> str | None:
    """``None`` if ``value`` is safe to write into a SET_D line; else why not.

    Mirrors the two ways ``RawLine.emit`` can fail or corrupt the file: it
    encodes the payload as Latin-1 (a wider character raises), and CR, LF or
    0x1C (the field terminator) inside the value would inject an extra
    physical line into a container that promises byte-for-byte round trips.
    Neither is reachable from the shipped ``<input>`` (a text field, native
    charset), but both are reachable from the JSON API.
    """
    if any(c in value for c in "\r\n\x1c"):
        return "contém um caractere de controle não permitido"
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return "contém um caractere fora do conjunto aceito pelo relé (Latin-1)"
    return None


def parse(data: bytes) -> SetDnpFile:
    """Tokenize a SET_D<n>.TXT. Never raises on malformed input.

    Anything that does not look like ``KEY,VALUE`` is carried through as an
    opaque line, so a file this parser does not fully understand still
    round-trips unchanged.
    """
    out = SetDnpFile()
    in_info = False

    # keepends so each physical line keeps its own terminator; a file with
    # mixed CRLF and LF (never seen, but cheap to survive) round-trips too.
    for raw_with_term in data.splitlines(keepends=True):
        body, term = _split_terminator(raw_with_term)
        text = body.decode("latin-1")

        ms = _SECTION_RE.match(text)
        if ms:
            name = ms.group(1)
            if name.upper() == "INFO":
                in_info = True
            else:
                in_info = False
                if not out.section:
                    out.section = name
            out.lines.append(RawLine(raw=body, terminator=term))
            continue

        if in_info:
            mi = _INFO_RE.match(text)
            if mi:
                out.info[mi.group(1)] = mi.group(2)
            out.lines.append(RawLine(raw=body, terminator=term))
            continue

        mk = _KV_RE.match(text)
        if mk:
            key, rest = mk.group(1), mk.group(2)
            quoted = len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"'
            value = rest[1:-1] if quoted else rest
            out.lines.append(RawLine(
                raw=body, terminator=term, key=key, value=value, quoted=quoted,
            ))
            continue

        out.lines.append(RawLine(raw=body, terminator=term))

    return out


@dataclass(frozen=True)
class DnpSession:
    """One SET_D<n> of one relay: a DNP session's map."""

    name: str                        # "D1"
    fs_path: Path                    # absolute path in the extraction
    stream_parts: tuple[str, ...]    # the path inside the RDB's OLE storage


@dataclass(frozen=True)
class DnpRelay:
    """A relay that has at least one DNP map."""

    name: str
    relaytype: str | None
    sessions: list[DnpSession]


def discover(extract_dir: Path) -> list[DnpRelay]:
    """Find every relay with a DNP map inside an extracted RDB.

    This walks the extraction instead of reusing ``RdbInfo.relays`` on
    purpose: ``sellib.rdb`` only builds a ``RelayEntry`` for a relay
    that owns a ``.gle`` file, which silently drops data concentrators like the
    SEL-2440 -- exactly the devices whose DNP map someone wants to edit.
    """
    relays_dir = Path(extract_dir) / "Relays"
    if not relays_dir.is_dir():
        return []

    out: list[DnpRelay] = []
    for relay_dir in sorted(relays_dir.iterdir(), key=lambda p: p.name):
        if not relay_dir.is_dir():
            continue
        sessions: list[DnpSession] = []
        relaytype: str | None = None
        used_names: set[str] = set()  # Track names to avoid collisions
        for child in sorted(relay_dir.iterdir(), key=lambda p: p.name.upper()):
            if not child.is_file():
                continue
            m = _SETD_NAME_RE.search(child.name)
            if not m:
                continue
            data = child.read_bytes()
            parsed = parse(data)
            if relaytype is None:
                relaytype = parsed.info.get("RELAYTYPE")

            # Prefer the section header (e.g., "D1" from "[D1]") but use filename
            # as fallback to avoid collisions. Task 6 and Task 8 key per-session
            # dictionaries by this name, so two sessions with the same name would
            # silently drop one from the export.
            session_name = parsed.section
            if not session_name or session_name in used_names:
                # Fall back to filename-derived name if section is empty or collides
                session_name = f"D{m.group(1)}"
            used_names.add(session_name)

            sessions.append(DnpSession(
                name=session_name,
                fs_path=child,
                stream_parts=("Relays", relay_dir.name, child.name),
            ))
        if sessions:
            out.append(DnpRelay(
                name=relay_dir.name, relaytype=relaytype, sessions=sessions,
            ))
    return out


def same_model(a: str | None, b: str | None) -> bool:
    """Whether two ``RELAYTYPE`` strings name the same relay model.

    Exact string comparison, only normalised for surrounding blanks and case.
    Deliberately NOT the lenient family match ``wordbits._key_variants`` does:
    that one exists to find a *name domain*, where falling back from
    ``SEL-411L-A`` to ``411L`` costs nothing, while here the question is
    whether one relay's DNP map may be written onto another's. An option
    suffix is exactly what changes the I/O board, and with it how many BI/BO
    points the file has -- copying across it would drop the points the target
    does not have and leave the ones the source never mentions untouched,
    which is a half-copied map that looks complete.

    Both sides always come from a SET_D ``[INFO]`` header inside one RDB,
    written by the same QuickSet save, so two relays of one model do carry
    the same literal string.

    An empty or missing RELAYTYPE matches nothing, itself included: "unknown"
    is not a model, and two unknowns are not the same one.
    """
    ka = (a or "").strip().upper()
    kb = (b or "").strip().upper()
    return bool(ka) and ka == kb


def identical_groups(relay: DnpRelay) -> list[list[str]]:
    """Group a relay's sessions by identical content.

    Relays usually carry the same map on every DNP session, though the
    [D<n>] section header differs. Hash only the map content, excluding
    section headers, so identical maps group together.
    """
    def content_digest(fs_path: Path) -> str:
        """Hash file content excluding [section header] lines."""
        data = fs_path.read_bytes()
        lines = data.splitlines(keepends=True)
        # Keep only lines that are not section headers ([...]). Peel the
        # terminator the same way `parse()` does -- no real section header
        # carries the 0x1C, so `rstrip(b"\r\n")` happened to agree, but that
        # was luck, not a guarantee `_SECTION_RE` could rely on.
        hashable = b"".join(
            line for line in lines
            if not _SECTION_RE.match(
                _split_terminator(line)[0].decode("latin-1", errors="replace")
            )
        )
        return hashlib.sha256(hashable).hexdigest()

    by_digest: dict[str, list[str]] = {}
    order: list[str] = []
    for s in relay.sessions:
        digest = content_digest(s.fs_path)
        if digest not in by_digest:
            order.append(digest)
        by_digest.setdefault(digest, []).append(s.name)
    return [by_digest[d] for d in order]
