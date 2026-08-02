"""
Smoke tests for response consistency.
Verifies that all route endpoints use success_response/error_response helpers
instead of raw jsonify() with 'success'/'error' fields.
"""

import ast
import os
import re
import unittest


def get_route_files():
    """Get all route files in api/routes/."""
    route_dir = os.path.join(os.path.dirname(__file__), "..", "api", "routes")
    route_dir = os.path.abspath(route_dir)
    route_files = []
    for f in os.listdir(route_dir):
        if f.endswith(".py"):
            route_files.append(os.path.join(route_dir, f))
    return route_files


def check_response_consistency(filepath):
    """
    Check that the file uses success_response/error_response helpers.
    Returns list of issues found.
    """
    issues = []

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Skip if not a route file (no Blueprint)
    if "Blueprint" not in content:
        return issues

    # Parse the AST
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        issues.append(f"Syntax error: {e}")
        return issues

    # Check if error_response/success_response are imported
    has_error_response = "error_response" in content
    has_success_response = "success_response" in content

    if not has_error_response and not has_success_response:
        # Check if it uses jsonify with error/success patterns
        if "jsonify({'error'" in content or "jsonify({'success'" in content:
            issues.append("Uses raw jsonify() with 'error'/'success' fields instead of helpers")

    # Check for raw jsonify() calls that might be non-standard
    # (skip jsonify() calls that are clearly the standard helpers)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if this is a jsonify call
            if isinstance(node.func, ast.Name) and node.func.id == "jsonify":
                # Get the line number
                line_no = node.lineno
                line = content.split("\n")[line_no - 1]

                # Check if this is a standard success/error response
                if "'success'" in line or "'error'" in line:
                    # This might be a non-standard response
                    # (Standard ones should use success_response/error_response)
                    if not has_error_response and not has_success_response:
                        issues.append(f"Line {line_no}: Possible non-standard jsonify() with success/error")

    return issues


class TestResponseConsistency(unittest.TestCase):
    """Test response consistency across all route files."""

    def test_all_routes_use_standard_responses(self):
        """Verify all route files use success_response/error_response helpers."""
        route_files = get_route_files()
        all_issues = {}

        for filepath in route_files:
            issues = check_response_consistency(filepath)
            if issues:
                all_issues[os.path.basename(filepath)] = issues

        if all_issues:
            error_msg = "Response consistency issues found:\n"
            for filename, issues in all_issues.items():
                error_msg += f"\n{filename}:\n"
                for issue in issues:
                    error_msg += f"  - {issue}\n"
            self.fail(error_msg)

    def test_no_hardcoded_error_responses(self):
        """Check no hardcoded error responses without using helpers."""
        route_files = get_route_files()

        for filepath in route_files:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Skip non-route files
            if "Blueprint" not in content:
                continue

            # Look for patterns like: return jsonify({'error': ...}), 500
            pattern = r"return\s+jsonify\(\s*\{\s*'error'"
            matches = re.findall(pattern, content)

            if matches and "error_response" not in content:
                self.fail(f"{os.path.basename(filepath)} has hardcoded error responses")


if __name__ == "__main__":
    unittest.main()
