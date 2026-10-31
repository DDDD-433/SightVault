#!/usr/bin/env python3
"""Run bounded real-model QA against a frozen SightVault desktop executable.

The harness itself uses only the Python standard library. Model inference is
performed by the supplied frozen executable; source modules are never imported.
If psutil happens to be available in the harness environment, it also records
observed process RSS; it does not estimate unified or model peak memory.
"""

from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid


TOKEN_HEADER = 'X-SightVault-Desktop-Token'
OWNER_MARKER = '.sightvault-desktop-model-qa-owner.json'
FIXTURE_NAME = 'terminal_cuda_01.png'
EMBEDDING_MODEL = 'google/embeddinggemma-2:768:text-image'
REQUIRED_MODEL_GROUPS = ('embeddinggemma-2', 'minicpm-ocr', 'minicpm-v')
OFFLINE_NOTE = 'HF offline flags plus an in-process Python socket/DNS guard; not an OS firewall.'


class QAFailure(RuntimeError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_absolute(raw, label):
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f'--{label} must be an absolute path')
    return path.resolve()


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _app_paths(raw_executable):
    requested = Path(raw_executable).expanduser().resolve()
    if requested.suffix.lower() == '.app' and requested.is_dir():
        executable = requested / 'Contents' / 'MacOS' / 'SightVault'
        app_root = requested
    else:
        executable = requested
        app_root = next((parent for parent in (requested, *requested.parents)
                         if parent.suffix.lower() == '.app'), requested.parent)
    if not executable.is_file():
        raise ValueError('--executable must identify a frozen SightVault executable or .app bundle')
    return executable.resolve(), Path(app_root).resolve()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True, help='Frozen SightVault executable, or a macOS .app bundle')
    parser.add_argument('--profile', required=True, help='New, absolute, empty per-run profile directory')
    parser.add_argument('--models', required=True, help='Absolute checkpoint directory; never removed or replaced')
    parser.add_argument('--report', required=True, help='New JSON report file outside the profile, models and app')
    parser.add_argument('--download', action='store_true',
                        help='Explicitly run the frozen setup-worker download action before verification')
    parser.add_argument('--timeout', type=int, default=7200,
                        help='Per-task model/setup deadline in seconds (default: 7200)')
    args = parser.parse_args(argv)
    if args.timeout < 60:
        parser.error('--timeout must be at least 60 seconds for first-run model loading')
    return args


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _json_from_log(path):
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != '{':
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get('scope') == 'models':
            return value
    return None


def _log_record(path):
    path = Path(path)
    result = {'path': str(path), 'exists': path.is_file()}
    if path.is_file():
        result.update(size_bytes=path.stat().st_size, sha256=_sha256(path))
    return result


class FrozenDesktopModelQA:
    def __init__(self, args):
        self.args = args
        self.executable, self.app_root = _app_paths(args.executable)
        self.profile = _resolved_absolute(args.profile, 'profile')
        self.models = _resolved_absolute(args.models, 'models')
        self.report_path = _resolved_absolute(args.report, 'report')
        self.owner_id = str(uuid.uuid4())
        self.stage = 'preflight'
        self.process = None
        self.instance_token = None
        self.base_url = None
        self.resources = None
        self.data_dir = None
        self.db_path = None
        self.fixture_dir = None
        self.stop_file = None
        self.opener = build_opener(ProxyHandler({}))
        self.report = {
            'schema_version': 1,
            'artifact': 'SightVault frozen desktop real-model QA',
            'started_utc': _utc_now(),
            'finished_utc': None,
            'passed': False,
            'owner_id': self.owner_id,
            'profile': str(self.profile),
            'executable': str(self.executable),
            'application_root': str(self.app_root),
            'models': str(self.models),
            'timeout_seconds': args.timeout,
            'download_requested': bool(args.download),
            'checks': {},
            'errors': [],
            'setup': {},
            'server': {'pid': None, 'url': None, 'shutdown': {}},
            'offline': {'isolation_method': OFFLINE_NOTE, 'socket_guard': None},
            'runtime_samples': {},
            'model_runs': {},
            'memory': {'measurement': 'observed process RSS; not unified/model peak', 'samples': {}},
        }

    def _check(self, key, condition, message):
        self.report['checks'][key] = bool(condition)
        if not condition:
            raise QAFailure(self.stage, message)

    def _preflight(self):
        self.stage = 'preflight'
        for path, name in ((self.profile, 'profile'), (self.models, 'models'), (self.report_path, 'report')):
            if not path.is_absolute():
                raise QAFailure(self.stage, f'{name} path must be absolute')
        if self.report_path.exists():
            raise QAFailure(self.stage, 'Report path already exists; choose a new report file')
        if (_is_within(self.report_path, self.profile) or _is_within(self.report_path, self.models)
                or _is_within(self.report_path, self.app_root)):
            raise QAFailure(self.stage, 'Report must be outside the profile, model directory and frozen application')
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        if self.profile.exists() or self.profile.is_symlink():
            raise QAFailure(self.stage, 'Profile path must be new; existing profile data was left untouched')
        if not self.models.exists() and not self.args.download:
            raise QAFailure(self.stage, 'Model directory is missing; use --download explicitly or supply checkpoints')
        if self.models.exists() and not self.models.is_dir():
            raise QAFailure(self.stage, 'Model path must be a directory')
        if self.models == self.profile or _is_within(self.models, self.profile) or _is_within(self.profile, self.models):
            raise QAFailure(self.stage, 'Profile and model directories must not overlap')
        if _is_within(self.profile, self.app_root) or _is_within(self.models, self.app_root):
            raise QAFailure(self.stage, 'Profile and model directories must stay outside the frozen application')
        self.profile.parent.mkdir(parents=True, exist_ok=True)
        self.profile.mkdir(mode=0o700, exist_ok=False)
        marker = self.profile / OWNER_MARKER
        marker.write_text(json.dumps({'owner_id': self.owner_id, 'created_utc': _utc_now()}, indent=2) + '\n',
                          encoding='utf-8')
        self.stop_file = self.profile / 'qa.stop'
        self.data_dir = self.profile / 'data'
        self.db_path = self.data_dir / 'sightvault.sqlite3'
        self._check('fresh_owned_profile', marker.is_file() and _read_json(marker).get('owner_id') == self.owner_id,
                    'Could not create the owned fresh profile')
        self.report['ownership_marker'] = str(marker)

    def _run_setup(self, action, *, online=False):
        self.stage = 'setup-' + action
        console_log = self.profile / 'logs' / f'setup-{action}.log'
        stdio_log = self.profile / 'logs' / f'setup-{action}-stdio.log'
        console_log.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(PYTHONUNBUFFERED='1', PYINSTALLER_RESET_ENVIRONMENT='1',
                   SIGHTVAULT_SETUP_LOG=str(stdio_log), HF_HUB_DISABLE_TELEMETRY='1')
        env.pop('SIGHTVAULT_QA_OFFLINE_GUARD', None)
        env['HF_HUB_OFFLINE'] = '0' if online else '1'
        env['TRANSFORMERS_OFFLINE'] = '0' if online else '1'
        command = [str(self.executable), '--setup-worker', action, '--model-root', str(self.models)]
        started = time.monotonic()
        result = {'attempted': True, 'command_mode': 'frozen setup-worker', 'network_allowed': online,
                  'timed_out': False,
                  'log': str(console_log), 'stdio_log': str(stdio_log)}
        try:
            with console_log.open('xb') as output:
                completed = subprocess.run(command, cwd=self.app_root, env=env, stdin=subprocess.DEVNULL,
                                            stdout=output, stderr=subprocess.STDOUT,
                                            timeout=self.args.timeout, check=False, close_fds=True)
            result['exit_code'] = completed.returncode
        except subprocess.TimeoutExpired:
            result.update(exit_code=None, timed_out=True)
            self.report['setup'][action] = result
            result['elapsed_seconds'] = round(time.monotonic() - started, 2)
            result['logs'] = [_log_record(console_log), _log_record(stdio_log)]
            raise QAFailure(self.stage, 'Frozen setup-worker exceeded the configured deadline')
        except OSError as exc:
            result.update(exit_code=None, launch_error=type(exc).__name__)
            raise QAFailure(self.stage, 'Could not start the frozen setup-worker') from None
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
        result['logs'] = [_log_record(console_log), _log_record(stdio_log)]
        if action == 'verify':
            verification = _json_from_log(console_log) or _json_from_log(stdio_log)
            if verification is not None:
                result['result'] = verification
        self.report['setup'][action] = result
        if result.get('exit_code') != 0:
            raise QAFailure(self.stage, f'Frozen setup-worker {action} did not succeed')
        return result

    def _prepare_models(self):
        if self.args.download:
            self._run_setup('download', online=True)
        verify = self._run_setup('verify', online=False)
        verification = verify.get('result')
        good = (isinstance(verification, dict) and verification.get('passed') is True
                and isinstance(verification.get('checked'), int)
                and verification.get('checked', 0) > 0)
        self._check('frozen_model_checksums', good,
                    'Frozen checksum verification did not confirm the required model files')
        self.report['setup']['verified_model_groups'] = REQUIRED_MODEL_GROUPS

    def _server_environment(self):
        env = dict(os.environ)
        # Explicitly reset product settings that can otherwise change the QA backend.
        for name in ('SIGHTVAULT_DATA', 'SIGHTVAULT_LLAMA_SERVER'):
            env.pop(name, None)
        env.update(SIGHTVAULT_HOME=str(self.profile), SIGHTVAULT_MODELS=str(self.models),
                   SIGHTVAULT_DEVICE='auto', SIGHTVAULT_VISION='minicpm', SIGHTVAULT_OCR='auto',
                   SIGHTVAULT_LFM3_THINKING_TOKENS='0', SIGHTVAULT_QA_OFFLINE_GUARD='1',
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   PYTHONUNBUFFERED='1', PYINSTALLER_RESET_ENVIRONMENT='1')
        return env

    def _start_server(self):
        self.stage = 'server-start'
        ready_file = self.profile / 'server-ready.json'
        server_log = self.profile / 'logs' / 'frozen-server.log'
        server_log.parent.mkdir(parents=True, exist_ok=True)
        if self.stop_file.exists() or ready_file.exists() or server_log.exists():
            raise QAFailure(self.stage, 'Fresh profile already contains a server control artifact')
        command = [str(self.executable), '--serve', '--report', str(ready_file), '--stop-file', str(self.stop_file)]
        try:
            with server_log.open('xb') as output:
                self.process = subprocess.Popen(command, cwd=self.app_root, env=self._server_environment(),
                                                stdin=subprocess.DEVNULL, stdout=output,
                                                stderr=subprocess.STDOUT, close_fds=True)
        except OSError:
            raise QAFailure(self.stage, 'Could not start the frozen server') from None
        self.report['server'].update(pid=self.process.pid, log=_log_record(server_log),
                                     ready_report=str(ready_file), stop_file=str(self.stop_file))
        deadline = time.monotonic() + min(float(self.args.timeout), 180.0)
        ready = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise QAFailure(self.stage, 'Frozen server exited before readiness')
            if ready_file.is_file():
                try:
                    ready = _read_json(ready_file)
                except (OSError, ValueError):
                    ready = None
                if isinstance(ready, dict) and ready.get('ready') is True:
                    break
            time.sleep(0.2)
        if not isinstance(ready, dict) or ready.get('ready') is not True:
            raise QAFailure(self.stage, 'Frozen server did not become ready before the deadline')
        self._check('server_ready', True, 'Frozen server readiness check failed')
        self.report['server']['ready_report'] = {'ready': True, 'url': ready.get('url')}
        self._validate_instance(ready)

    def _validate_instance(self, ready_report):
        self.stage = 'instance-validation'
        instance_path = self.profile / 'instance.json'
        try:
            record = _read_json(instance_path)
        except (OSError, ValueError):
            raise QAFailure(self.stage, 'Frozen server instance record is missing or invalid') from None
        if not isinstance(record, dict):
            raise QAFailure(self.stage, 'Frozen server instance record is missing or invalid')
        url = record.get('url')
        try:
            parts = urlsplit(url)
            port = parts.port
        except (TypeError, ValueError):
            raise QAFailure(self.stage, 'Frozen server instance URL is invalid') from None
        token = record.get('token') if isinstance(record, dict) else None
        valid = (parts.scheme == 'http' and parts.hostname == '127.0.0.1' and port is not None
                 and 0 < port < 65536 and parts.path in ('', '/') and not parts.username and not parts.password
                 and not parts.query and not parts.fragment)
        same_process = type(record.get('pid')) is int and record['pid'] == self.process.pid
        valid_token = isinstance(token, str) and re.fullmatch(r'[0-9a-f]{64}', token) is not None
        self._check('owned_instance_record', bool(valid and same_process and valid_token
                                                    and ready_report.get('url') == url),
                    'Instance record did not belong to the frozen process or failed loopback/token validation')
        self.base_url, self.instance_token = url.rstrip('/'), token
        self.report['server'].update(url=self.base_url, instance_pid=record['pid'],
                                     instance_token_valid=True, token_recorded=False)

    def _request_json(self, path, *, method='GET', payload=None, desktop=False, timeout=None):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if desktop:
            headers[TOKEN_HEADER] = self.instance_token
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout or min(30, self.args.timeout)) as response:
                if response.status != 200:
                    raise QAFailure(self.stage, f'Local API returned HTTP {response.status}')
                return json.loads(response.read().decode('utf-8'))
        except QAFailure:
            raise
        except HTTPError as exc:
            raise QAFailure(self.stage, f'Local API returned HTTP {exc.code}') from None
        except (URLError, OSError, ValueError):
            raise QAFailure(self.stage, 'Local API request failed or returned invalid JSON') from None

    def _start_diagnostics(self):
        self.stage = 'guarded-diagnostics'
        status = self._request_json('/api/status')
        diagnostics = self._request_json('/desktop/api/diagnostics', desktop=True)
        app = diagnostics.get('app') or {}
        paths = diagnostics.get('paths') or {}
        offline = diagnostics.get('offline') or {}
        guard = offline.get('socket_guard') or {}
        same_executable = False
        try:
            same_executable = Path(app.get('executable', '')).resolve() == self.executable
        except (OSError, TypeError, ValueError):
            pass
        instance_root = status.get('instance_root')
        resource_path_value = paths.get('resources')
        has_resource_paths = (isinstance(instance_root, str) and bool(instance_root)
                              and Path(instance_root).is_absolute()
                              and isinstance(resource_path_value, str) and bool(resource_path_value)
                              and Path(resource_path_value).is_absolute())
        self.resources = Path(instance_root).resolve() if has_resource_paths else Path('/__invalid_sightvault_resources__')
        resource_path = Path(resource_path_value).resolve() if has_resource_paths else Path('/__invalid_sightvault_resources_other__')
        self.data_dir = Path(paths.get('data', '')).resolve()
        self.db_path = self.data_dir / 'sightvault.sqlite3'
        expected_data = (self.profile / 'data').resolve()
        self._check('frozen_instance', app.get('frozen') is True and same_executable,
                    'API diagnostics did not identify the supplied frozen executable')
        self._check('owned_profile_paths', Path(paths.get('base', '')).resolve() == self.profile
                    and self.data_dir == expected_data and Path(paths.get('models', '')).resolve() == self.models,
                    'Frozen app storage paths did not match the owned QA profile and supplied models')
        self._check('resource_root_matches_status', has_resource_paths and self.resources == resource_path
                    and _is_within(self.resources, self.app_root),
                    'Status instance_root did not match resources inside the supplied frozen application')
        self._check('bundle_manifests_present',
                    all((self.resources / relative).is_file() for relative in
                        ('build-info.json', 'handoff/SAMPLE_MANIFEST.json', 'handoff/MODEL_MANIFEST.json')),
                    'Frozen resource root is missing build or model/sample manifests')
        self._check('loopback_only_server', diagnostics.get('server', {}).get('loopback_only') is True
                    and status.get('local_only') is True and status.get('cloud_api_calls') == 0,
                    'Frozen app status did not confirm local loopback operation')
        self._check('offline_flags', offline.get('HF_HUB_OFFLINE') == '1'
                    and offline.get('TRANSFORMERS_OFFLINE') == '1',
                    'Frozen app did not retain its offline model flags')
        self._check('socket_guard_probe', guard.get('installed') is True and guard.get('probe_denied') is True,
                    'Frozen app did not report an installed socket guard with a denied self-test')
        self.report['offline']['socket_guard'] = {
            'installed': guard.get('installed'),
            'blocked_attempts': guard.get('blocked_attempts'),
            'probe_denied': guard.get('probe_denied'),
        }
        self.report['bundle'] = self._bundle_evidence()
        self.report['initial_status'] = status
        self.report['initial_diagnostics'] = diagnostics
        self._record_runtime('before-demo-import', status.get('models') or {})

    def _bundle_evidence(self):
        try:
            build_info = _read_json(self.resources / 'build-info.json')
            model_manifest = _read_json(self.resources / 'handoff' / 'MODEL_MANIFEST.json')
            sample_manifest = _read_json(self.resources / 'handoff' / 'SAMPLE_MANIFEST.json')
        except (OSError, ValueError):
            raise QAFailure(self.stage, 'Frozen build or model/sample manifest could not be read') from None
        model_sources = model_manifest.get('sources') or {}
        revisions = {}
        for name in REQUIRED_MODEL_GROUPS:
            source = model_sources.get(name)
            if not isinstance(source, dict) or not source.get('repo') or not source.get('revision'):
                raise QAFailure(self.stage, 'Frozen model manifest is missing a required pinned model revision')
            revisions[name] = {'repo': source['repo'], 'revision': source['revision']}
        sample = next((entry for entry in sample_manifest
                       if entry.get('path') == 'samples/' + FIXTURE_NAME), None)
        if not sample or not isinstance(sample.get('sha256'), str):
            raise QAFailure(self.stage, 'Frozen sample manifest does not list the CUDA fixture')
        sample_path = self.resources / 'samples' / FIXTURE_NAME
        if not sample_path.is_file() or sample_path.is_symlink() or _sha256(sample_path) != sample['sha256']:
            raise QAFailure(self.stage, 'Bundled CUDA fixture did not match its frozen sample manifest')
        revision = build_info.get('source_revision')
        if not isinstance(revision, str) or not revision:
            raise QAFailure(self.stage, 'Frozen build-info.json is missing source_revision')
        return {
            'source_revision': revision,
            'model_revisions': revisions,
            'fixture': {'filename': FIXTURE_NAME, 'sha256': sample['sha256'], 'size_bytes': sample.get('size')},
            'sample_manifest_path': str(self.resources / 'handoff' / 'SAMPLE_MANIFEST.json'),
            'model_manifest_path': str(self.resources / 'handoff' / 'MODEL_MANIFEST.json'),
            'build_info_path': str(self.resources / 'build-info.json'),
        }

    def _sample_rss(self, label):
        value = None
        if self.process is not None:
            try:
                import psutil
                value = psutil.Process(self.process.pid).memory_info().rss
            except (ImportError, OSError, ValueError):
                value = None
        self.report['memory']['samples'][label] = value

    def _record_runtime(self, label, models):
        ocr = models.get('ocr') or {}
        vision = models.get('vision') or {}
        embedding = models.get('embedding') or {}
        no_dual_generator = not (ocr.get('loaded') is True and vision.get('loaded') is True)
        self.report['runtime_samples'][label] = {
            'embedding': embedding,
            'ocr': ocr,
            'vision': vision,
            'single_generator_slot': no_dual_generator,
        }
        self._check('single_generator_slot_' + label, no_dual_generator,
                    'Diagnostics reported OCR and vision generators loaded at the same time')

    def _import_demo(self):
        self.stage = 'demo-import'
        imported = self._request_json('/desktop/api/demo/import', method='POST', payload={}, desktop=True,
                                      timeout=min(120, self.args.timeout))
        self._check('packaged_demo_imported', imported.get('imported') == 62,
                    'Frozen public demo import did not import exactly 62 assets')
        folder = Path(imported.get('folder', '')).resolve()
        self._check('demo_folder_owned', _is_within(folder, self.profile),
                    'Imported demo folder was outside the owned profile')
        self.data_dir = Path(self.profile / 'data').resolve()
        self.db_path = self.data_dir / 'sightvault.sqlite3'
        status = self._request_json('/api/status')
        self._check('demo_status_62', status.get('total') == 62 and status.get('with_embeddings') == 62,
                    'Frozen app status did not show the 62 embedded public demo assets')
        self.report['demo'] = {'import': imported, 'status_after_import': status}
        self.report['server']['data_directory'] = str(self.data_dir)
        self._record_runtime('after-demo-import', status.get('models') or {})
        return folder

    def _copy_fixture(self):
        self.stage = 'fixture-copy'
        source = self.resources / 'samples' / FIXTURE_NAME
        self.fixture_dir = self.profile / 'fixture-pixels'
        self.fixture_dir.mkdir(mode=0o700, exist_ok=False)
        target = self.fixture_dir / FIXTURE_NAME
        with source.open('rb') as inp, target.open('xb') as out:
            shutil.copyfileobj(inp, out)
        source_hash = _sha256(source)
        target_hash = _sha256(target)
        self._check('owned_fresh_fixture', source_hash == target_hash == self.report['bundle']['fixture']['sha256'],
                    'Owned fixture copy did not match the bundled sample manifest')
        self.report['fixture'] = {'source': str(source), 'owned_copy': str(target),
                                  'sha256': target_hash, 'owned_directory': str(self.fixture_dir)}

    def _wait_job(self, job_id, stage):
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            if self.process is None or self.process.poll() is not None:
                raise QAFailure(stage, 'Frozen server exited while an indexing job was running')
            job = self._request_json('/api/jobs/' + str(job_id), timeout=min(30, self.args.timeout))
            if job.get('state') in ('complete', 'error', 'cancelled'):
                return job
            time.sleep(1)
        raise QAFailure(stage, 'Indexing job exceeded the configured CPU deadline')

    def _index(self, label, ocr):
        self.stage = label
        started = time.monotonic()
        response = self._request_json('/api/index', method='POST',
                                      payload={'folder': str(self.fixture_dir), 'force': True, 'ocr': ocr},
                                      timeout=min(30, self.args.timeout))
        job_id = response.get('job_id')
        if not isinstance(job_id, str) or not job_id:
            raise QAFailure(self.stage, 'Index API did not return a job identifier')
        job = self._wait_job(job_id, self.stage)
        result = {'ocr_requested': ocr, 'job_id': job_id, 'state': job.get('state'),
                  'processed': job.get('processed'), 'indexed': job.get('indexed'),
                  'updated': job.get('updated'), 'failed': job.get('failed'),
                  'elapsed_seconds': round(time.monotonic() - started, 2),
                  'job_elapsed_seconds': job.get('elapsed_s')}
        self.report['model_runs'][label] = result
        good = (job.get('state') == 'complete' and job.get('processed') == 1 and job.get('failed') == 0
                and (job.get('indexed', 0) + job.get('updated', 0)) == 1)
        self._check(label + '_completed', good, f'{ocr} indexing did not complete for the single fresh image')
        return job

    def _read_asset(self, filepath):
        if self.db_path.is_symlink() or not self.db_path.is_file() or not _is_within(self.db_path.resolve(), self.profile):
            raise QAFailure(self.stage, 'Owned fixture SQLite database is missing or outside the profile')
        uri = self.db_path.resolve().as_uri() + '?mode=ro'
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute('SELECT id, filepath, filename, sha256, ocr_text, ocr_backend, ocr_error, '
                                 'embedding, embedding_model, embedding_error FROM assets WHERE filepath = ?',
                                 (str(filepath),)).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:
            raise QAFailure(self.stage, 'Could not read the owned fixture SQLite database') from None

    def _compare_fixture_embedding(self, demo_folder):
        self.stage = 'embedding-check'
        baseline = self._read_asset(demo_folder / FIXTURE_NAME)
        fresh = self._read_asset(self.fixture_dir / FIXTURE_NAME)
        self._check('fixture_rows_present', baseline is not None and fresh is not None,
                    'Owned SQLite is missing either the imported demo row or fresh fixture row')
        vectors = []
        for row in (baseline, fresh):
            blob = row.get('embedding')
            if (not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) != 768 * 4
                    or row.get('embedding_model') != EMBEDDING_MODEL or row.get('embedding_error') is not None):
                raise QAFailure(self.stage, 'Fixture image did not store the direct 768-dimensional image embedding')
            vector = struct.unpack('<768f', bytes(blob))
            if not all(math.isfinite(value) for value in vector):
                raise QAFailure(self.stage, 'Fixture image embedding contains non-finite values')
            norm = math.sqrt(math.fsum(value * value for value in vector))
            if not math.isfinite(norm) or abs(norm - 1.0) > 0.01:
                raise QAFailure(self.stage, 'Fixture image embedding was not normalized to unit length')
            vectors.append(vector)
        cosine = math.fsum(left * right for left, right in zip(vectors[0], vectors[1]))
        good = cosine >= 0.99
        self._check('embedding_matches_demo', good,
                    'Fresh-image embedding cosine was below the 0.99 demo-fixture threshold')
        self.report['model_runs']['embedding'] = {
            'model': EMBEDDING_MODEL,
            'dimensions': 768,
            'baseline_norm': math.sqrt(math.fsum(value * value for value in vectors[0])),
            'fresh_norm': math.sqrt(math.fsum(value * value for value in vectors[1])),
            'cosine_similarity': cosine,
            'threshold': 0.99,
            'fresh_asset_id': fresh['id'],
            'fresh_path': fresh['filepath'],
        }
        self._check('asset_count_63', self._sqlite_asset_count() == 63,
                    'Expected the imported 62 assets plus exactly one owned fixture row')
        return fresh

    def _sqlite_asset_count(self):
        uri = self.db_path.resolve().as_uri() + '?mode=ro'
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
                return db.execute('SELECT count(*) FROM assets').fetchone()[0]
        except (OSError, sqlite3.Error):
            return None

    def _check_ocr_row(self, label, filepath, expected_backend):
        row = self._read_asset(filepath)
        if row is None:
            raise QAFailure(self.stage, 'Owned fixture row was not persisted after indexing')
        text = row.get('ocr_text') or ''
        has_cuda = 'cuda' in text.lower()
        record = {'backend': row.get('ocr_backend'), 'error': row.get('ocr_error'),
                  'text_characters': len(text), 'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                  'text_contains_cuda': has_cuda, 'asset_id': row.get('id')}
        self.report['model_runs'][label]['ocr_result'] = record
        good = row.get('ocr_backend') == expected_backend and row.get('ocr_error') is None and has_cuda
        self._check(label + '_actual_backend_and_text', good,
                    f'Expected {expected_backend} to return CUDA text without an OCR error or fallback')
        return row

    def _search_cuda_fixture(self):
        self.stage = 'semantic-search'
        query = 'GPU memory error'
        path = '/api/search?' + urlencode({'q': query, 'mode': 'semantic', 'top_k': 10})
        result = self._request_json(path, timeout=self.args.timeout)
        items = result.get('results') or []
        names = [item.get('filename', '') for item in items if isinstance(item, dict)]
        found = any(name.lower().startswith('terminal_cuda_') for name in names)
        self._check('semantic_cuda_screenshot', found,
                    'Semantic search for GPU memory error did not return a CUDA screenshot')
        self.report['model_runs']['semantic_search'] = {
            'query': query, 'mode': 'semantic', 'latency_ms': result.get('latency_ms'),
            'total_matches': result.get('total_matches'), 'top_filenames': names[:5],
            'returned_cuda_screenshot': found, 'warning': result.get('warning'),
        }

    def _analyze(self, asset_id):
        self.stage = 'vision-analysis'
        started = time.monotonic()
        result = self._request_json('/api/analyze', method='POST', payload={
            'asset_id': asset_id,
            'question': 'What visible error is shown? Explain the memory issue using evidence visible in the image.',
        }, timeout=self.args.timeout)
        elapsed = round(time.monotonic() - started, 2)
        answer = str(result.get('answer') or '').strip()
        model_id = result.get('model_id')
        status = self._request_json('/api/status')
        models = status.get('models') or {}
        vision = models.get('vision') or {}
        good = (len(answer) >= 12 and 'memory' in answer.lower()
                and model_id == 'openbmb/MiniCPM-V-4.7-1B'
                and result.get('backend') == 'minicpm'
                and result.get('latency_ms') is not None and result.get('latency_ms') > 0
                and vision.get('loaded') is True and bool(vision.get('device')))
        self._check('minicpm_v_analysis', good,
                    'MiniCPM-V did not return a meaningful memory answer with its exact model ID and device status')
        self.report['model_runs']['analysis'] = {
            'answer': answer[:1000], 'answer_characters': len(answer), 'model': result.get('model'),
            'model_id': model_id, 'backend': result.get('backend'), 'runtime': result.get('runtime'),
            'device': result.get('device'), 'precision': result.get('precision'),
            'model_revision': result.get('model_revision'), 'model_quantization': result.get('model_quantization'),
            'latency_ms': result.get('latency_ms'), 'wall_seconds': elapsed,
            'generation': result.get('generation'), 'runtime_status': vision,
        }
        self.report['final_status_after_analysis'] = status
        self._record_runtime('after-analysis', models)
        self._sample_rss('after-analysis')

    def _finish_status(self):
        self.stage = 'final-status'
        status = self._request_json('/api/status')
        diagnostics = self._request_json('/desktop/api/diagnostics', desktop=True)
        models = status.get('models') or {}
        self._record_runtime('after-rapid-ocr', models)
        self._sample_rss('after-rapid-ocr')
        self._check('final_asset_count_63', status.get('total') == 63 and status.get('with_embeddings') == 63,
                    'Final app status did not show 63 assets with embeddings')
        self._check('final_offline_guard', diagnostics.get('offline', {}).get('socket_guard', {}).get('installed') is True
                    and diagnostics.get('offline', {}).get('socket_guard', {}).get('probe_denied') is True,
                    'Final diagnostics no longer reported the offline socket-guard self-test')
        self.report['final_status'] = status
        self.report['final_diagnostics'] = diagnostics
        self.report['offline']['socket_guard'] = {
            'installed': diagnostics['offline']['socket_guard'].get('installed'),
            'blocked_attempts': diagnostics['offline']['socket_guard'].get('blocked_attempts'),
            'probe_denied': diagnostics['offline']['socket_guard'].get('probe_denied'),
        }

    def run(self):
        self._preflight()
        self._prepare_models()
        self._start_server()
        self._start_diagnostics()
        demo_folder = self._import_demo()
        self._copy_fixture()

        self._index('minicpm_ocr_index', 'minicpm')
        first_status = self._request_json('/api/status')
        self._record_runtime('after-minicpm-ocr', first_status.get('models') or {})
        self._sample_rss('after-minicpm-ocr')
        fresh = self._compare_fixture_embedding(demo_folder)
        ocr_row = self._check_ocr_row('minicpm_ocr_index', self.fixture_dir / FIXTURE_NAME,
                                      'MiniCPM-OCR 1.0')
        runtime_ocr = (first_status.get('models') or {}).get('ocr') or {}
        self._check('minicpm_ocr_loaded_on_device', runtime_ocr.get('loaded') is True
                    and bool(runtime_ocr.get('device')) and not runtime_ocr.get('error'),
                    'MiniCPM-OCR inference status did not report a loaded model/device')
        runtime_embedding = (first_status.get('models') or {}).get('embedding') or {}
        self._check('embedding_loaded_on_device', runtime_embedding.get('loaded') is True
                    and bool(runtime_embedding.get('device')) and bool(runtime_embedding.get('precision'))
                    and runtime_embedding.get('error') is None,
                    'EmbeddingGemma force-inference status did not report a loaded model/device/precision without errors')
        self._search_cuda_fixture()
        self._analyze(fresh['id'])

        self._index('rapid_ocr_index', 'rapid')
        rapid_row = self._check_ocr_row('rapid_ocr_index', self.fixture_dir / FIXTURE_NAME,
                                        'RapidOCR (local)')
        self._check('rapid_ocr_distinct_from_fallback', rapid_row.get('ocr_backend') == 'RapidOCR (local)'
                    and rapid_row.get('ocr_error') is None,
                    'RapidOCR fixture run reported a fallback or error')
        self.report['model_runs']['rapid_ocr_index']['asset_id'] = rapid_row['id']
        self.report['model_runs']['rapid_ocr_index']['mini_cpm_row_after_switch'] = {
            'backend_before_rapid_run': ocr_row.get('ocr_backend'), 'asset_id': ocr_row.get('id')}
        self._finish_status()
        self._check('ownership_marker_retained',
                    _read_json(self.profile / OWNER_MARKER).get('owner_id') == self.owner_id,
                    'Profile ownership marker changed during QA')

    def stop_server(self):
        if self.process is None:
            return
        self.stage = 'server-shutdown'
        shutdown = self.report['server']['shutdown']
        if self.process.poll() is None:
            try:
                self.stop_file.touch(exist_ok=False)
                shutdown['stop_marker_written'] = True
            except OSError:
                shutdown['stop_marker_written'] = False
            try:
                self.process.wait(timeout=min(60, self.args.timeout))
                shutdown['exit_code'] = self.process.returncode
                shutdown['graceful'] = bool(shutdown.get('stop_marker_written') and self.process.returncode == 0)
                shutdown['force_killed'] = False
            except subprocess.TimeoutExpired:
                # This is our exact child process. Report deadline cleanup explicitly.
                shutdown['graceful'] = False
                shutdown['deadline_exceeded'] = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                    shutdown['exit_code'] = self.process.returncode
                    shutdown['force_killed'] = False
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
                    shutdown['exit_code'] = self.process.returncode
                    shutdown['force_killed'] = True
        else:
            shutdown['exit_code'] = self.process.returncode
            shutdown['graceful'] = False
            shutdown['force_killed'] = False
        shutdown['log'] = _log_record(self.profile / 'logs' / 'frozen-server.log')
        if 'server_stopped_gracefully' not in self.report['checks']:
            self.report['checks']['server_stopped_gracefully'] = shutdown.get('graceful') is True
        if shutdown.get('graceful') is not True:
            self.report['errors'].append({'stage': self.stage,
                                          'message': 'Frozen server did not stop gracefully from the owned stop marker'})

    def finalize(self):
        self.report['finished_utc'] = _utc_now()
        self.report['passed'] = bool(self.report['checks']) and all(self.report['checks'].values()) \
            and not self.report['errors']
        self.report['memory']['available'] = any(value is not None
                                                 for value in self.report['memory']['samples'].values())


def main(argv=None):
    args = _parse_args(argv)
    try:
        harness = FrozenDesktopModelQA(args)
    except (OSError, ValueError) as exc:
        print(f'QA preflight error: {type(exc).__name__}', file=sys.stderr)
        return 2
    try:
        harness.run()
    except QAFailure as exc:
        harness.report['errors'].append({'stage': exc.stage, 'message': str(exc)})
    except KeyboardInterrupt:
        harness.report['errors'].append({'stage': harness.stage, 'message': 'Interrupted by operator'})
    except Exception as exc:
        # Avoid copying arbitrary exception text into the report; it may contain
        # local request/config details that do not belong in QA output.
        harness.report['errors'].append({'stage': harness.stage,
                                         'message': f'Unexpected {type(exc).__name__}; details omitted'})
    finally:
        try:
            harness.stop_server()
        except Exception as exc:
            harness.report['errors'].append({'stage': 'server-shutdown',
                                             'message': f'Unexpected {type(exc).__name__}; details omitted'})
        harness.finalize()
        try:
            with harness.report_path.open('x', encoding='utf-8') as stream:
                json.dump(harness.report, stream, indent=2, ensure_ascii=False)
                stream.write('\n')
        except OSError:
            print('Could not write the requested new QA report file.', file=sys.stderr)
            return 2
    summary = {'passed': harness.report['passed'], 'report': str(harness.report_path),
               'checks': harness.report['checks'], 'errors': harness.report['errors']}
    print(json.dumps(summary, indent=2))
    return 0 if harness.report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
