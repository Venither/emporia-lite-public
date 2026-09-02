"""SAM's builder needs requirements.txt inside src/ (see the fix in
a2515fd), while local dev tooling (requirements-dev.txt) depends on the
root-level requirements.txt. This guards against the two drifting apart."""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_src_requirements_matches_root_requirements():
    with open(os.path.join(ROOT, "requirements.txt")) as f:
        root = f.read()
    with open(os.path.join(ROOT, "src", "requirements.txt")) as f:
        src = f.read()
    assert root == src
