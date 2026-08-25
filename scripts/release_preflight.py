#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


FORBIDDEN_TRACKED_FILES = {
    ".env",
    "puzzle_ops.db",
    "feishu_mock",
}

FORBIDDEN_TRACKED_PREFIXES = (
    "trial_uploads/",
    "runtime/",
    "backups/",
    "knowledge/indices/",
    ".runtime_probe_images/",
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai-style sk-", re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_\-]{20,}")),
    ("QWEN_API_KEY", re.compile(r"QWEN_API_KEY[ \t]*=[ \t]*(?!your_)(?!replace_me)[A-Za-z0-9_\-]{24,}[ \t]*(?:$|\n)", re.MULTILINE)),
    ("DASHSCOPE_API_KEY", re.compile(r"DASHSCOPE_API_KEY[ \t]*=[ \t]*(?!your_)(?!replace_me)[A-Za-z0-9_\-]{24,}[ \t]*(?:$|\n)", re.MULTILINE)),
    ("FEISHU_APP_SECRET", re.compile(r"FEISHU_APP_SECRET[ \t]*=[ \t]*(?!your_)(?!replace_me)[A-Za-z0-9_\-]{24,}[ \t]*(?:$|\n)", re.MULTILINE)),
    ("FEISHU_ACCESS_TOKEN", re.compile(r"FEISHU_ACCESS_TOKEN[ \t]*=[ \t]*(?!your_)(?!replace_me)[A-Za-z0-9_\-]{24,}[ \t]*(?:$|\n)", re.MULTILINE)),
    ("PUZZLEOPS_API_TOKENS", re.compile(r"PUZZLEOPS_API_TOKENS[ \t]*=[ \t]*(?!.*replace_me)(?!.*token_jp)(?!.*jp_token)(?!.*jp-token)(?!.*fr-token)(?!.*admin-token)(?!.*token1)(?!.*token2)(?!.*token3)(?!.*:token:)[^\n]{36,}")),
)

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".csv",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
    ".example",
    ".svg",
}

PUBLIC_PATH_SCAN_PREFIXES = ("docs/assets/",)
PUBLIC_PATH_SCAN_FILES = {"README.md", "SECURITY.md"}
ABSOLUTE_LOCAL_PATH_PATTERN = re.compile(r"(?:/Users/[^/\s]+/|/home/[^/\s]+/|[A-Za-z]:\\\\Users\\\\[^\\\s]+\\\\)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PuzzleOps release safety preflight.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--tracked-file", action="append", default=[], help="Extra tracked file for tests.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    tracked = tuple(dict.fromkeys((*_git_tracked_files(root), *args.tracked_file)))
    failures: list[str] = []

    for name in tracked:
        normalized = name.strip().replace("\\", "/")
        if not normalized:
            continue
        if normalized in FORBIDDEN_TRACKED_FILES or any(normalized.startswith(prefix) for prefix in FORBIDDEN_TRACKED_PREFIXES):
            failures.append(f"tracked forbidden file: {normalized}")

    for relative in tracked:
        path = root / relative
        if not _should_scan(path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                failures.append(f"secret-like pattern [{label}] in {relative}")
        normalized = relative.strip().replace("\\", "/")
        if (
            normalized in PUBLIC_PATH_SCAN_FILES
            or any(normalized.startswith(prefix) for prefix in PUBLIC_PATH_SCAN_PREFIXES)
        ) and ABSOLUTE_LOCAL_PATH_PATTERN.search(content):
            failures.append(f"absolute local path in {relative}")

    if failures:
        print("PuzzleOps release preflight failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PuzzleOps release preflight passed.")
    print(f"checked_files={len(tracked)}")
    return 0


def _git_tracked_files(root: Path) -> tuple[str, ...]:
    # Production path uses `git ls-files` so the preflight checks exactly what would be published.
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return _walk_files(root)
    tracked = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    return tracked or _walk_files(root)


def _walk_files(root: Path) -> tuple[str, ...]:
    files: list[str] = []
    for path in root.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        files.append(path.relative_to(root).as_posix())
    return tuple(sorted(files))


def _should_scan(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name in {".env.example", ".gitignore"}:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


if __name__ == "__main__":
    raise SystemExit(main())
