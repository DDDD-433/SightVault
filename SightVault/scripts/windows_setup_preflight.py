"""Check Windows x64/Python 3.12, pinned direct dependencies, and SQLite FTS5."""
import importlib.metadata
import json
import platform
from pathlib import Path
import re
import sqlite3
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]


def pinned_packages() -> dict[str, str]:
    result = {}
    for line in (ROOT / 'requirements-windows.lock.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith(('#', ' ', '\\')):
            continue
        match = re.match(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def main() -> int:
    report = {
        'os': platform.system(),
        'machine': platform.machine(),
        'python': platform.python_version(),
        'python_bits': struct.calcsize('P') * 8,
        'python_executable': sys.executable,
        'packages': {},
        'sqlite_fts5': False,
        'passed': False,
        'failures': [],
    }
    if report['os'] != 'Windows':
        report['failures'].append('Expected native Windows')
    if report['machine'].lower() not in ('amd64', 'x86_64') or report['python_bits'] != 64:
        report['failures'].append('Expected a native 64-bit x64 interpreter')
    if sys.version_info[:2] != (3, 12):
        report['failures'].append('Expected Python 3.12')
    for distribution, expected_version in pinned_packages().items():
        try:
            installed = importlib.metadata.version(distribution)
            report['packages'][distribution] = installed
            if installed != expected_version:
                report['failures'].append(f'{distribution}: expected {expected_version}, found {installed}')
        except importlib.metadata.PackageNotFoundError:
            report['failures'].append(f'Missing installed package: {distribution}=={expected_version}')
    try:
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIRTUAL TABLE fts_check USING fts5(text)')
            report['sqlite_fts5'] = True
    except sqlite3.Error as exc:
        report['failures'].append(f'SQLite FTS5 unavailable: {exc}')
    report['passed'] = not report['failures']
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
