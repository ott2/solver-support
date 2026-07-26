#!/usr/bin/env python3
"""Test solution file naming conventions."""

import pytest
from solver_support.file_management import (
    compute_tune_slot,
    compute_options_label,
    SolutionFileNamer,
    ObjectiveFileManager,
)


class TestComputeTuneSlot:
    """Test the filename tune slot computed from --tune / --options."""

    def test_default_when_nothing_requested(self):
        """No tune and no options is the default configuration: 'defopt'."""
        assert compute_tune_slot(None, None) == "defopt"
        assert compute_tune_slot(None, []) == "defopt"
        assert compute_tune_slot("", []) == "defopt"

    def test_tune_name_only(self):
        """A requested tune name is used verbatim as the slot."""
        assert compute_tune_slot("hmax", []) == "hmax"
        assert compute_tune_slot("soup", None) == "soup"

    def test_tune_list_joined_with_plus(self):
        """A list of tune names joins with '+' (commas read awkwardly in names)."""
        assert compute_tune_slot(["a", "b"], []) == "a+b"

    def test_tune_comma_string_normalised(self):
        """A comma-separated --tune string becomes '+'-joined too."""
        assert compute_tune_slot("a,b", []) == "a+b"

    def test_options_append_shorthash(self):
        """Raw options add a git-style 8-hex suffix, keyed on their string."""
        slot = compute_tune_slot(None, ["-O2"])
        assert slot.startswith("defopt+")
        shorthash = slot.split("+", 1)[1]
        assert len(shorthash) == 8
        assert all(c in "0123456789abcdef" for c in shorthash)

    def test_options_shorthash_is_deterministic(self):
        """Same options always hash to the same slot (comparable filenames)."""
        assert compute_tune_slot("hmax", ["-O2"]) == compute_tune_slot("hmax", ["-O2"])

    def test_options_shorthash_is_order_sensitive(self):
        """Different option orderings are distinct configurations."""
        a = compute_tune_slot(None, ["-O2", "-active-cse"])
        b = compute_tune_slot(None, ["-active-cse", "-O2"])
        assert a != b

    def test_tune_and_options_combined(self):
        """Both dials present: '<tune>+<shorthash>'."""
        slot = compute_tune_slot("hmax", ["-O2"])
        assert slot.startswith("hmax+")
        assert len(slot.split("+", 1)[1]) == 8


class TestComputeOptionsLabel:
    """Test the readable config label recorded in the summary (plot grouping)."""

    def test_defopt_when_nothing_requested(self):
        assert compute_options_label(None, None) == "defopt"
        assert compute_options_label("", []) == "defopt"

    def test_options_only_keeps_raw_text(self):
        """Options-only labels are unchanged from the pre-refactor spelling."""
        assert compute_options_label(None, ["-O2"]) == "O2"
        assert compute_options_label(None, ["-active-cse"]) == "active-cse"
        assert compute_options_label(None, ["-O2", "-active-cse"]) == "O2+active-cse"

    def test_tune_only(self):
        assert compute_options_label("hmax", []) == "hmax"

    def test_tune_and_options(self):
        assert compute_options_label("hmax", ["-O2"]) == "hmax+O2"

    def test_readable_not_hashed(self):
        """Unlike the filename slot, the label keeps the option text verbatim."""
        assert "+" in compute_tune_slot("hmax", ["-O2"])  # hashed
        assert compute_options_label("hmax", ["-O2"]) == "hmax+O2"  # readable


class TestFilenameTuneSlot:
    """Test that the namers insert the tune slot between solver and model."""

    def test_solution_filename_with_slot(self):
        name = SolutionFileNamer.generate_solution_filename(
            "sample-instance", "kissat", "demo", "20250720133846",
            tune="defopt"
        )
        assert name == "sample-instance-kissat-defopt-demo-20250720133846.solution"

    def test_solution_filename_without_slot_is_legacy(self):
        """An empty slot reproduces the pre-slot filename (backward compat)."""
        name = SolutionFileNamer.generate_solution_filename(
            "inst", "fd", "pddl", "ts"
        )
        assert name == "inst-fd-pddl-ts.solution"

    def test_solution_filename_slot_with_horizon_and_suffix(self):
        name = SolutionFileNamer.generate_solution_filename(
            "inst", "kissat", "scored", "ts",
            tune="defopt+a1b2c3d4", horizon=7, suffix=".best.solution"
        )
        assert name == "inst-kissat-defopt+a1b2c3d4-scored-ts-07.best.solution"

    def test_objective_path_with_slot(self):
        path = ObjectiveFileManager.generate_objective_path(
            "inst", "kissat", "scored", "ts", 3, tune="hmax"
        )
        assert path.name == "inst-kissat-hmax-scored-ts-03.obj"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])