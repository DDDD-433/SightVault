"""Download manifest-pinned models explicitly, then verify their exact file hashes."""
import argparse
import json
import os
import platform
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def current_platform_id(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    if system in {'darwin', 'macos'} and machine in {'arm64', 'aarch64'}:
        return 'macos-arm64'
    if system == 'windows' and machine in {'amd64', 'x86_64'}:
        return 'windows-x64'
    return f'{system}-{machine}'


def incompatible_platform_groups(sources: dict, selected: set[str], platform_id: str) -> list[str]:
    errors = []
    for name in sorted(selected):
        source = sources[name]
        supported = source.get('platforms')
        if supported and platform_id not in supported:
            errors.append(f'{name} supports {", ".join(supported)}, not {platform_id}')
    return errors

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', action='append', help='Download one named manifest group (repeat to select several)')
    parser.add_argument('--with-model', action='append', help='Add one explicitly selected manifest group to the required set')
    parser.add_argument('--model-root', type=Path, default=ROOT / '.models',
                        help='Folder that receives the model groups (default: .models in this checkout)')
    args = parser.parse_args(argv)
    model_root = args.model_root.expanduser()
    if not model_root.is_absolute():
        parser.error('--model-root must be an absolute path')
    os.environ['HF_HUB_OFFLINE'] = '0'
    os.environ['TRANSFORMERS_OFFLINE'] = '0'
    from verify_transfer import sha256, safe_path
    manifest = json.loads((ROOT / 'handoff' / 'MODEL_MANIFEST.json').read_text(encoding='utf8'))
    sources = manifest.get('sources', {})
    if args.only and args.with_model:
        parser.error('Use --only or --with-model, not both')
    selected = set(args.only or [name for name, source in sources.items()
                                 if not (source.get('optional', False) or source.get('required') is False)])
    if not args.only:
        selected.update(args.with_model or [])
    unknown = sorted(selected - set(sources))
    if unknown:
        parser.error('Unknown model name(s): ' + ', '.join(unknown))
    if not selected:
        parser.error('No model groups were selected for download')
    unsupported = incompatible_platform_groups(sources, selected, current_platform_id())
    if unsupported:
        parser.error('Unsupported model group for this platform: ' + '; '.join(unsupported))
    from huggingface_hub import snapshot_download

    for name, source in manifest['sources'].items():
        if name not in selected:
            continue
        items = [m for m in manifest['files'] if m['path'].startswith('.models/' + name + '/')]
        prefix = '.models/' + name + '/'
        patterns = [m['path'][len(prefix):] for m in items if not m['path'].endswith('/source.json')]
        target = safe_path(model_root, name)
        print(f"Downloading {source['repo']} at {source['revision']}", flush=True)
        snapshot_download(repo_id=source['repo'], revision=source['revision'],
                          local_dir=target, allow_patterns=patterns)
        # Preserve the exact local provenance bytes used by the checksum manifest.
        (target / 'source.json').write_bytes(manifest['source_json_bytes'][name].encode('utf8'))
        for m in items:
            path = safe_path(model_root, Path(m['path']).relative_to('.models'))
            if path.stat().st_size != m['size'] or sha256(path) != m['sha256']:
                raise ValueError('Downloaded file differs from Windows baseline: ' + m['path'])
        print(name + ': verified', flush=True)
    print('Requested model files are verified. App inference uses local files only.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
