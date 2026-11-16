#!/usr/bin/env python3
"""Pre-commit secret hygiene check.

Blocks committing:
1. Any .env file
2. Any file containing a 64-hex-character private key (e.g., standard EVM private key)
"""
import re
import subprocess
import sys

HEX64_PATTERN = re.compile(r'(?:0x)?[0-9a-fA-F]{64}')

def get_staged_files():
    try:
        res = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
            capture_output=True,
            text=True,
            check=True
        )
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]
    except subprocess.CalledProcessError:
        return []

def check_staged_file_for_secrets(filepath):
    if filepath == '.env' or filepath.endswith('/.env') or filepath.endswith('\\.env'):
        return f"CRITICAL: Attempted to commit .env file: {filepath}"

    # Check the staged content diff for this file
    try:
        res = subprocess.run(
            ['git', 'diff', '--cached', '-U0', filepath],
            capture_output=True,
            text=True,
            check=True
        )
        added_lines = [
            line[1:] for line in res.stdout.splitlines()
            if line.startswith('+') and not line.startswith('+++')
        ]
        for line in added_lines:
            match = HEX64_PATTERN.search(line)
            if match:
                # Mask secret in output to obey secret hygiene rule
                matched_val = match.group(0)
                masked = matched_val[:4] + '...' + matched_val[-4:]
                return (
                    f"CRITICAL: 64-hex character secret detected in staged file "
                    f"'{filepath}': {masked}"
                )
    except subprocess.CalledProcessError as e:
        return f"Error checking diff for {filepath}: {e}"

    return None

def main():
    staged_files = get_staged_files()
    errors = []
    for filepath in staged_files:
        err = check_staged_file_for_secrets(filepath)
        if err:
            errors.append(err)

    if errors:
        print("=" * 60, file=sys.stderr)
        print("PRE-COMMIT HOOK FAILED: Secret hygiene violation", file=sys.stderr)
        for err in errors:
            print(f" - {err}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)

    print("Pre-commit secret hygiene check passed.")
    sys.exit(0)

if __name__ == '__main__':
    main()
