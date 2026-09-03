"""
Comparador de valores SELOGIC com 4 veredictos:

  1. EQUAL                       -- mesma string apos strip de whitespace e
                                    comentarios identicos
  2. EQUAL_LOGIC_DIFF_COMMENT    -- corpo identico, comentarios diferentes
                                    (somente 4xx/7xx; 3xx nao tem `#`)
  3. EQUIVALENT                  -- mesma funcao booleana, sintaxe diferente
                                    (catch via AST canonicalizado e/ou tabela
                                    verdade ate 16 atomos)
  4. DIFFERENT                   -- avaliacao diferente em pelo menos um
                                    estado, OU > 16 atomos sem match
                                    canonical (com nota "nao verificado
                                    exaustivamente")

Para valores nao booleanos:
  - kind="number"   : parse como float, comparacao com tolerancia
  - kind="enum"     : igualdade de string trimada (case sensitive: 'Y' != 'y')
  - kind="string"   : igualdade de string trimada

A API recebe os RAW values direto do parser de SET_*.TXT (o comentario interno
`# ...`, se houver, ainda esta no valor; este modulo cuida do strip).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from selfiles.selogic import parser as sp

Verdict = Literal[
    "EQUAL",
    "EQUAL_LOGIC_DIFF_COMMENT",
    "EQUIVALENT",
    "DIFFERENT",
]


Kind = Literal["logic", "number", "enum", "string", "set_list"]


@dataclass(frozen=True)
class CompareResult:
    verdict: Verdict
    note: str | None = None


# Threshold para fallback de tabela verdade. Acima disso, a explosao binaria
# (2^N) fica cara: 2^16 = 65k linhas, 2^20 = 1M. Mantemos em 16 para teto
# pratico; equacoes SELOGIC reais raramente passam de ~10 atomos distintos.
_MAX_TRUTH_ATOMS = 16


def _split_logic_and_comment(raw: str) -> tuple[str, str]:
    """Retorna (corpo, comentario) -- ambos trim. Comentario sem o `#`."""
    h = raw.find("#")
    if h < 0:
        return raw.strip(), ""
    return raw[:h].strip(), raw[h + 1 :].strip()


def _normalize_whitespace(s: str) -> str:
    return " ".join(s.split())


def _compare_logic(
    a_body: str,
    b_body: str,
    dialect: sp.Dialect,
) -> CompareResult:
    """Compara dois corpos *sem comentario*. Aplica a cascata canonical +
    tabela verdade. Retorna EQUIVALENT/DIFFERENT (nunca EQUAL aqui)."""
    try:
        ast_a = sp.parse(a_body, dialect)
        ast_b = sp.parse(b_body, dialect)
    except sp.ParseError:
        # Nao parseou como booleano -- pode ser equacao matematica ou
        # sintaxe estranha. Compara como string normalizada.
        if _normalize_whitespace(a_body) == _normalize_whitespace(b_body):
            return CompareResult("EQUIVALENT", note="texto coincidente (sem parse)")
        return CompareResult("DIFFERENT", note="nao parseavel como booleano")

    can_a = sp.canonicalize(ast_a)
    can_b = sp.canonicalize(ast_b)
    if sp.node_repr(can_a) == sp.node_repr(can_b):
        return CompareResult("EQUIVALENT")

    # Fallback de tabela verdade
    union = sp.atoms(can_a) | sp.atoms(can_b)
    if len(union) > _MAX_TRUTH_ATOMS:
        return CompareResult(
            "DIFFERENT",
            note=f"nao verificado exaustivamente (>{_MAX_TRUTH_ATOMS} atomos)",
        )

    names = sorted(union)
    n = len(names)
    for mask in range(1 << n):
        env = {names[k]: bool((mask >> k) & 1) for k in range(n)}
        if sp.evaluate(can_a, env) != sp.evaluate(can_b, env):
            return CompareResult("DIFFERENT")

    return CompareResult("EQUIVALENT")


def compare_logic(
    a_raw: str,
    b_raw: str,
    dialect: sp.Dialect,
) -> CompareResult:
    """Compara duas equacoes SELOGIC do mesmo dialeto. 4 veredictos possiveis."""
    a_body, a_comment = _split_logic_and_comment(a_raw)
    b_body, b_comment = _split_logic_and_comment(b_raw)

    a_norm = _normalize_whitespace(a_body)
    b_norm = _normalize_whitespace(b_body)

    if a_norm == b_norm:
        if a_comment.strip() == b_comment.strip():
            return CompareResult("EQUAL")
        return CompareResult("EQUAL_LOGIC_DIFF_COMMENT")

    return _compare_logic(a_body, b_body, dialect)


def _try_float(s: str) -> float | None:
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def compare_number(
    a_raw: str,
    b_raw: str,
    *,
    rel_tol: float = 1e-6,
    abs_tol: float = 1e-6,
) -> CompareResult:
    """Compara dois numeros tolerando formatacao (0 vs 0.000000) e ruido FP."""
    a_str = a_raw.strip()
    b_str = b_raw.strip()
    if a_str == b_str:
        return CompareResult("EQUAL")
    fa = _try_float(a_str)
    fb = _try_float(b_str)
    if fa is None or fb is None:
        # Pelo menos um nao eh numero -- caia pra comparacao string.
        return CompareResult("DIFFERENT")
    diff = abs(fa - fb)
    if diff <= max(abs_tol, rel_tol * max(abs(fa), abs(fb))):
        return CompareResult("EQUIVALENT", note="numericamente iguais")
    return CompareResult("DIFFERENT")


def compare_enum(a_raw: str, b_raw: str) -> CompareResult:
    if a_raw.strip() == b_raw.strip():
        return CompareResult("EQUAL")
    return CompareResult("DIFFERENT")


def compare_string(a_raw: str, b_raw: str) -> CompareResult:
    return compare_enum(a_raw, b_raw)


def _parse_set_list(raw: str) -> set[str]:
    """Listas SER no QuickSet sao CSV simples. ALIAS sao space-separated."""
    # Aceita virgulas, espacos, tabs e ponto-e-virgula como delimitadores.
    import re
    return {t for t in re.split(r"[,\s;]+", raw.strip()) if t}


def compare_set_list(a_raw: str, b_raw: str) -> CompareResult:
    """Compara duas listas como conjuntos: ordem e duplicatas ignoradas.

    Usado pelas listas SER (Sequence of Events Recorder) e similares onde o
    rele registra um *conjunto* de wordbits e a posicao no arquivo nao tem
    semantica.

    Veredicto:
      - EQUAL              : se o texto bruto eh identico
      - EQUIVALENT         : conjuntos iguais mas texto reordenado
      - DIFFERENT          : conjuntos diferentes (nota lista o que sobra/falta)
    """
    a_text = " ".join(a_raw.split())
    b_text = " ".join(b_raw.split())
    if a_text == b_text:
        return CompareResult("EQUAL")
    a_set = _parse_set_list(a_raw)
    b_set = _parse_set_list(b_raw)
    if a_set == b_set:
        return CompareResult("EQUIVALENT", note="mesmo conjunto, ordem diferente")
    only_a = sorted(a_set - b_set)
    only_b = sorted(b_set - a_set)
    parts: list[str] = []
    if only_a:
        sample = only_a if len(only_a) <= 6 else only_a[:6] + [f"+{len(only_a)-6}"]
        parts.append(f"sobra em A: {', '.join(sample)}")
    if only_b:
        sample = only_b if len(only_b) <= 6 else only_b[:6] + [f"+{len(only_b)-6}"]
        parts.append(f"sobra em B: {', '.join(sample)}")
    return CompareResult("DIFFERENT", note=" | ".join(parts) or None)


def compare(
    a_raw: str,
    b_raw: str,
    *,
    kind: Kind,
    dialect: sp.Dialect = "keyword",
) -> CompareResult:
    """Dispatcher principal. `kind` decide o caminho (logic/number/enum/string).
    `dialect` so importa para `kind="logic"`."""
    if kind == "logic":
        return compare_logic(a_raw, b_raw, dialect)
    if kind == "number":
        return compare_number(a_raw, b_raw)
    if kind == "enum":
        return compare_enum(a_raw, b_raw)
    if kind == "string":
        return compare_string(a_raw, b_raw)
    if kind == "set_list":
        return compare_set_list(a_raw, b_raw)
    raise ValueError(f"kind desconhecido: {kind!r}")


def compare_n(
    values: list[str],
    *,
    kind: Kind,
    dialect: sp.Dialect = "keyword",
) -> tuple[Verdict, str | None]:
    """Compara N >= 2 valores. Veredicto global eh o "pior" entre todos os
    pares (a ordem de severidade eh EQUAL < EQUAL_LOGIC_DIFF_COMMENT <
    EQUIVALENT < DIFFERENT).

    Notas dos pares sao agregadas (primeira nao-vazia ganha) -- isso eh
    suficiente para o UI rotular o estado mais agressivo entre os reles.
    """
    if len(values) < 2:
        return ("EQUAL", None)

    severity: dict[Verdict, int] = {
        "EQUAL": 0,
        "EQUAL_LOGIC_DIFF_COMMENT": 1,
        "EQUIVALENT": 2,
        "DIFFERENT": 3,
    }
    worst: Verdict = "EQUAL"
    note: str | None = None
    a = values[0]
    for b in values[1:]:
        r = compare(a, b, kind=kind, dialect=dialect)
        if severity[r.verdict] > severity[worst]:
            worst = r.verdict
            note = r.note
        elif severity[r.verdict] == severity[worst] and note is None:
            note = r.note
    return (worst, note)
