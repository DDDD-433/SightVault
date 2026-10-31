"""Portable regressions found by running the installer pipeline on Windows."""
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
from PIL import Image

from sightvault import ingestion
from sightvault.metadata import iso_timestamp


def test_utc_metadata_dates_support_pre_epoch_seconds():
    assert iso_timestamp(-946771200) == '1940-01-01T00:00:00+00:00'
    assert iso_timestamp(0) == '1970-01-01T00:00:00+00:00'


def test_atomic_save_retries_transient_windows_sharing_denial(tmp_path, monkeypatch):
    target = tmp_path / 'image.png'
    real_replace = ingestion.os.replace
    attempts = []
    def replace(source, destination):
        attempts.append(destination)
        if len(attempts) < 3:
            raise PermissionError(errno.EACCES, 'Temporary sharing denial')
        return real_replace(source, destination)
    monkeypatch.setattr(ingestion, '_WINDOWS', True)
    monkeypatch.setattr(ingestion.os, 'replace', replace)
    monkeypatch.setattr(ingestion.time, 'sleep', lambda _: None)
    ingestion._atomic_save(Image.new('RGB', (8, 8), 'blue'), target, format='PNG')
    with Image.open(target) as result:
        assert result.getpixel((0, 0)) == (0, 0, 255)
    assert len(attempts) == 3 and not list(tmp_path.glob('*.partial'))


def test_atomic_save_permanent_denial_preserves_existing_evidence(tmp_path, monkeypatch):
    target = tmp_path / 'image.png'
    Image.new('RGB', (8, 8), 'red').save(target)
    previous = target.read_bytes()
    attempts = []
    def denied(source, destination):
        attempts.append(destination)
        raise PermissionError(errno.EACCES, 'Permanent denial')
    monkeypatch.setattr(ingestion, '_WINDOWS', True)
    monkeypatch.setattr(ingestion.os, 'replace', denied)
    monkeypatch.setattr(ingestion.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        ingestion._atomic_save(Image.new('RGB', (8, 8), 'blue'), target, format='PNG')
    assert len(attempts) == 8 and target.read_bytes() == previous
    assert not list(tmp_path.glob('*.partial'))


def test_headless_server_stops_gracefully_with_owned_marker(tmp_path):
    root = Path(__file__).resolve().parents[1]
    profile = tmp_path / 'Owned Profile With Spaces'
    marker = profile / 'qa.stop'
    report = tmp_path / 'ready.json'
    env = {key: value for key, value in os.environ.items() if key not in ('SIGHTVAULT_DATA', 'SIGHTVAULT_MODELS')}
    env['SIGHTVAULT_HOME'] = str(profile)
    process = subprocess.Popen([sys.executable, '-m', 'sightvault.desktop', '--serve',
                                '--report', str(report), '--stop-file', str(marker)],
                               cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while not report.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert report.exists(), 'server never became ready'
        ready = json.loads(report.read_text())
        assert ready['ready']
        marker.touch()
        output, errors = process.communicate(timeout=15)
        assert process.returncode == 0, errors
        assert not (profile / 'instance.json').exists()
        port = int(ready['url'].rsplit(':', 1)[1])
        with pytest.raises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=.5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.parametrize('stale', [False, True])
def test_headless_marker_must_be_fresh_and_inside_profile(tmp_path, stale):
    profile = tmp_path / 'profile'
    profile.mkdir()
    marker = profile / 'qa.stop' if stale else tmp_path / 'outside.stop'
    if stale:
        marker.touch()
    env = dict(os.environ, SIGHTVAULT_HOME=str(profile))
    result = subprocess.run([sys.executable, '-m', 'sightvault.desktop', '--serve', '--stop-file', str(marker)],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and '--stop-file' in result.stderr
    assert not (profile / 'instance.json').exists()
