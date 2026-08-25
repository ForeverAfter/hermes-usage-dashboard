"""Fail closed when public files contain common secret or personal-data patterns."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".js", ".json", ".yaml", ".yml", ".md", ".txt", ".toml"}
TEXT_FILENAMES = {"LICENSE"}
BLOCKED = {
    "personal Windows path": re.compile(r"C:[\\/]Users[\\/]", re.IGNORECASE),
    "personal macOS path": re.compile("/Us" + r"ers/[^/\s]+/"),
    "personal Linux path": re.compile("/ho" + r"me/[^/\s]+/"),
    "email address": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "GitHub token": re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "literal bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
}
SKIP_PARTS = {".git", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}


def public_files() -> list[Path]:
    """Return Git-tracked files, with a safe tree-walk fallback for source archives."""
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        tracked = [item for item in result.stdout.split(b"\0") if item]
        if tracked:
            return [ROOT / item.decode("utf-8", errors="strict") for item in tracked]
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in SKIP_PARTS for part in path.parts)
    ]


def scan() -> list[str]:
    findings: list[str] = []
    for path in public_files():
        if not path.is_file() or (
            path.suffix.lower() not in TEXT_SUFFIXES
            and path.name not in TEXT_FILENAMES
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in BLOCKED.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(ROOT)}:{line}: {label}")
    return findings


if __name__ == "__main__":
    issues = scan()
    if issues:
        print("Public safety scan failed:")
        print("\n".join(f"- {item}" for item in issues))
        sys.exit(1)
    print("Public safety scan passed: no blocked secrets, personal paths, or email addresses found.")
