"""The four verdicts Settings Compare puts next to a setting.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

Why they matter: the verdict is the whole product of the tool. An engineer
scans a diff of two relays and looks only at the rows that are not green, so a
DIFFERENT reported as EQUIVALENT is a divergence nobody will ever open again --
and a settings difference between two relays of a protection scheme is the kind
of thing that is found later, by a mis-operation.

Severity is ordered `EQUAL < EQUAL_LOGIC_DIFF_COMMENT < EQUIVALENT <
DIFFERENT`, and the tests below cover each verdict plus the two boundaries that
decide them: the `#`-comment split, and the 16-atom ceiling on the truth-table
fallback (`_MAX_TRUTH_ATOMS`, documented in `PLAN-settings-compare.md`).
"""

from __future__ import annotations

import pytest

from selfiles.selogic import compare as lc


def _v(result) -> str:
    return result.verdict


#: 16 and 17 distinct atom names, for the truth-table boundary.
N16 = [f"A{i:02d}" for i in range(16)]
N17 = [f"A{i:02d}" for i in range(17)]


def _dup(names: list[str]) -> str:
    """`A00 AND A00 AND A01 AND ...` -- logically the same as `_plain`, but a
    DIFFERENT canonical string, because `canonicalize` deliberately does not
    de-duplicate. Forces the comparison down to the truth table."""
    return " AND ".join([names[0]] + names)


def _plain(names: list[str]) -> str:
    return " AND ".join(names)


# -----------------------------------------------------------------------------
# The four verdicts
# -----------------------------------------------------------------------------

class TestVerdicts:

    def test_equal_is_the_same_text(self):
        """Fails if the fast path goes -- every identical setting in the
        project would be run through the parser and the truth table."""
        assert _v(lc.compare_logic("PLT01 AND VB002", "PLT01 AND VB002",
                                   "keyword")) == "EQUAL"

    def test_equal_ignores_whitespace(self):
        """QuickSet does not preserve spacing between edits. Fails if
        `_normalize_whitespace` goes: two saves of an untouched setting would
        report as different."""
        assert _v(lc.compare_logic("A AND  B", " A AND B ", "keyword")) == "EQUAL"

    def test_equal_logic_diff_comment_is_its_own_verdict(self):
        """Same logic, different `#` comment. It is NOT `EQUAL` (an engineer
        may want to reconcile the documentation) and NOT `DIFFERENT` (the relay
        behaves identically). Fails if the comment split goes -- the row would
        turn red and the real differences would be lost in the noise."""
        assert _v(lc.compare_logic("A AND B # trava local",
                                   "A AND B # trava remota",
                                   "keyword")) == "EQUAL_LOGIC_DIFF_COMMENT"

    def test_a_comment_on_only_one_side_is_still_only_a_comment(self):
        """Fails the same way. This is the common case: one engineer documents
        the equation and the other does not."""
        assert _v(lc.compare_logic("A AND B", "A AND B # trava",
                                   "keyword")) == "EQUAL_LOGIC_DIFF_COMMENT"

    def test_an_empty_comment_is_no_comment(self):
        """A trailing `#` with nothing after it. Fails if the split stops
        stripping -- a stray `#` would downgrade an EQUAL row."""
        assert _v(lc.compare_logic("A AND B", "A AND B #",
                                   "keyword")) == "EQUAL"

    def test_equivalent_by_canonical_form_when_operands_are_reordered(self):
        """The cheap path: sorted children of a commutative node. Fails if
        `canonicalize` stops sorting."""
        assert _v(lc.compare_logic("VB002 AND PLT01", "PLT01 AND VB002",
                                   "keyword")) == "EQUIVALENT"

    def test_equivalent_by_truth_table_when_the_canonical_forms_differ(self):
        """De Morgan: `NOT (A OR B)` and `NOT A AND NOT B` canonicalise to
        different strings and are the same function. Only the exhaustive table
        catches it.

        Fails if the truth-table fallback goes -- every algebraic rewrite in
        the corpus would be reported DIFFERENT."""
        assert _v(lc.compare_logic("NOT (A OR B)", "NOT A AND NOT B",
                                   "keyword")) == "EQUIVALENT"

    def test_different_when_one_assignment_disagrees(self):
        """The table returns on the FIRST disagreeing row. Fails if the early
        return goes (slow) or if the comparison is inverted (catastrophic)."""
        assert _v(lc.compare_logic("A AND B", "A OR B", "keyword")) == "DIFFERENT"

    def test_an_edge_trigger_is_not_its_bare_bit(self):
        """`R_TRIG VB002` folds to an atom independent of `VB002`, so the table
        can assign them separately and finds a row where they disagree.

        Fails if the fold goes: a latch that pulses would compare EQUIVALENT to
        one that holds -- which is a protection difference, not a style one."""
        assert _v(lc.compare_logic("R_TRIG VB002", "VB002",
                                   "keyword")) == "DIFFERENT"


# -----------------------------------------------------------------------------
# The truth-table ceiling
# -----------------------------------------------------------------------------

class TestTruthTableBoundary:

    def test_sixteen_atoms_are_still_verified_exhaustively(self):
        """`> _MAX_TRUTH_ATOMS` is the cutoff, so 16 is INSIDE it: 65,536 rows,
        about a tenth of a second. This pair is equivalent only by idempotence
        (`A00 AND A00` is `A00`), which the canonical form does not see.

        Fails if the ceiling drops below 16 -- real equivalences would start
        being reported DIFFERENT with a note nobody reads."""
        got = lc.compare_logic(_dup(N16), _plain(N16), "keyword")
        assert got.verdict == "EQUIVALENT"
        assert got.note is None

    def test_seventeen_atoms_are_reported_different_with_a_caveat(self):
        """The same equivalence, one atom over the line: 2^17 rows is where the
        cost stops being worth it, so the answer becomes DIFFERENT *with the
        note saying it was not checked*. That note is the honest part -- the
        verdict alone would be a lie.

        Fails if the note goes (a DIFFERENT indistinguishable from a proven
        one) or if the ceiling is raised without measuring the cost."""
        got = lc.compare_logic(_dup(N17), _plain(N17), "keyword")
        assert got.verdict == "DIFFERENT"
        assert "nao verificado exaustivamente" in (got.note or "")
        assert ">16 atomos" in got.note

    def test_a_genuine_difference_above_the_ceiling_is_still_different(self):
        """The cutoff only loses EQUIVALENT verdicts, never DIFFERENT ones --
        which is the safe direction. Fails if the >16 branch ever returns
        anything but DIFFERENT."""
        got = lc.compare_logic(_plain(N17), " OR ".join(N17), "keyword")
        assert got.verdict == "DIFFERENT"


# -----------------------------------------------------------------------------
# Non-boolean values
# -----------------------------------------------------------------------------

class TestNonBooleanLogic:

    def test_an_unparseable_pair_is_compared_as_text(self):
        """`(IA+IB)/3` is a math equation living in a SELOGIC-shaped field.
        Fails if `ParseError` stops being caught -- the whole comparison would
        raise on the first math setting in the file."""
        got = lc.compare_logic("(IA+IB)/3", "(IA+IC)/3", "keyword")
        assert got.verdict == "DIFFERENT"
        assert got.note == "nao parseavel como booleano"

    def test_the_text_coincidence_branch_is_unreachable_from_the_public_api(self):
        """`_compare_logic` answers EQUIVALENT with its "matching text, no
        parse" note when two unparseable bodies normalise to the same text -- but
        `compare_logic` already returned EQUAL for that case before calling it,
        so through the public API the branch is DEAD.

        Pinned by calling the private function directly, and reported rather
        than removed: Phase 3 changes no production code.

        Fails if `compare_logic` stops normalising whitespace before the
        equality check (the branch would come alive) or if the branch is
        deleted (which is the tidy-up)."""
        assert lc.compare_logic("(IA+IB)/3", "(IA+IB)/3", "keyword").verdict == "EQUAL"
        got = lc._compare_logic("(IA+IB)/3", "(IA+IB)/3", "keyword")
        assert got.verdict == "EQUIVALENT"
        assert got.note == "texto coincidente (sem parse)"


class TestNumbers:

    def test_identical_text_is_equal_not_merely_equivalent(self):
        """Fails if the string fast path goes -- every unchanged numeric
        setting would be labelled 'numericamente iguais' instead of equal, and
        the diff would look like it found something."""
        assert _v(lc.compare_number("5.000000", "5.000000")) == "EQUAL"

    def test_the_same_value_written_differently_is_equivalent(self):
        """`0` and `0.000000` are one setting; QuickSet writes both. Fails if
        the float parse goes -- most numeric rows in a real comparison would
        turn red."""
        got = lc.compare_number("0", "0.000000")
        assert got.verdict == "EQUIVALENT"
        assert got.note == "numericamente iguais"

    def test_the_tolerance_is_relative_as_well_as_absolute(self):
        """`max(abs_tol, rel_tol * max(|a|,|b|))`. Fails if the relative term
        goes: 1 count of float noise on a 1e6 setting would read as a real
        difference."""
        assert _v(lc.compare_number("1000000", "1000001")) == "EQUIVALENT"
        assert _v(lc.compare_number("1.0", "1.1")) == "DIFFERENT"

    def test_something_that_is_not_a_number_is_different_not_an_exception(self):
        """A field declared numeric can hold `OFF`. Fails if `_try_float` stops
        catching -- one such value would abort the whole comparison."""
        assert _v(lc.compare_number("OFF", "5.0")) == "DIFFERENT"
        assert _v(lc.compare_number("OFF", "OFF")) == "EQUAL"


class TestEnumsAndStrings:

    def test_an_enum_is_compared_case_sensitively(self):
        """`Y` and `y` are DIFFERENT, deliberately: the relay's own enums are
        upper case and a lower-case value is a value someone typed. Fails if a
        `.upper()` is added -- pinned so that change is a decision, not a
        drive-by."""
        assert _v(lc.compare_enum("Y", "y")) == "DIFFERENT"
        assert _v(lc.compare_enum("Y", "Y")) == "EQUAL"

    def test_surrounding_whitespace_is_ignored(self):
        """Fails if the strip goes: trailing spaces in a settings file are
        invisible on screen and would produce an unexplainable red row."""
        assert _v(lc.compare_enum(" Y ", "Y")) == "EQUAL"

    def test_a_string_is_compared_like_an_enum(self):
        """`compare_string` delegates. Fails if the two diverge without a
        reason -- a `TID` differing only in case would change verdict."""
        assert lc.compare_string("SE TESTE", "SE teste") == lc.compare_enum(
            "SE TESTE", "SE teste")


class TestSetLists:

    def test_the_same_list_in_a_different_order_is_equivalent(self):
        """An SER list is a SET: the relay records these wordbits and the
        position in the file carries no meaning. Fails if the set comparison
        goes -- a reordered SER list, which QuickSet produces on its own, would
        be flagged as a settings difference."""
        got = lc.compare_set_list("TRIP, 50P1, 51P", "51P, TRIP, 50P1")
        assert got.verdict == "EQUIVALENT"
        assert got.note == "mesmo conjunto, ordem diferente"

    def test_identical_text_is_equal(self):
        """Fails if the fast path goes and every unchanged SER list is labelled
        'reordered'."""
        assert _v(lc.compare_set_list("TRIP, 50P1", "TRIP,  50P1")) == "EQUAL"

    def test_the_delimiters_are_commas_spaces_tabs_and_semicolons(self):
        """SER lists are CSV and ALIAS lists are space-separated, in the same
        code path. Fails if `_parse_set_list` narrows to one delimiter."""
        assert _v(lc.compare_set_list("TRIP 50P1;51P", "51P,TRIP\t50P1")
                  ) == "EQUIVALENT"

    def test_a_real_difference_names_what_is_missing_on_each_side(self):
        """The note is what makes the row actionable -- a bare DIFFERENT on a
        60-entry SER list tells the engineer nothing. Fails if the note goes or
        the two sides are swapped."""
        got = lc.compare_set_list("TRIP, 50P1", "TRIP, 51P")
        assert got.verdict == "DIFFERENT"
        assert got.note == "sobra em A: 50P1 | sobra em B: 51P"

    def test_a_long_difference_is_truncated_with_a_count(self):
        """Six entries then `+N`, so the note stays readable in a table cell.
        Fails if the cap changes or the remainder count goes."""
        got = lc.compare_set_list(" ".join(f"B{i}" for i in range(9)), "X")
        assert got.note.startswith("sobra em A: B0, B1, B2, B3, B4, B5, +3")
        assert "sobra em B: X" in got.note


# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------

class TestDispatch:

    @pytest.mark.parametrize("kind,a,b,want", [
        ("logic", "A AND B", "B AND A", "EQUIVALENT"),
        ("number", "0", "0.000000", "EQUIVALENT"),
        ("enum", "Y", "N", "DIFFERENT"),
        ("string", "SE A", "SE A", "EQUAL"),
        ("set_list", "A B", "B A", "EQUIVALENT"),
    ])
    def test_each_kind_reaches_its_own_comparator(self, kind, a, b, want):
        """`kind` comes from `core/settings_catalog.py`; a wrong route silently
        compares a number as a string (`0` vs `0.000000` turns red) or a
        string as logic (`SE TESTE` fails to parse). Fails if a branch is
        dropped from the dispatcher."""
        assert _v(lc.compare(a, b, kind=kind)) == want

    def test_an_unknown_kind_raises_rather_than_defaulting(self):
        """A typo in the catalogue must surface at once. Fails if a fallback is
        added -- the setting would be compared by whatever the default is, and
        nothing would say so."""
        with pytest.raises(ValueError):
            lc.compare("a", "b", kind="coisa_nova")

    def test_the_dialect_only_reaches_the_logic_comparator(self):
        """`*` is AND in the symbolic dialect and a syntax error in keyword.
        Fails if `dialect` starts leaking into the other kinds."""
        assert _v(lc.compare("A*B", "B*A", kind="logic",
                             dialect="symbolic")) == "EQUIVALENT"
        assert _v(lc.compare("A*B", "B*A", kind="logic",
                             dialect="keyword")) == "DIFFERENT"


class TestCompareN:

    def test_fewer_than_two_values_is_trivially_equal(self):
        """Comparing one relay against nothing. Fails if the guard goes and
        `values[0]` raises on an empty list."""
        assert lc.compare_n([], kind="enum") == ("EQUAL", None)
        assert lc.compare_n(["Y"], kind="enum") == ("EQUAL", None)

    def test_the_worst_verdict_among_the_pairs_wins(self):
        """The row's colour must be the most alarming thing in it. Fails if the
        severity table is reordered -- an EQUAL_LOGIC_DIFF_COMMENT next to a
        DIFFERENT would paint the row green."""
        verdict, _ = lc.compare_n(
            ["A AND B", "A AND B # nota", "A OR B"], kind="logic")
        assert verdict == "DIFFERENT"

    def test_the_note_travels_with_the_worst_verdict(self):
        """Fails if the note is taken from the first pair instead of the worst
        one -- the cell would explain a verdict it is not showing."""
        verdict, note = lc.compare_n(["0", "0.000000", "OFF"], kind="number")
        assert verdict == "DIFFERENT"
        assert note is None            # the DIFFERENT pair carries no note

    def test_every_value_is_compared_against_the_first_not_pairwise(
            self, monkeypatch):
        """`compare_n` is a STAR comparison: `values[0]` against each of the
        rest, N-1 comparisons rather than N*(N-1)/2. For equality that is the
        same answer; for EQUIVALENT it need not be, since equivalence here is
        decided by a bounded search that can give up.

        Pinned because the cost model depends on it: a 20-relay comparison is
        19 comparisons, not 190. Fails if it becomes an all-pairs loop."""
        calls = []
        real = lc.compare

        def spy(a, b, **kw):
            calls.append((a, b))
            return real(a, b, **kw)

        monkeypatch.setattr(lc, "compare", spy)
        lc.compare_n(["A", "B", "C", "D"], kind="enum")
        assert calls == [("A", "B"), ("A", "C"), ("A", "D")]
