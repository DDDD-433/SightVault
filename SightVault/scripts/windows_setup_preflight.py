"""Check Windows x64/Python 3.12, pinned direct dependencies, and SQLite FTS5."""
import argparse
import importlib.metadata
import json
import platform
from pathlib import Path
import re
import sqlite3
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from handoff.download_pinned_models import current_platform_id, incompatible_platform_groups


def pinned_packages() -> dict[str, str]:
    result = {}
    for line in (ROOT / 'requirements-windows.lock.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith(('#', ' ', '\\')):
            continue
        match = re.match(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def validate_optional_model_groups(sources: dict, requested: list[str], platform_id: str) -> list[str]:
    errors = []
    selected = set()
    for name in requested:
        source = sources.get(name)
        if source is None:
            errors.append(f'Unknown optional model group: {name}')
            continue
        if not (source.get('optional', False) or source.get('required') is False):
            errors.append(f'Model group is not optional: {name}')
            continue
        selected.add(name)
    errors.extend(incompatible_platform_groups(sources, selected, platform_id))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--additional-model', action='append', default=[],
                        help='Optional manifest group explicitly selected for setup; repeat as needed')
    args = parser.parse_args()

    manifest_path = ROOT / 'handoff' / 'MODEL_MANIFEST.json'
    model_failures = []
    model_data = {}
    try:
        model_data = json.loads(manifest_path.read_text(encoding='utf-8'))
        sources = model_data.get('sources', {})
        platform_name = current_platform_id()
        required_groups = {name for name, source in sources.items()
                           if not (source.get('optional', False) or source.get('required') is False)}
        model_failures.extend(incompatible_platform_groups(sources, required_groups, platform_name))
        model_failures.extend(validate_optional_model_groups(sources, args.additional_model, platform_name))
        supported_optional_groups = sorted(
            name for name, source in sources.items()
            if (source.get('optional', False) or source.get('required') is False)
            and not incompatible_platform_groups(sources, {name}, platform_name)
        )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        sources = {}
        required_groups = set()
        supported_optional_groups = []
        model_failures.append(f'Could not validate model platform policy: {exc}')

    report = {
        'os': platform.system(),
        'machine': platform.machine(),
        'platform_id': current_platform_id(),
        'python': platform.python_version(),
        'python_bits': struct.calcsize('P') * 8,
        'python_executable': sys.executable,
        'packages': {},
        'sqlite_fts5': False,
        'model_platform_policy': {
            'passed': not model_failures,
            'required_groups': sorted(required_groups),
            'additional_optional_groups': args.additional_model,
            'supported_optional_groups': supported_optional_groups,
            'failures': model_failures,
        },
        'passed': False,
        'failures': [],
    }
    if report['os'] != 'Windows':
        report['failures'].append('Expected native Windows')
    if report['machine'].lower() not in ('amd64', 'x86_64') or report['python_bits'] != 64:
        report['failures'].append('Expected a native 64-bit x64 interpreter')
    if sys.version_info[:2] != (3, 12):
        report['failures'].append('Expected Python 3.12')
    report['failures'].extend(model_failures)
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
