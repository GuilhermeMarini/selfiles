"""
Parser de arquivos SET_*.TXT (e set_*.txt) extraidos de RDBs do QuickSet.

Layout comum (independente de familia):

    [INFO]                              <- cabecalho; pares KEY=VALUE sem aspas
    RELAYTYPE=SEL-487E-3
    FID=...
    [<SECTION>]                         <- L1, S1, G1, 1, P5, etc.
    KEY,"VALUE"                         <- a maioria das linhas
    KEY,VALUE                           <- ocasionalmente sem aspas (numericos)

Este modulo eh deliberadamente *agnostico de familia*. Ele apenas tokeniza
em linhas estruturadas. A interpretacao de cada linha (latch slot, par
SET/RST, equacao com `:=`, etc.) fica para `selfiles.selogic.model`.

API publica:

    parse_settings_file(path) -> ParsedSettings
    parse_relay_settings_dir(relay_dir) -> list[ParsedSettings]
    iter_settings_files(relay_dir) -> Iterator[Path]
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Casa lines como:
#   KEY,"VALUE"
#   KEY,VALUE
#   KEY,
# Captura KEY (G1) e VALUE bruto (G2 ou G3). VALUE pode ter aspas, virgulas,
# pontuacao SELOGIC arbitraria. Linhas em branco/comentario sao filtradas antes.
_KV_QUOTED_RE = re.compile(r'^\s*([^,\s]+)\s*,\s*"((?:[^"\\]|\\.)*)"\s*$')
_KV_BARE_RE = re.compile(r'^\s*([^,\s]+)\s*,\s*(.*?)\s*$')

# INFO header: KEY=VALUE sem aspas.
_INFO_RE = re.compile(r'^\s*([^=\s]+)\s*=\s*(.*?)\s*$')

# Header de secao: [NAME]
_SECTION_RE = re.compile(r'^\s*\[\s*([^\]\s]+)\s*\]\s*$')


@dataclass(frozen=True)
class Line:
    """Uma linha tokenizada de um SET_*.TXT.

    Atributos:
        key:   nome bruto da chave (ex.: 'PROTSEL1', 'CTRS', 'SET01').
        value: valor bruto como aparece no arquivo, ja sem as aspas externas
               (mas com `#`/comentario interno preservado, se houver).
        lineno: 1-based no arquivo de origem.
    """
    key: str
    value: str
    lineno: int


@dataclass
class ParsedSettings:
    """Conteudo de um SET_*.TXT.

    Atributos:
        path:    caminho absoluto do arquivo na extracao.
        section: nome da secao principal (ex.: 'L1', 'S1', 'G1', '1', 'PF').
                 Eh o ultimo header `[...]` antes das linhas de dados. None
                 se o arquivo so tem o `[INFO]`.
        info:    pares do `[INFO]` (RELAYTYPE, FID, BFID, PARTNO).
        lines:   linhas de dados em ordem de leitura. Linhas vazias filtradas.
    """
    path: Path
    section: str | None
    info: dict[str, str] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)

    @property
    def relaytype(self) -> str | None:
        return self.info.get("RELAYTYPE")


def _read_text(path: Path) -> str:
    """Le com latin-1 (tolera qualquer byte) e remove BOM se presente."""
    raw = path.read_bytes()
    # BOM UTF-8 (raro em RDB mas barato de remover)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("latin-1", errors="replace")


def parse_settings_file(path: Path) -> ParsedSettings:
    """Tokeniza um SET_*.TXT.

    Aceita tanto a forma `KEY,"VALUE"` quanto `KEY,VALUE` (sem aspas).
    Linhas que nao casam com nenhum dos padroes sao silenciosamente ignoradas
    -- isso eh defensivo: o QuickSet ocasionalmente emite linhas mal-formadas
    ou comentarios proprietarios, e nao queremos quebrar o tool por isso.
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
            # A primeira secao "real" e a canonica do arquivo. Algum SET_*.TXT
            # pode ter `[INFO]` seguido de uma unica secao de dados; outros
            # tem multiplas (raro). Guardamos a primeira.
            if result.section is None:
                result.section = section_name
            continue

        if in_info:
            mi = _INFO_RE.match(line)
            if mi:
                result.info[mi.group(1)] = mi.group(2)
            continue

        # Linha de dados em uma secao real.
        if current_section is None:
            # Linha solta antes de qualquer secao: ignora.
            continue

        mq = _KV_QUOTED_RE.match(line)
        if mq:
            key, value = mq.group(1), mq.group(2)
            # Aspas escapadas dentro do valor: o QuickSet quase nunca produz isso,
            # mas se aparecer, devolvemos como veio (sem desescapar) para nao
            # perder informacao.
            result.lines.append(Line(key=key, value=value, lineno=lineno))
            continue

        mb = _KV_BARE_RE.match(line)
        if mb:
            key, value = mb.group(1), mb.group(2)
            # Strip aspas residuais de valores tipo `KEY,"x"` que escaparam o
            # primeiro regex por whitespace estranho.
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            result.lines.append(Line(key=key, value=value, lineno=lineno))
            continue

        # Nao casou com nada -- ignora (defensivo).

    return result


# Padroes que caracterizam um arquivo de settings dentro do diretorio do rele.
# Os 4xx usam `SET_*.TXT` (maiusculo); 3xx/7xx usam `set_*.txt` (minusculo).
# Ambos terminam em `.TXT` no OLE (case-insensitive no Windows), entao a
# diferenca aparece apenas apos extracao.
_SETTINGS_NAME_RE = re.compile(r"^set_[a-z0-9]+\.txt$", re.IGNORECASE)


def is_settings_filename(name: str) -> bool:
    """True se `name` parece um arquivo de settings (SET_*.TXT ou set_*.txt).

    Exclui explicitamente arquivos auxiliares como `BAY_SCREEN.TXT` (que nao
    tem o prefixo `SET_`) e `SET_HMI.TXT` que algumas familias usam para HMI
    e nao para settings em si (mas mantemos esse pois eh um setting de fato).
    """
    return _SETTINGS_NAME_RE.match(name) is not None


def iter_settings_files(relay_dir: Path) -> Iterator[Path]:
    """Itera sobre os SET_*.TXT do diretorio do rele (top-level apenas).

    NAO recursa em `Misc/` -- aqueles sao GLE/Cfg/Device, nao settings.
    """
    if not relay_dir.is_dir():
        return
    for child in sorted(relay_dir.iterdir()):
        if child.is_file() and is_settings_filename(child.name):
            yield child


def parse_relay_settings_dir(relay_dir: Path) -> list[ParsedSettings]:
    """Parseia todos os SET_*.TXT do rele, em ordem alfabetica do nome."""
    return [parse_settings_file(p) for p in iter_settings_files(relay_dir)]
