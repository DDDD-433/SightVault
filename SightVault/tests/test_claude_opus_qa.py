"""Opus review regressions. Baseline failures are documented in OPUS_REPORT.md;
resolved cases now assert correct behavior without expected-failure markers."""
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sightvault.config import Config
from sightvault.metadata import image_metadata
from sightvault.models import Models, ModelUnavailable
from sightvault.search import Search
from sightvault.server import create_app
from scripts.build_portable_package import collect_source_files
from test_core import env, record

ROOT = Path(__file__).resolve().parent.parent
bug = lambda reason: pytest.mark.xfail(strict=True, reason=reason)

def ids(result):
    return [row['id'] for row in result['results']]

# --- Search ranking and earliest match -------------------------------------------------------------

def test_earliest_match_does_not_match_inside_longer_words(env):
    _, db, models = env
    old = db.upsert(record(1, 'Application error: please retry', '2026-01-01T00:00:00+00:00', vector=[0, 1, 0]))
    cat = db.upsert(record(2, '', '2026-05-01T00:00:00+00:00', vector=[1, 0, 0]))
    result = Search(db, models).run('earliest cat')
    assert ids(result)[0] == cat, result['results']
    assert not Search(db, models).run('cat')['results'][0]['exact_match']

@pytest.mark.parametrize('kwargs', [{'query': 'When did E404 first appear?'}, {'query': 'E404', 'mode': 'exact', 'sort': 'oldest'}])
def test_earliest_error_code_is_not_a_prefix_of_a_longer_code(env, kwargs):
    _, db, models = env
    db.upsert(record(1, 'Error E4040: gateway', '2026-01-01T00:00:00+00:00'))
    wanted = db.upsert(record(2, 'Error E404: not found', '2026-05-01T00:00:00+00:00'))
    assert ids(Search(db, models).run(**kwargs)) == [wanted]

def test_oldest_and_earliest_wording_agree(env):
    _, db, models = env
    db.upsert(record(1, 'npm ERR! code peer dependency error', '2026-01-01T00:00:00+00:00', vector=[0, 1, 0]))
    db.upsert(record(2, 'build succeeded', '2026-05-01T00:00:00+00:00', vector=[1, 0, 0]))
    search = Search(db, models)
    assert ids(search.run('oldest npm error')) == ids(search.run('earliest npm error'))

def test_earliest_match_spans_corpus_beyond_api_top_k_clamp(env):
    cfg, db, models = env
    for n in range(150):
        db.upsert(record(n + 1, 'ERESOLVE unable' if n % 7 == 0 else 'unrelated', f'2026-{1 + n % 12:02d}-{1 + n % 28:02d}T{n % 24:02d}:00:00+00:00'))
    matches = [r for r in db.all() if 'ERESOLVE' in r['ocr_text']]
    oldest = min(matches, key=lambda r: (r['timestamp'], r['id']))['id']
    with TestClient(create_app(cfg, models)) as client:
        body = client.get('/api/search', params={'q': 'When did ERESOLVE first appear?', 'top_k': 500}).json()
    assert body['sort'] == 'oldest' and body['total_matches'] == len(matches) and body['results'][0]['id'] == oldest

# --- Unicode and exact queries ---------------------------------------------------------------------

@pytest.mark.parametrize('field', ['filename', 'ocr_text'])
def test_nfc_query_matches_nfd_text(env, field):
    _, db, models = env
    row = record(1)
    row[field] = unicodedata.normalize('NFD', 'café menu.png' if field == 'filename' else 'Café menu')
    db.upsert(row)
    assert Search(db, models).run(unicodedata.normalize('NFC', 'café'), mode='exact')['total_matches'] == 1

def test_single_character_cjk_exact_query(env):
    _, db, models = env
    db.upsert(record(1, '猫の写真'))
    assert Search(db, models).run('猫', mode='exact')['total_matches'] == 1

# --- Metadata --------------------------------------------------------------------------------------

def test_exif_capture_time_from_exif_ifd_is_used(tmp_path):
    exif = Image.Exif()
    sub = exif.get_ifd(0x8769)
    sub[36867], sub[36881] = '2019:06:15 12:00:00', '+02:00'
    path = tmp_path / 'camera.jpg'
    Image.new('RGB', (8, 8)).save(path, exif=exif.tobytes())
    with Image.open(path) as image:
        meta = image_metadata(path, image)
    assert (meta['timestamp'], meta['timestamp_source']) == ('2019-06-15T10:00:00+00:00', 'EXIF capture time (with timezone)')

# --- Model status and failure recovery -------------------------------------------------------------

def _models(tmp_path, device='auto'):
    return Models(Config(root=tmp_path, data=tmp_path / 'data', models=tmp_path / 'weights', device=device))

def test_backend_fallback_label_survives_transient_error(tmp_path):
    models = _models(tmp_path, 'mps')
    models._clear_device_cache = lambda: None
    class Embedder:
        device, calls = 'mps', 0
        def encode(self, inputs, **kwargs):
            self.calls += 1
            if self.device == 'mps': raise NotImplementedError('operator not implemented for MPS')
            if self.calls == 3: raise RuntimeError('transient tokenizer failure')
            return np.ones((1, 768), dtype=np.float32)
        def to(self, device): self.device = device; return self
        def float(self): return self
    models._embedding = Embedder(); models.active['embedding'] = 'mps'
    models.embed_query('first')
    with pytest.raises(ModelUnavailable): models.embed_query('second')
    models.embed_query('third')
    assert models.status()['embedding']['device'] == 'cpu (backend fallback)'

def test_mps_oom_unloads_generative_model(tmp_path):
    import torch
    models = _models(tmp_path, 'cpu')
    class Inputs(dict):
        def to(self, device): return self
    class Processor:
        def apply_chat_template(self, *a, **k): return Inputs(input_ids=torch.zeros((1, 3), dtype=torch.long))
    class Model:
        device = 'mps:0'
        def generate(self, **k): raise RuntimeError('MPS backend out of memory (MPS allocated: 9 GB)')
    models._vlm = (Processor(), Model()); models.active['vision'] = 'mps:0'
    models._clear_device_cache = lambda: None
    with pytest.raises(ModelUnavailable):
        models.analyze(Image.new('RGB', (8, 8)), 'What is this?')
    assert models._vlm is None and 'vision' not in models.active

def test_wrong_dimension_embedding_is_rejected(tmp_path):
    # Keep the model identity truthful even if a runtime unexpectedly emits a narrower vector.
    models = _models(tmp_path, 'cpu')
    class Narrow:
        device = 'cpu'
        def encode(self, *a, **k): return np.ones((1, 512), dtype=np.float32)
    models._embedding = Narrow()
    with pytest.raises(ModelUnavailable, match='768'):
        models.embed_query('q')

# --- Codex adversarial fixes (b47d931): extended boundaries --------------------------------------

@pytest.mark.parametrize('params', [{'start': '9999-12-31T23:59:59-01:00'}, {'end': '0001-01-01T00:00:00+00:01'},
                                    {'similar_id': -10**30}, {'start': '2026-10-01', 'end': '2026-10-02T00:00:00Z'}])
def test_extended_boundary_inputs_are_validation_errors(env, params):
    cfg, _, models = env
    with TestClient(create_app(cfg, models), raise_server_exceptions=False) as client:
        response = client.get('/api/search', params=params)
    assert response.status_code in (404, 422), response.text

@pytest.mark.parametrize('path', ['/api/assets/-99999999999999999999', '/api/assets/-99999999999999999999/image'])
def test_negative_oversized_ids_are_not_server_errors(env, path):
    cfg, _, models = env
    with TestClient(create_app(cfg, models), raise_server_exceptions=False) as client:
        assert client.get(path).status_code in (404, 422)

# --- Packaging safety ------------------------------------------------------------------------------

def test_package_excludes_agent_transcripts_and_account_metadata(tmp_path):
    source_root = tmp_path / 'source'
    (source_root / 'scripts').mkdir(parents=True)
    (source_root / 'qa').mkdir()
    (source_root / 'scripts' / 'public.py').write_text('pass\n')
    fixtures = {
        'qa/claude-stream.raw': 'agent transcript content',
        'qa/CLAUDE_RUN_2026-10-30.json': '{"transcript":"private"}',
        'qa/account.json': '{"account_email":"tester@example.com"}',
    }
    for relative, content in fixtures.items():
        (source_root / relative).write_text(content)

    source_allowlist = tmp_path / 'source-allowlist.json'
    source_allowlist.write_text(json.dumps({
        'format_version': 1,
        'root_files': [],
        'trees': [{'path': 'scripts', 'extensions': ['.py']}],
    }))
    empty_qa_allowlist = ROOT / 'packaging' / 'EMPTY_QA_ALLOWLIST.json'
    selected = collect_source_files(source_root, source_allowlist, empty_qa_allowlist)

    assert set(selected) == {'scripts/public.py'}
    assert not ({relative for relative in fixtures} & set(selected))

# --- UI date boundaries (node runs the exact helper from app.js) -----------------------------------

def _local_midnight(values, tz):
    node = shutil.which('node')
    if not node: pytest.skip('node is not installed')
    source = (ROOT / 'sightvault' / 'web' / 'app.js').read_text()
    helper = re.search(r'const localMidnight=.*?;\};', source).group(0)
    script = helper + f'console.log(JSON.stringify({json.dumps(values)}.map(([v,a])=>localMidnight(v,a))));'
    output = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True, env={**os.environ, 'TZ': tz})
    return json.loads(output.stdout)

def test_ui_local_day_bounds_follow_dst():
    start, end = _local_midnight([['2026-03-08', 0], ['2026-03-08', 1]], 'America/New_York')
    assert (start, end) == ('2026-03-08T05:00:00.000Z', '2026-03-09T04:00:00.000Z')  # 23-hour local day

def test_ui_two_digit_years_are_not_remapped():
    assert _local_midnight([['0050-03-01', 0]], 'UTC') == ['0050-03-01T00:00:00.000Z']
