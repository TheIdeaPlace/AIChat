"""conftest's hang report (#757): on CI, a test that runs too long ends the
run with every thread's stack, rather than running out the job's time. Run
for real, in a pytest of its own, so the capture and the exit are the ones a
real hang meets. Windowless."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_a_slow_test_when_asked():
    """Only the report's own pytest runs this: it stands in for a hang."""
    if os.environ.get("TCP_HANG_PROBE") != "1":
        pytest.skip("run by test_a_hung_test_is_reported_and_ends_the_run")
    time.sleep(30)


@pytest.mark.parametrize("verbosity", ["-q", "-v"])
def test_a_hung_test_is_reported_and_ends_the_run(verbosity):
    node = "tests/test_hang_report.py::test_a_slow_test_when_asked"
    env = dict(os.environ, CI="1", TCP_HANG_SECONDS="1", TCP_HANG_PROBE="1")
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "pytest", verbosity, "-p", "no:cacheprovider",
                             node], cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=120)
    assert time.monotonic() - started < 25, "the run wasn't ended at the hang"
    assert result.returncode == 3, result.stdout + result.stderr
    assert f"#757: {node} has run for 1 seconds" in result.stderr
    assert "test_a_slow_test_when_asked" in result.stderr.split("#757:", 1)[1]  # its stack
