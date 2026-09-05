r"""
Parser for SELOGIC equations, in both dialects.

SEL uses two incompatible syntaxes for its boolean equations:

  - **Symbolic** (3xx / 351 series): `*` for AND, `+` for OR, `!` for NOT,
    `/X` for a rising edge, `\X` for a falling edge. No keywords.
    e.g. `52A := !IN101*IN102` or `TR := Z1T+Z2T+/SV3`.

  - **Keyword** (4xx / 7xx / 400 series): `AND`, `OR`, `NOT`, `R_TRIG X`,
    `F_TRIG X`, and `#` starting a comment that runs to end of line.
    e.g. `PLT11S := R_TRIG VB002 # latch set`.

Both dialects produce the same AST, so nothing downstream needs to know which
family an equation came from (the family is checked in the UI, before the
diff).

How edges are handled (`R_TRIG` / `F_TRIG` / `/` / `\`): they are stateful
operators, depending on the bit's history rather than on the boolean
combination. For equivalence comparison, `R_TRIG X` and `/X` are treated as a
*distinct atom* (canonical name "R_TRIG:X"). Two equations that differ only in
operand order are then found equivalent by the truth table, without having to
model the trigger's behaviour over time.

Public API:

    parse(expr, dialect) -> Node                  (raises ParseError)
    is_boolean_expression(expr, dialect) -> bool
    atoms(node) -> set[str]
    evaluate(node, env) -> bool
    canonicalize(node) -> Node
    node_repr(node) -> str                        (forma textual canonica)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal as _TypingLiteral
from typing import TypeAlias

# `Literal` is the name of a tree NODE here (a SELOGIC boolean literal),
# declared further down in this same module. `typing.Literal` comes in under
# another name on purpose: without that, the class shadows the type from its
# own line onwards, and any `Literal[...]` annotation written below would
# quietly mean the dataclass instead of the type.
Dialect: TypeAlias = _TypingLiteral["symbolic", "keyword"]


class ParseError(Exception):
    r"""Raised when the expression does not match the SELOGIC boolean grammar.

    Typical causes:
      - a mathematical expression (`(IA+IB)/3`) where a boolean was expected
      - an unknown operator (`<=`, `<>`)
      - an unrecognised character
      - unbalanced parentheses
      - an invalid argument to R_TRIG/F_TRIG/`/`/`\` (it must be a single
        identifier)
    """


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Atom:
    """An identifier (a Relay Word bit). The canonical name may carry an edge
    prefix, e.g. `R_TRIG:VB002` -- the edge is folded into the atom so the
    truth table treats it as independent."""
    name: str

    def __repr__(self) -> str:  # canonico
        return self.name


@dataclass(frozen=True)
class Literal:
    """Literal booleano `0` ou `1`."""
    value: bool

    def __repr__(self) -> str:
        return "1" if self.value else "0"


@dataclass(frozen=True)
class Not:
    child: Node

    def __repr__(self) -> str:
        # Always parenthesise the child, so the text is never ambiguous.
        return f"!{_wrap(self.child)}"


@dataclass(frozen=True)
class And:
    """AND n-ario; flatten faz parte da canonicalizacao."""
    children: tuple[Node, ...]

    def __repr__(self) -> str:
        return " & ".join(_wrap(c) for c in self.children)


@dataclass(frozen=True)
class Or:
    """OR n-ario; flatten faz parte da canonicalizacao."""
    children: tuple[Node, ...]

    def __repr__(self) -> str:
        return " | ".join(_wrap(c) for c in self.children)


Node: TypeAlias = Atom | Literal | Not | And | Or


def _wrap(n: Node) -> str:
    if isinstance(n, (Atom, Literal, Not)):
        return repr(n)
    return f"({n!r})"


def node_repr(n: Node) -> str:
    """Forma textual canonica do AST. Usado para comparar AST canonicalizado."""
    return repr(n)


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tok:
    kind: str    # 'LP', 'RP', 'AND', 'OR', 'NOT', 'RTRIG', 'FTRIG', 'IDENT', 'NUM'
    text: str
    pos: int


_IDENT_RE = re.compile(r"[A-Za-z0-9_]+")
_KEYWORDS = {"AND": "AND", "OR": "OR", "NOT": "NOT", "R_TRIG": "RTRIG", "F_TRIG": "FTRIG"}


def _tokenize(src: str, dialect: Dialect) -> list[Tok]:
    """Tokenise the expression, stripping a `# ...` comment to end of line.

    Comments exist only in the keyword dialect (4xx/7xx). In the symbolic one
    a `#` would be an error -- but since the caller has usually stripped the
    comment already, one is accepted here rather than refused.
    """
    # Strip do comentario (em ambos dialetos, defensivo)
    h = src.find("#")
    if h >= 0:
        src = src[:h]

    toks: list[Tok] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue

        if c == "(":
            toks.append(Tok("LP", c, i)); i += 1; continue
        if c == ")":
            toks.append(Tok("RP", c, i)); i += 1; continue

        if dialect == "symbolic":
            if c == "*":
                toks.append(Tok("AND", c, i)); i += 1; continue
            if c == "+":
                toks.append(Tok("OR", c, i)); i += 1; continue
            if c == "!":
                toks.append(Tok("NOT", c, i)); i += 1; continue
            if c == "/":
                toks.append(Tok("RTRIG", c, i)); i += 1; continue
            if c == "\\":
                toks.append(Tok("FTRIG", c, i)); i += 1; continue

        # Identifier or number
        m = _IDENT_RE.match(src, i)
        if m:
            text = m.group(0)
            # Keyword dialect: detecta palavras reservadas case-insensitive.
            if dialect == "keyword":
                up = text.upper()
                if up in _KEYWORDS:
                    toks.append(Tok(_KEYWORDS[up], up, i))
                    i = m.end()
                    continue
            # A literal 0 or 1 is a boolean constant. Any other number
            # (0.5, 12) belongs to a mathematical equation -- `PCT07PU :=
            # 0.000000`, say -- and is raised as a ParseError with a flag, so
            # the caller decides whether the equation is mathematical.
            if text.isdigit() and text in ("0", "1"):
                toks.append(Tok("NUM", text, i))
                i = m.end()
                continue
            if text.isdigit():
                # An integer above 1 is not boolean. Tell the caller.
                raise ParseError(
                    f"numero {text!r} nao e literal booleano (posicao {i})"
                )
            toks.append(Tok("IDENT", text, i))
            i = m.end()
            continue

        # Ponto/digit-context = expressao matematica (e.g. "0.000000")
        if c == "." or c.isdigit():
            raise ParseError(
                f"caractere {c!r} sugere expressao matematica (posicao {i})"
            )

        # Math and comparison operators have no meaning in a boolean
        if c in "+-*/<>=":
            raise ParseError(
                f"operador {c!r} nao valido no dialeto {dialect!r} (posicao {i})"
            )

        raise ParseError(f"caractere inesperado {c!r} na posicao {i}")

    return toks


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, toks: list[Tok], dialect: Dialect):
        self.toks = toks
        self.i = 0
        self.dialect = dialect

    def _peek(self) -> Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _eat(self, kind: str) -> Tok:
        t = self._peek()
        if t is None or t.kind != kind:
            got = "EOF" if t is None else f"{t.kind}={t.text!r}"
            raise ParseError(f"esperava {kind}, encontrou {got}")
        self.i += 1
        return t

    def parse_expr(self) -> Node:
        node = self._parse_or()
        if self.i != len(self.toks):
            t = self.toks[self.i]
            raise ParseError(f"token inesperado {t.text!r} na posicao {t.pos}")
        return node

    def _parse_or(self) -> Node:
        first = self._parse_and()
        tok = self._peek()
        if tok is None or tok.kind != "OR":
            return first
        children: list[Node] = [first]
        while (tok := self._peek()) is not None and tok.kind == "OR":
            self._eat("OR")
            children.append(self._parse_and())
        return Or(tuple(children))

    def _parse_and(self) -> Node:
        first = self._parse_unary()
        tok = self._peek()
        if tok is None or tok.kind != "AND":
            return first
        children: list[Node] = [first]
        while (tok := self._peek()) is not None and tok.kind == "AND":
            self._eat("AND")
            children.append(self._parse_unary())
        return And(tuple(children))

    def _parse_unary(self) -> Node:
        t = self._peek()
        if t is None:
            raise ParseError("expressao terminou cedo demais")
        if t.kind == "NOT":
            self._eat("NOT")
            return Not(self._parse_unary())
        if t.kind in ("RTRIG", "FTRIG"):
            self._eat(t.kind)
            # The argument MUST be a single identifier (411L manual p.1273,
            # 751 manual p.365). Parentheses are not accepted.
            arg = self._peek()
            if arg is None or arg.kind != "IDENT":
                raise ParseError(
                    f"argumento de {t.text!r} deve ser um identificador, "
                    f"encontrou {arg.text if arg else 'EOF'!r}"
                )
            self._eat("IDENT")
            prefix = "R_TRIG" if t.kind == "RTRIG" else "F_TRIG"
            return Atom(f"{prefix}:{arg.text}")
        return self._parse_primary()

    def _parse_primary(self) -> Node:
        t = self._peek()
        if t is None:
            raise ParseError("expressao terminou cedo demais")
        if t.kind == "LP":
            self._eat("LP")
            inner = self._parse_or()
            self._eat("RP")
            return inner
        if t.kind == "IDENT":
            self._eat("IDENT")
            return Atom(t.text)
        if t.kind == "NUM":
            self._eat("NUM")
            return Literal(t.text == "1")
        raise ParseError(f"token inesperado {t.text!r} na posicao {t.pos}")


def parse(expr: str, dialect: Dialect) -> Node:
    """Tokenise and parse. Raises ParseError if this is not a valid boolean."""
    toks = _tokenize(expr, dialect)
    if not toks:
        raise ParseError("expressao vazia")
    return _Parser(toks, dialect).parse_expr()


def is_boolean_expression(expr: str, dialect: Dialect) -> bool:
    """Retorna True se `expr` parseia limpo como expressao booleana."""
    try:
        parse(expr, dialect)
        return True
    except ParseError:
        return False


# ---------------------------------------------------------------------------
# Atomos, avaliacao, canonicalizacao
# ---------------------------------------------------------------------------

def atoms(node: Node) -> set[str]:
    """The set of atom names referenced anywhere in the AST."""
    out: set[str] = set()
    _collect_atoms(node, out)
    return out


def _collect_atoms(node: Node, out: set[str]) -> None:
    if isinstance(node, Atom):
        out.add(node.name)
    elif isinstance(node, Literal):
        return
    elif isinstance(node, Not):
        _collect_atoms(node.child, out)
    elif isinstance(node, (And, Or)):
        for c in node.children:
            _collect_atoms(c, out)


def evaluate(node: Node, env: dict[str, bool]) -> bool:
    """Evaluate the AST under an atom -> bool mapping. A missing atom is False."""
    if isinstance(node, Atom):
        return env.get(node.name, False)
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, Not):
        return not evaluate(node.child, env)
    if isinstance(node, And):
        return all(evaluate(c, env) for c in node.children)
    if isinstance(node, Or):
        return any(evaluate(c, env) for c in node.children)
    raise TypeError(f"no de AST desconhecido: {type(node).__name__}")


def canonicalize(node: Node) -> Node:
    """Normalise:
      - NOT NOT x -> x
      - flatten AND-of-AND and OR-of-OR
      - sort the children of AND/OR by their canonical repr
      - And(()) -> Literal(True), Or(()) -> Literal(False), And((x,)) -> x

    Distributive and De Morgan laws are deliberately NOT applied -- that is
    what the truth table is for.
    """
    if isinstance(node, Atom) or isinstance(node, Literal):
        return node
    if isinstance(node, Not):
        inner = canonicalize(node.child)
        if isinstance(inner, Not):
            return inner.child
        return Not(inner)
    if isinstance(node, And):
        flat: list[Node] = []
        for c in node.children:
            cc = canonicalize(c)
            if isinstance(cc, And):
                flat.extend(cc.children)
            else:
                flat.append(cc)
        if not flat:
            return Literal(True)
        if len(flat) == 1:
            return flat[0]
        flat.sort(key=lambda n: repr(n))
        return And(tuple(flat))
    if isinstance(node, Or):
        flat = []
        for c in node.children:
            cc = canonicalize(c)
            if isinstance(cc, Or):
                flat.extend(cc.children)
            else:
                flat.append(cc)
        if not flat:
            return Literal(False)
        if len(flat) == 1:
            return flat[0]
        flat.sort(key=lambda n: repr(n))
        return Or(tuple(flat))
    raise TypeError(f"no de AST desconhecido: {type(node).__name__}")
