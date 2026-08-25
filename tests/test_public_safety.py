from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_tree_contains_no_blocked_personal_or_secret_patterns():
    spec = importlib.util.spec_from_file_location("security_scan_test", ROOT / "scripts" / "security_scan.py")
    scanner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(scanner)
    assert "LICENSE" in scanner.TEXT_FILENAMES
    assert ROOT / "LICENSE" in scanner.public_files()
    assert scanner.scan() == []
