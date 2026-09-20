import unittest
import os
from unittest.mock import patch

from luple.menu import browse, choose, text, render_options


class MenuTests(unittest.TestCase):
    def test_sections_are_not_selectable_and_numbers_stay_stable(self):
        output = render_options(["● 이전으로", "★ 바로가기", "■ 저장 ◆ ★", "닫기"],
                                sections={0: "이전으로", 1: "바로가기", 2: "저장 목록 (1/1 페이지)"}, numbered=True)
        self.assertIn("1. ● 이전으로", output)
        self.assertIn("3. ■ 저장 ◆ ★", output)
        self.assertIn("4. 닫기", output)
        self.assertEqual(output.count("─" * 56), 4)

    @unittest.skipUnless(os.name == "nt", "Windows console keys")
    def test_windows_arrow_and_escape(self):
        from unittest.mock import MagicMock
        kernel = MagicMock()
        kernel.GetConsoleMode.return_value = 1
        kernel.SetConsoleMode.return_value = 1
        with patch("ctypes.windll.kernel32", kernel), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch("builtins.print"), patch("msvcrt.getwch", side_effect=["\xe0", "P", "\r"]):
            self.assertEqual(choose("title", ["one", "two"]), 1)
        with patch("ctypes.windll.kernel32", kernel), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch("builtins.print"), patch("msvcrt.getwch", return_value="\x1b"):
            self.assertIsNone(choose("title", ["one", "two"]))

    def test_numbered_picker_invalid_input_and_eof(self):
        with patch("sys.stdin.isatty", return_value=False), patch("builtins.input", side_effect=["invalid", "9", "2"]), patch("builtins.print"):
            self.assertEqual(choose("title", ["one", "two"]), 1)
        with patch("sys.stdin.isatty", return_value=False), patch("builtins.input", side_effect=EOFError), patch("builtins.print"):
            self.assertIsNone(choose("title", ["one"]))

    def test_control_characters_are_removed(self):
        self.assertNotIn("\x1b", text("hello\x1b[2J\nworld"))

    def test_pagination_back_and_no_mutation(self):
        class Repo:
            def read(self):
                return {"current": {"line": "S0", "save": "14"},
                        "lines": {"S0": {"head": "14"}},
                        "saves": {str(n): {"parent": str(n - 1) if n else None,
                                           "time": 0, "message": str(n)} for n in range(15)}}
            def ancestry(self, state, line):
                return [str(n) for n in range(15)]
        screens = []
        choices = iter([10, 5, None])
        def picker(title, options, subtitle):
            screens.append(title)
            return next(choices)
        browse(Repo(), picker=picker)
        self.assertIn("1/2", screens[0])
        self.assertIn("2/2", screens[1])
        self.assertIn("1/2", screens[2])
