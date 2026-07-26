"""Test helper utilities shared by the solver_support core tests."""

import shutil
import os
import tempfile
from pathlib import Path


class TempDirTestMixin:
    """
    Mixin class for tests that need temporary working directory functionality.

    Use this mixin when you prefer the setup_method/teardown_method pattern
    over pytest fixtures.
    """

    def setup_temp_working_dir(self):
        """Set up temporary working directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.original_cwd = Path.cwd()
        os.chdir(self.temp_dir)

    def teardown_temp_working_dir(self):
        """Clean up temporary working directory."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)
