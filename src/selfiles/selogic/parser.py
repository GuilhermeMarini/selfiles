r"""
Parser de equacoes SELOGIC com suporte aos dois dialetos.

SEL usa duas sintaxes incompativeis para suas equacoes booleanas:

  - **Symbolic** (familias 3xx / 351-series): `*` AND, `+` OR, `!` NOT,
    `/X` borda de subida, `\X` borda de descida. Sem palavras-chave.
    Ex.: `52A := !IN101*IN102` ou `TR := Z1T+Z2T+/SV3`.

  - **Keyword** (familias 4xx / 7xx / 400-series): `AND`, `OR`, `NOT`,
    `R_TRIG X`, `F_TRIG X`. `#` comecando um comentario ate o fim da linha.
    Ex.: `PLT11S := R_TRIG VB002 # latch set`.

Os dois dialetos produzem o mesmo AST -- a comparacao downstream nao precisa
saber de qual familia vieram as equacoes (a familia eh checada no UI, antes
do diff).

Tratamento de bordas (`R_TRIG`/`F_TRIG`/`/`/`\`):
Sao operadores stateful que dependem da historia do bit, nao da combinacao
booleana. Para comparacao de equivalencia, tratamos um `R_TRIG X` ou `/X`
como um *atomo distinto* (nome canonico "R_TRIG:X"). Assim duas equacoes
que so diferem na ordem dos operandos sao detectadas como equivalentes via
tabela verdade, sem precisar modelar a temporalidade do trigger.

API publica:

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

# `Literal` e' o nome do NO da arvore (um literal booleano do SELOGIC), e ele
# e' declarado mais abaixo neste mesmo modulo. O `typing.Literal` entra com
# outro nome de proposito: sem isso o segundo sombreia o primeiro a partir da
# linha da classe, e qualquer anotacao `Literal[...]` escrita daqui pra baixo
# passaria a significar o dataclass em vez do tipo.
Dialect: TypeAlias = _TypingLiteral["symbolic", "keyword"]


class ParseError(Exception):
    r"""Levantada quando a expressao nao casa com a gramatica booleana SELOGIC.

    Causas tipicas:
      - expressao matematica (ex.: `(IA+IB)/3`) em contexto que esperava boolean
      - operador desconhecido (ex.: `<=`, `<>`)
      - caractere nao reconhecido
      - parenteses desbalanceados
      - argumento invalido pra R_TRIG/F_TRIG/`/`/`\` (deve ser um identificador unico)
    """


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Atom:
    """Identificador (relay-word bit). Pode incluir um prefixo de borda no
    nome canonico, e.g. `R_TRIG:VB002` -- a borda eh dobrada no atomo para
    que tabela-verdade trate-a como independente."""
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
        # Sempre paren-wrap o filho para nao ambiguidade textual.
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
    """Tokeniza a expressao. Strip de comentario `# ...` ate o fim.

    Comentarios so existem no dialeto keyword (4xx/7xx). No symbolic, qualquer
    `#` seria erro -- mas como o caller normalmente ja strip-a o comentario
    antes de chamar, ainda assim aceitamos para robustez.
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

        # Identificador / numero
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
            # Detecta numero literal (0 ou 1) para representar boolean constante.
            # Qualquer outro numero (e.g., 0.5, 12) pode aparecer em equacoes
            # matematicas -- e.g., `PCT07PU := 0.000000`. Tratamos como ParseError
            # via flag: caller decide se a equacao e matematica.
            if text.isdigit() and text in ("0", "1"):
                toks.append(Tok("NUM", text, i))
                i = m.end()
                continue
            if text.isdigit():
                # Numero inteiro >1: nao e boolean. Sinalize ao caller.
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

        # Operadores math/comparison nao suportados em boolean
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
            # Argumento DEVE ser um identificador unico (per manual 411L p.1273
            # e 751 p.365). Nao aceitar parenteses.
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
    """Tokeniza e parseia. ParseError se nao for booleano valido."""
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
    """Conjunto de nomes de atomos referenciados no AST."""
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
    """Avalia AST sob um mapeamento atomo->bool. Atomos ausentes => False."""
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
    """Normaliza:
      - NOT NOT x -> x
      - flatten AND-of-AND e OR-of-OR
      - sort children de AND/OR pelo repr canonico
      - And(()) -> Literal(True), Or(()) -> Literal(False), And((x,)) -> x

    Nao aplica leis distributivas/De Morgan -- isso fica para a tabela verdade.
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
