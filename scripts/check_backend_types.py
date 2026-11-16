"""Run the explicit strict scope and verify deliberately invalid contracts fail."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
NEGATIVE = Path('type_checks/invalid_usage.py')
EXPECTED = re.compile(r'# expect-error: ([\w-]+)')
DIAGNOSTIC = re.compile(r'^.+?:(\d+): error: .*\[([\w-]+)\]$')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-executable', help='Python environment supplying project dependencies to mypy')
    args = parser.parse_args()
    checker = [sys.executable, '-m', 'mypy', '--strict']
    if args.python_executable:
        checker.extend(['--python-executable', args.python_executable])
    positive = subprocess.run(checker, cwd=BACKEND, check=False)
    if positive.returncode:
        return positive.returncode
    expected = {(number, match.group(1))
        for number, line in enumerate((BACKEND / NEGATIVE).read_text(encoding='utf-8').splitlines(), 1)
        if (match := EXPECTED.search(line))}
    if not expected:
        print('Missing negative type contract cases', file=sys.stderr)
        return 1
    negative = subprocess.run([*checker, str(NEGATIVE)], cwd=BACKEND, check=False,
        capture_output=True, text=True, encoding='utf-8')
    actual = {(int(match.group(1)), match.group(2))
        for line in negative.stdout.splitlines() if (match := DIAGNOSTIC.match(line))}
    if negative.returncode != 1 or actual != expected:
        print('Type contract rejection check failed', file=sys.stderr)
        print(negative.stdout, file=sys.stderr)
        print(negative.stderr, file=sys.stderr)
        print(f'Missing: {sorted(expected - actual)}; unexpected: {sorted(actual - expected)}', file=sys.stderr)
        return 1
    print(f'Type contract rejection checks passed: {len(expected)} invalid calls rejected')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
