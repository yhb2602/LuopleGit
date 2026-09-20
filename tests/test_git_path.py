import unittest
from unittest.mock import patch

from luple.core import LupleError, git_executable


class GitPathTests(unittest.TestCase):
    def test_path_git_is_used(self):
        with patch.dict("os.environ", {"LUPLE_GIT": ""}), patch("shutil.which", return_value="installed-git"):
            self.assertEqual(git_executable(), "installed-git")

    def test_known_location_fallback(self):
        with patch.dict("os.environ", {"LUPLE_GIT": ""}), patch("shutil.which", return_value=None), patch("pathlib.Path.is_file", return_value=True):
            self.assertTrue(git_executable().endswith("git.exe"))

    def test_missing_git_has_actionable_error(self):
        with patch.dict("os.environ", {"LUPLE_GIT": ""}), patch("shutil.which", return_value=None), patch("pathlib.Path.is_file", return_value=False):
            with self.assertRaisesRegex(LupleError, "Git executable not found"):
                git_executable()

    def test_invalid_override_is_not_silently_ignored(self):
        with patch.dict("os.environ", {"LUPLE_GIT": "missing-git"}), patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(LupleError, "LUPLE_GIT"):
                git_executable()
