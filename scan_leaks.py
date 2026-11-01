#!/usr/bin/env python3
"""
scan_leaks.py - Automated Privacy, Sanitization & Secret Leak Scanner.

Recursively scans all files in the repository directory to verify that:
1. No personal usernames or local filesystem paths exist (e.g., specific user home paths).
2. No live Telegram bot tokens, chat IDs, or cloud API keys exist.
3. No active .env files, cache directories (__pycache__, .pytest_cache), or logs exist.
4. No Git repository history (.git) exists (R4 compliance).
5. No open-source license files exist (R3 compliance).
6. No unvetted internal scratch scripts or legacy design documents exist.

Exits with code 0 if ZERO leaks are found; exits with code 1 if any leak is detected.
"""

import sys
import os
import re
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional

# Default target directory is the folder where this script resides
DEFAULT_TARGET_DIR = Path(__file__).resolve().parent

# Forbidden filenames or directory names
FORBIDDEN_PATHS = [
    ".git",
    ".agents",
    ".gemini",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
    ".env",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "COPYING",
    "COPYING.md",
    "build_v31_portfolio_state.py",
    "ARCHITECTURE_AUDIT_AND_UPGRADE_PLAN.md",
    "empirical_stress_test.py",
    "verify_remediation_empirics.py",
]

# Forbidden file extensions
FORBIDDEN_EXTENSIONS = [".log", ".pyc", ".pyo"]

# Generic regex patterns identifying private data, credentials, and unwanted markers
FORBIDDEN_REGEXES = [
    # Live Telegram Bot Token: 8-10 digits followed by colon and 35 alphanumeric/underscore/dash chars
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "Live Telegram Bot Token Pattern"),
    # Windows User home directory pattern (permits generic placeholders like <username>, username, example)
    (re.compile(r"[A-Za-z]:[\\/]Users[\\/](?!(?:Public|Default|All Users|username|<username>|your_username|example)\b)[A-Za-z0-9_\-\.]+", re.IGNORECASE), "Personal Windows User Path"),
    # Unix User home directory pattern (permits standard runners and generic placeholders)
    (re.compile(r"/(?:home|Users)/(?!(?:runner|ubuntu|github|username|<username>|your_username|example)\b)[A-Za-z0-9_\-\.]+", re.IGNORECASE), "Personal Unix User Path"),
    # Active hardcoded secret token assignment in code or configuration
    (re.compile(r"(?i)\b(?:bot_token|api_token|user_key|chat_id)\s*[:=]\s*['\"][A-Za-z0-9_-]{6,}['\"]"), "Hardcoded Active Secret Key Assignment"),
    # Cloud API Keys (OpenAI, Gemini / Google AI Studio)
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z-_]{35})\b"), "Live Cloud API Key Pattern"),
    # Internal subagent identity tags
    (re.compile(r"\b(?:" + r"challenger|auditor|reviewer|explorer|worker" + r")_[a-zA-Z0-9_]+_\d+\b"), "Subagent Identity Tag"),
]


def is_binary_file(filepath: Path) -> bool:
    """Detects whether a file is binary."""
    try:
        with open(filepath, "tr", encoding="utf-8") as f:
            f.read(1024)
            return False
    except (UnicodeDecodeError, PermissionError):
        return True


def scan_repository(target_dir: Path, custom_forbidden: Optional[List[str]] = None) -> int:
    forbidden_literals = [lit.strip() for lit in (custom_forbidden or []) if lit.strip()]

    print("=" * 80)
    print("TRADING SENTINEL — AUTOMATED PRIVACY & LEAK AUDIT SCANNER")
    print(f"Target Directory: {target_dir}")
    if forbidden_literals:
        print(f"Active Custom Forbidden Literals: {len(forbidden_literals)} supplied via CLI/ENV")
    print("=" * 80)

    total_files_scanned = 0
    total_bytes_scanned = 0
    leaks: List[Dict[str, Any]] = []

    # 1. Structural Scan (File and Directory Names)
    for root, dirs, files in os.walk(target_dir):
        current_rel = Path(root).relative_to(target_dir)

        # Check directory names
        for d in dirs:
            rel_dir_path = (current_rel / d).as_posix()
            for forbidden in FORBIDDEN_PATHS:
                if d.lower() == forbidden.lower() or rel_dir_path.lower() == forbidden.lower():
                    leaks.append({
                        "type": "FORBIDDEN_DIRECTORY",
                        "path": rel_dir_path,
                        "line": None,
                        "detail": f"Forbidden directory '{d}' detected (violation of export rules)."
                    })

        # Check filenames
        for f in files:
            rel_file_path = (current_rel / f).as_posix()
            total_files_scanned += 1
            full_path = Path(root) / f
            try:
                total_bytes_scanned += full_path.stat().st_size
            except Exception:
                pass

            # Check if filename is forbidden
            for forbidden in FORBIDDEN_PATHS:
                if f.lower() == forbidden.lower():
                    # Allow .gitignore and .env.example
                    if f.lower() in [".env.example", ".gitignore"]:
                        continue
                    leaks.append({
                        "type": "FORBIDDEN_FILE",
                        "path": rel_file_path,
                        "line": None,
                        "detail": f"Forbidden file '{f}' detected."
                    })

            # Check if file has forbidden extensions
            for ext in FORBIDDEN_EXTENSIONS:
                if f.lower().endswith(ext):
                    leaks.append({
                        "type": "FORBIDDEN_EXTENSION",
                        "path": rel_file_path,
                        "line": None,
                        "detail": f"Forbidden file extension in '{f}'."
                    })

            # Binary files are checked structurally; skip line-by-line text inspection
            if is_binary_file(full_path):
                continue

            # 2. Content Scan (All files inspected uniformly; NO self-skip)
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line_num, line in enumerate(fh, start=1):
                        # Literal checks (from CLI / ENV)
                        for lit in forbidden_literals:
                            if lit.lower() in line.lower():
                                leaks.append({
                                    "type": "SENSITIVE_LITERAL",
                                    "path": rel_file_path,
                                    "line": line_num,
                                    "detail": f"Found forbidden literal: {line.strip()[:100]}"
                                })

                        # Generic Regex checks
                        for reg, desc in FORBIDDEN_REGEXES:
                            match = reg.search(line)
                            if match:
                                leaks.append({
                                    "type": "SENSITIVE_PATTERN",
                                    "path": rel_file_path,
                                    "line": line_num,
                                    "detail": f"{desc} match ('{match.group(0)}'): {line.strip()[:100]}"
                                })
            except Exception as e:
                print(f"Warning: Could not read file {rel_file_path}: {e}", file=sys.stderr)

    # Summary Output
    print(f"\nFiles Audited:       {total_files_scanned}")
    print(f"Bytes Inspected:     {total_bytes_scanned:,} bytes")
    print(f"Total Leaks Found:   {len(leaks)}")
    print("-" * 80)

    if leaks:
        print("[FAILED] AUDIT FAILED -- LEAKS DETECTED:")
        for idx, leak in enumerate(leaks, start=1):
            line_info = f":{leak['line']}" if leak['line'] else ""
            print(f"  [{idx}] [{leak['type']}] {leak['path']}{line_info} -> {leak['detail']}")
        print("\nPlease remediate all detected leaks before public release.")
        return 1

    print("[PASSED] AUDIT PASSED -- ZERO LEAKS DETECTED!")
    print("Repository is completely sanitized and ready for public GitHub release.")
    print("=" * 80)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous Trading Sentinel Privacy & Leak Scanner")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET_DIR, help="Target directory to scan")
    parser.add_argument("--forbidden", nargs="*", default=[], help="Additional forbidden literals to check")
    parser.add_argument("--forbidden-env", type=str, default="FORBIDDEN_LITERALS", help="Environment variable containing comma-separated forbidden strings")
    args = parser.parse_args()

    forbidden_list = list(args.forbidden)
    env_val = os.environ.get(args.forbidden_env, "")
    if env_val:
        forbidden_list.extend([x.strip() for x in env_val.split(",") if x.strip()])

    return scan_repository(args.target, forbidden_list)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
