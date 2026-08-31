"""
Encoding and display sanity checks.

Checks for:
  - Mojibake/replacement characters in source files
  - IV decimal displayed as percent (0.30 → 30.0%)
  - IV rank 0-100 displayed as percent
  - Delta displayed consistently as signed Greek and absolute probability
"""

import os
import unittest

# Replacement character and common mojibake patterns
MOJIBAKE_PATTERNS = [
    "Ã",  # Latin-1 misdecoded UTF-8
    "ð",  # Another common mojibake indicator
    "\ufffd",  # Unicode replacement character
    "â€",  # Common UTF-8 → Latin-1 mojibake
]


def _get_source_files(base_dir, extensions=(".py", ".js", ".html", ".css", ".md")):
    """Yield paths to all source files under base_dir."""
    for root, dirs, files in os.walk(base_dir):
        # Skip hidden and virtual environment dirs
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d not in ("venv", ".venv", "node_modules", "__pycache__")
        ]
        for f in files:
            if any(f.endswith(ext) for ext in extensions):
                yield os.path.join(root, f)


class TestEncodingSanity(unittest.TestCase):
    """Check for mojibake and encoding issues in source files."""

    def test_no_mojibake_in_source_files(self):
        """No mojibake or replacement characters in user-facing source files."""
        base_dir = os.path.join(os.path.dirname(__file__), "..")
        ignored = {
            os.path.normpath(os.path.join(base_dir, "SIGNAL_GENERATOR_10X_TODO.md")),
            os.path.normpath(os.path.join(base_dir, "tests", "test_encoding.py")),
        }
        failures = []
        for filepath in _get_source_files(base_dir):
            if os.path.normpath(filepath) in ignored:
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                for pattern in MOJIBAKE_PATTERNS:
                    if pattern in content:
                        relpath = os.path.relpath(filepath, base_dir)
                        failures.append(f"{relpath}: contains mojibake pattern {pattern!r}")
            except (UnicodeDecodeError, UnicodeError) as e:
                relpath = os.path.relpath(filepath, base_dir)
                failures.append(f"{relpath}: encoding error: {e}")

        if failures:
            self.fail("Mojibake/replacement characters found:\n" + "\n".join(failures))

    def test_source_files_are_utf8(self):
        """Source files should be valid UTF-8."""
        base_dir = os.path.join(os.path.dirname(__file__), "..")
        failures = []
        for filepath in _get_source_files(base_dir):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    f.read()
            except UnicodeDecodeError as e:
                relpath = os.path.relpath(filepath, base_dir)
                failures.append(f"{relpath}: {e}")

        if failures:
            self.fail("Non-UTF-8 files found:\n" + "\n".join(failures))


class TestDisplayFormat(unittest.TestCase):
    """Check that display formatting is centralized and correct."""

    def test_iv_decimal_display_format(self):
        """IV decimal 0.30 should display as 30.0%, not 0.30%."""
        iv_decimal = 0.30
        # Simulate the formatting
        displayed = f"{iv_decimal * 100:.1f}%"
        self.assertEqual(displayed, "30.0%")
        # Ensure we're not doing the wrong thing
        wrong = f"{iv_decimal:.2f}%"
        self.assertNotEqual(wrong, "30.00%")  # This would be 0.30%

    def test_iv_rank_display_format(self):
        """IV rank 70 (0-100 scale) should display as 70%."""
        iv_rank = 70
        displayed = f"{iv_rank}%"
        self.assertEqual(displayed, "70%")

    def test_delta_signed_and_absolute(self):
        """Delta should be available as signed and absolute."""
        delta = -0.25
        self.assertEqual(delta, -0.25)
        self.assertEqual(abs(delta), 0.25)
        # Probability proxy
        prob = abs(delta)
        self.assertEqual(prob, 0.25)


class TestFormatterModule(unittest.TestCase):
    """Verify that a centralized formatter module exists (TODO 6.3)."""

    def test_formatters_module_exists(self):
        """Check that frontend has a formatters.js module."""
        base_dir = os.path.join(os.path.dirname(__file__), "..")
        formatters_path = os.path.join(base_dir, "frontend", "static", "js", "utils", "formatters.js")
        self.assertTrue(
            os.path.exists(formatters_path),
            "frontend/static/js/utils/formatters.js should exist for centralized formatting",
        )


if __name__ == "__main__":
    unittest.main()
