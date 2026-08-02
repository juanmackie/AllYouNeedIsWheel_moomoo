"""CI test runner.

Runs pytest then force-exits with the real exit code. The moomoo SDK leaves
non-daemon threads behind after tests complete; Python's normal shutdown waits
for them, which hangs Windows CI. os._exit bypasses that join without altering
test behavior.
"""

import os
import sys


def main() -> int:
    import pytest

    code = pytest.main(sys.argv[1:] or ["tests/", "-q"])
    os._exit(code)  # noqa: PLR1722 - intentional: skip non-daemon thread join


if __name__ == "__main__":
    main()
