"""Regressions found while exercising the actual desktop release path."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sightvault.database import Database
from sightvault.desktop import demo
from sightvault.desktop.paths import resource_root
from test_desktop import _demo_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_macos_bundle_uses_real_resource_directory(monkeypatch, tmp_path):
    contents = tmp_path / 'SightVault.app' / 'Contents'
    frameworks = contents / 'Frameworks'
    frameworks.mkdir(parents=True)
    resources = contents / 'Resources'
    (resources / 'handoff').mkdir(parents=True)
    (resources / 'handoff' / 'MODEL_MANIFEST.json').write_text('{}')
    (frameworks / 'handoff').symlink_to(resources / 'handoff', target_is_directory=True)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(frameworks), raising=False)
    assert resource_root() == resources


def test_windowed_stdio_still_writes_qa_report(tmp_path):
    report = tmp_path / 'report.json'
    env = {key: value for key, value in os.environ.items()
           if key not in ('SIGHTVAULT_DATA', 'SIGHTVAULT_MODELS', 'SIGHTVAULT_SETUP_LOG')}
    env['SIGHTVAULT_HOME'] = str(tmp_path / 'Fresh Profile')
    command = ('import sys; sys.stdout=sys.stderr=None; '
               'from sightvault.desktop.main import main; '
               'raise SystemExit(main(sys.argv[1:]))')
    run = subprocess.run([sys.executable, '-c', command, '--print-paths', '--report', str(report)],
                         env=env, cwd=ROOT, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(report.read_text())['data'] == str(tmp_path / 'Fresh Profile' / 'data')


def test_windowed_setup_failure_has_actionable_log(tmp_path):
    log = tmp_path / 'worker.log'
    env = dict(os.environ, SIGHTVAULT_SETUP_LOG=str(log))
    command = ('import sys; sys.stdout=sys.stderr=None; '
               'from sightvault.desktop.main import main; '
               'raise SystemExit(main(sys.argv[1:]))')
    run = subprocess.run([sys.executable, '-c', command, '--setup-worker', 'verify',
                          '--model-root', str(tmp_path / 'missing-models')],
                         env=env, cwd=ROOT, capture_output=True, timeout=30)
    assert run.returncode == 1
    text = log.read_text()
    assert 'missing' in text and 'embeddinggemma-2' in text and '"passed": false' in text


def test_demo_refuses_symlinked_destination_before_copy(tmp_path):
    resources = _demo_bundle(tmp_path)
    data = tmp_path / 'data'
    library = Database(data / 'sightvault.sqlite3')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (data / 'demo-samples').symlink_to(outside, target_is_directory=True)
    with pytest.raises(demo.DemoImportError, match='symlink'):
        demo.import_demo(resources, data, library.path, expected_count=2)
    assert not list(outside.iterdir()) and library.stats()['total'] == 0


def test_demo_does_not_touch_existing_partial_symlink(tmp_path):
    resources = _demo_bundle(tmp_path)
    data = tmp_path / 'data'
    library = Database(data / 'sightvault.sqlite3')
    folder = data / 'demo-samples'
    folder.mkdir()
    unrelated = tmp_path / 'unrelated.txt'
    unrelated.write_text('keep this')
    (folder / 'sample_0.png.partial').symlink_to(unrelated)
    assert demo.import_demo(resources, data, library.path, expected_count=2)['imported'] == 2
    assert unrelated.read_text() == 'keep this'
