"""How a SELOGIC equation is read, in both of SEL's incompatible syntaxes.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

Why they matter: `core/selogic_parser.py` is the front half of Settings
Compare. A parse that goes wrong does not raise anything the user sees -- it
returns `ParseError`, the comparator falls back to comparing the two equations
as TEXT, and two logically identical settings written by two engineers are
reported as DIFFERENT. The opposite is worse: a parse that accepts the wrong
grammar can canonicalise two different equations into one and report a real
divergence as EQUAL.

Two syntaxes share one AST:

* **symbolic** (3xx): ``*`` AND, ``+`` OR, ``!`` NOT, ``/X`` rising edge,
  ``\\X`` falling edge;
* **keyword** (4xx/7xx): ``AND``/``OR``/``NOT``/``R_TRIG``/``F_TRIG``, plus
  ``#`` comments.

The dialect is not a hint -- ``A + B`` is an OR in one and a syntax error in
the other, and that asymmetry is what these tests guard.
"""

from __future__ import annotations

import pytest

from selfiles.selogic import parser as sp


def _repr(expr: str, dialect: sp.Dialect) -> str:
    return sp.node_repr(sp.parse(expr, dialect))


def _canon(expr: str, dialect: sp.Dialect = "keyword") -> str:
    return sp.node_repr(sp.canonicalize(sp.parse(expr, dialect)))


# -----------------------------------------------------------------------------
# The keyword dialect (4xx / 7xx)
# -----------------------------------------------------------------------------

class TestKeywordDialect:

    def test_and_binds_tighter_than_or(self):
        """`A AND B OR C` is `(A AND B) OR C`. Fails if `_parse_or` and
        `_parse_and` swap places -- every mixed equation in the corpus would be
        re-associated and the comparator would report equivalences that are
        not there."""
        assert _repr("A AND B OR C", "keyword") == "(A & B) | C"

    def test_parentheses_override_the_precedence(self):
        """Fails if `_parse_primary` stops recursing into `_parse_or` inside
        parentheses."""
        assert _repr("A AND (B OR C)", "keyword") == "A & (B | C)"

    def test_an_operator_chain_is_flattened_into_one_n_ary_node(self):
        """`A OR B OR C` is one `Or` of three, not nested pairs -- that is what
        makes reordering detectable by the canonical form alone, without the
        truth table. Fails if the while-loop becomes a right fold."""
        node = sp.parse("A OR B OR C", "keyword")
        assert isinstance(node, sp.Or) and len(node.children) == 3

    def test_keywords_are_case_insensitive_but_bit_names_are_not(self):
        """`_tokenize` upper-cases only to TEST for a keyword; an identifier
        keeps the case it was written in. So `a and b` is a valid AND of two
        atoms named `a` and `b`, and those are NOT the atoms `A` and `B`.

        Pinned rather than fixed: settings files write Relay Word names in
        upper case, so the asymmetry is invisible in practice, and a
        case-folding of atom names would be a behaviour change, not a test
        change.

        Fails if the keyword `.upper()` goes (a lower-case `and` becomes an
        atom and the expression stops parsing) or if atom names start being
        folded (which would make this pass differently)."""
        assert _repr("a and b", "keyword") == "a & b"
        assert sp.atoms(sp.parse("a and b", "keyword")) == {"a", "b"}
        assert _repr("A AND B", "keyword") == "A & B"

    def test_a_comment_is_stripped_before_tokenising(self):
        """`#` starts a comment to end of line, and a comment must not change
        the logic. Fails if the strip goes -- every commented equation in a 4xx
        would become a ParseError and fall back to text comparison."""
        assert _repr("A AND B # trava de local", "keyword") == "A & B"

    def test_the_boolean_literals_are_only_zero_and_one(self):
        """`PSV01 := 1` is how SEL writes 'always true'. Fails if `Literal`
        stops being produced -- `1` would become an atom that evaluates False,
        inverting the meaning of every hard-wired enable in the project."""
        assert sp.evaluate(sp.parse("1", "keyword"), {}) is True
        assert sp.evaluate(sp.parse("0", "keyword"), {}) is False

    def test_a_number_that_is_not_a_boolean_literal_is_refused(self):
        """`PCT07PU := 0.000000` is a MATH setting that happens to sit in a
        SELOGIC-shaped field. Refusing it is what routes it to the numeric
        comparator instead. Fails if the digit check loosens -- `12` would
        become an atom and `0.5` vs `0.500000` would compare as logic."""
        with pytest.raises(sp.ParseError):
            sp.parse("12", "keyword")
        with pytest.raises(sp.ParseError):
            sp.parse("0.000000", "keyword")

    def test_a_math_operator_is_refused_in_the_keyword_dialect(self):
        """`(IA+IB)/3` is an equation, not a logic expression. Fails if `+-*/`
        stop raising: `IA+IB` would parse as an OR of two analog names."""
        for expr in ["IA + IB", "IA * IB", "IA / 3", "A <= B"]:
            with pytest.raises(sp.ParseError):
                sp.parse(expr, "keyword")


# -----------------------------------------------------------------------------
# The symbolic dialect (3xx)
# -----------------------------------------------------------------------------

class TestSymbolicDialect:

    def test_star_is_and_and_plus_is_or(self):
        """The 351-series syntax. Fails if the symbolic branch of `_tokenize`
        is removed -- `+` would hit the math-operator guard and every 3xx
        equation in the project would stop parsing."""
        assert _repr("!IN101*IN102", "symbolic") == "!IN101 & IN102"
        assert _repr("Z1T+Z2T", "symbolic") == "Z1T | Z2T"

    def test_the_two_dialects_build_the_same_tree(self):
        """The point of having one AST: downstream never asks which family an
        equation came from. Fails if either grammar drifts."""
        assert (_repr("!A*B+C", "symbolic")
                == _repr("NOT A AND B OR C", "keyword"))

    def test_the_keyword_spellings_are_plain_atoms_in_the_symbolic_dialect(self):
        """`AND` is a legal Relay Word name, and in a 3xx it is just a name.
        `A AND B` therefore parses as three atoms in a row and is refused.

        Fails if the keyword table is applied to both dialects -- a 3xx
        equation mentioning a bit called `OR` would silently change meaning."""
        assert _repr("AND", "symbolic") == "AND"
        with pytest.raises(sp.ParseError):
            sp.parse("A AND B", "symbolic")


# -----------------------------------------------------------------------------
# Edge triggers
# -----------------------------------------------------------------------------

class TestEdgeTriggers:

    def test_an_edge_becomes_one_atom_with_a_prefixed_name(self):
        """`R_TRIG X` depends on the HISTORY of X, not on its present value, so
        it cannot be modelled by the truth table. Folding it into an atom named
        `R_TRIG:X` makes the table treat it as independent of `X`, which is
        exactly right for equivalence.

        Fails if the prefix is dropped: `R_TRIG X` and `X` would then be the
        same atom, and `PLT11S := R_TRIG VB002` would compare EQUAL to
        `PLT11S := VB002` -- a latch that pulses versus one that holds."""
        assert sp.parse("R_TRIG VB002", "keyword") == sp.Atom("R_TRIG:VB002")
        assert sp.parse("F_TRIG VB002", "keyword") == sp.Atom("F_TRIG:VB002")

    def test_the_symbolic_slashes_map_to_the_same_atoms(self):
        """`/X` is `R_TRIG X` and `\\X` is `F_TRIG X`. Fails if the two
        dialects stop agreeing on the canonical name -- comparing a 3xx to a
        4xx is not something this tool does, but the canonical form is also
        what `atoms()` reports to the >16 cutoff."""
        assert sp.parse("/VB002", "symbolic") == sp.Atom("R_TRIG:VB002")
        assert sp.parse("\\VB002", "symbolic") == sp.Atom("F_TRIG:VB002")

    def test_an_edge_atom_is_independent_of_its_bare_bit(self):
        """Fails if the fold starts sharing the name -- the truth table would
        be forced to give `X` and `R_TRIG X` the same value, and would then
        'prove' equivalences that do not hold."""
        node = sp.parse("X AND R_TRIG X", "keyword")
        assert sp.atoms(node) == {"X", "R_TRIG:X"}
        assert sp.evaluate(node, {"X": True, "R_TRIG:X": False}) is False

    def test_the_argument_of_an_edge_must_be_a_single_identifier(self):
        """Per the 411L manual (p.1273) and the 751 manual (p.365), `R_TRIG`
        takes a bit, not an expression. Fails if parentheses are accepted --
        `R_TRIG (A OR B)` would build a tree the relay would never accept and
        the comparator would validate settings the relay rejects."""
        with pytest.raises(sp.ParseError):
            sp.parse("R_TRIG (A OR B)", "keyword")
        with pytest.raises(sp.ParseError):
            sp.parse("/(A+B)", "symbolic")


# -----------------------------------------------------------------------------
# Malformed input
# -----------------------------------------------------------------------------

class TestParseErrors:

    @pytest.mark.parametrize("expr", [
        "", "   ", "# so um comentario",     # nothing left after the strip
        "(A", "A)", "A AND", "AND B", "A @ B",
    ])
    def test_malformed_input_raises_rather_than_guessing(self, expr):
        """`ParseError` is a ROUTING decision, not a failure: the comparator
        catches it and falls back to text. Fails if any of these starts parsing
        into a partial tree -- half an equation compared as if it were the
        whole one."""
        with pytest.raises(sp.ParseError):
            sp.parse(expr, "keyword")

    def test_is_boolean_expression_never_raises(self):
        """The predicate form, used to pick a comparison strategy. Fails if it
        stops catching."""
        assert sp.is_boolean_expression("A AND B", "keyword") is True
        assert sp.is_boolean_expression("(IA+IB)/3", "keyword") is False


# -----------------------------------------------------------------------------
# Canonicalisation and evaluation
# -----------------------------------------------------------------------------

class TestCanonicalize:

    def test_reordered_operands_reach_the_same_canonical_form(self):
        """The cheap half of equivalence: sorting the children of a commutative
        node catches the reordering that two engineers typing the same logic
        produce, without enumerating 2^N rows.

        Fails if the sort goes -- every such pair would fall through to the
        truth table, and above 16 atoms it would be reported DIFFERENT."""
        assert _canon("B AND A") == _canon("A AND B") == "A & B"

    def test_a_double_negation_collapses(self):
        """Fails if the `isinstance(inner, Not)` fold goes."""
        assert sp.canonicalize(sp.Not(sp.Not(sp.Atom("A")))) == sp.Atom("A")

    def test_nesting_of_the_same_operator_is_flattened(self):
        """`A AND (B AND C)` and `(A AND B) AND C` are one node of three.
        Fails if the flatten goes: the two spellings would only be reconciled
        by the truth table."""
        assert _canon("A AND (B AND C)") == _canon("(A AND B) AND C") == "A & B & C"

    def test_an_or_inside_an_and_is_not_flattened(self):
        """Only same-operator nesting collapses -- flattening across operators
        would change the meaning. The sort key is the child's own `repr`, so
        the `Or` sorts under `"B | C"` and lands after `"A"`, and the parent's
        `_wrap` re-adds the parentheses.

        Fails if the `isinstance` check on the child loosens, or if the sort
        key changes -- the canonical string is compared literally, so two
        equivalent trees sorted differently would be reported DIFFERENT."""
        assert _canon("A AND (B OR C)") == "A & (B | C)"

    def test_repeated_operands_are_kept(self):
        """`A AND A` canonicalises to `A & A`, NOT to `A`. Deliberate: the
        canonical form is a cheap pre-filter, and idempotence is left to the
        truth table. Fails if a de-duplication is added -- which would be a
        real improvement, and would need this test rewritten."""
        assert _canon("A AND A") == "A & A"

    def test_the_degenerate_nodes_have_identity_values(self):
        """An empty AND is true and an empty OR is false; a one-child node
        unwraps. Unreachable from `parse`, but `canonicalize` is public and
        recursion can produce them. Fails if the identities are swapped."""
        assert sp.canonicalize(sp.And(())) == sp.Literal(True)
        assert sp.canonicalize(sp.Or(())) == sp.Literal(False)
        assert sp.canonicalize(sp.And((sp.Atom("A"),))) == sp.Atom("A")

    def test_canonicalize_recurses_into_children(self):
        """Fails if only the top node is normalised -- a reordering one level
        down would escape the cheap path."""
        assert _canon("X OR (B AND A)") == _canon("(A AND B) OR X")


class TestEvaluate:

    def test_an_atom_absent_from_the_environment_is_false(self):
        """The truth table only enumerates the atoms in the UNION of two
        equations, so one side is routinely evaluated with atoms it does not
        contain. Fails if the default becomes True or a KeyError."""
        assert sp.evaluate(sp.parse("A AND B", "keyword"), {"A": True}) is False

    def test_the_operators_mean_what_they_say(self):
        """Fails if `all`/`any` are swapped, which would make every AND in the
        project compare as an OR."""
        node = sp.parse("NOT A AND (B OR C)", "keyword")
        assert sp.evaluate(node, {"A": False, "B": True, "C": False}) is True
        assert sp.evaluate(node, {"A": True, "B": True, "C": False}) is False
        assert sp.evaluate(node, {"A": False, "B": False, "C": False}) is False

    def test_atoms_collects_every_name_once(self):
        """This set is what the >16 cutoff counts. Fails if literals start
        being collected, or if a branch of the walk is dropped -- an
        undercount would enumerate a table missing a variable."""
        node = sp.parse("A AND NOT (B OR A) OR 1", "keyword")
        assert sp.atoms(node) == {"A", "B"}
