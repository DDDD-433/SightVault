"""Regression tests for issues found in the Oct 29 QA pass."""
import os
import threading
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sightvault.config import Config
from sightvault.database import Database
from sightvault.ingestion import Ingestor
from sightvault.models import Models, ModelUnavailable
from sightvault.search import Search, date_bounds
from sightvault.server import create_app
from test_core import FakeModels, record

@pytest.fixture
def env(tmp_path):
    cfg = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models')
    cfg.prepare()
    return cfg, Database(cfg.db_path), FakeModels()

@pytest.mark.parametrize('query,expected', [
    ('first normal form', 'relevance'),
    ('Show the first slide about joins', 'relevance'),
    ('When did the CUDA error first occur?', 'oldest'),
    ('When was ERESOLVE first seen', 'oldest'),
    ('earliest receipt', 'oldest'),
    ('first time I saw a red backpack', 'oldest'),
])
def test_temporal_intent_not_triggered_by_topic_words(env, query, expected):
    _, db, models = env
    db.upsert(record(1, 'First normal form: atomic values'))
    assert Search(db, models).run(query)['sort'] == expected

def test_local_midnight_bounds_are_converted_to_utc():
    low, high = date_bounds('2026-10-20T00:00:00-04:00', '2026-10-21T00:00:00-04:00')
    assert low == '2026-10-20T04:00:00+00:00' and high == '2026-10-21T04:00:00+00:00'
    with pytest.raises(ValueError): date_bounds('2026-10-20T00:00:00', None)

def test_local_day_filter_includes_late_evening_image(env):
    _, db, models = env
    db.upsert(record(1, stamp='2026-10-21T02:30:00+00:00'))  # Oct 20, 22:30 in New York
    found = Search(db, models).run('', start='2026-10-20T00:00:00-04:00', end='2026-10-21T00:00:00-04:00')
    assert len(found['results']) == 1

def test_ingestion_writes_no_partial_files_and_flags_renamed_sources(env, tmp_path):
    cfg, db, models = env
    folder = tmp_path/'pics'; folder.mkdir()
    Image.new('RGB', (50, 40), 'green').save(folder/'old.png')
    Ingestor(cfg, db, models).run(folder)
    assert not list((cfg.data).rglob('*.partial'))
    (folder/'old.png').rename(folder/'new.png')
    report = Ingestor(cfg, db, models).run(folder)
    assert report['missing'] == 1 and any('no longer in this folder' in w for w in report['warnings'])
    with TestClient(create_app(cfg, models)) as client:
        rows = {r['filename']: r['id'] for r in db.all()}
        assert client.get(f"/api/assets/{rows['old.png']}").json()['source_exists'] is False
        assert client.get(f"/api/assets/{rows['new.png']}").json()['source_exists'] is True

def test_images_are_not_embeddable_cross_origin(env):
    cfg, db, models = env
    with TestClient(create_app(cfg, models)) as client:
        assert client.get('/').headers['cross-origin-resource-policy'] == 'same-origin'

class _FakeEmbedder:
    def encode(self, inputs, **kwargs):
        return np.ones((len(inputs), 768), dtype=np.float32)

def test_search_embedding_not_blocked_by_running_generation(tmp_path):
    import torch  # warm import so the timing below measures locking only
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models'))
    models._embedding = _FakeEmbedder()
    held, release = threading.Event(), threading.Event()
    def generation():
        with models.lock:
            held.set(); release.wait(60)
    worker = threading.Thread(target=generation); worker.start(); held.wait(5)
    result = {}
    probe = threading.Thread(target=lambda: result.setdefault('v', models.embed_query('gpu')))
    probe.start(); probe.join(10)
    got_while_generating = 'v' in result and worker.is_alive()
    release.set(); worker.join()
    assert got_while_generating, 'query embedding waited for the generative-model lock'

def test_gpu_oom_unloads_model_and_reports_actionable_error(tmp_path):
    import torch
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models'))
    class Inputs(dict):
        def to(self, device): return self
    class Processor:
        def apply_chat_template(self, *a, **k): return Inputs(input_ids=torch.zeros((1, 3), dtype=torch.long))
    class Model:
        device = 'cpu'
        def generate(self, **k): raise torch.cuda.OutOfMemoryError('CUDA out of memory')
    models._vlm = (Processor(), Model()); models.active['vision'] = 'cuda'
    with pytest.raises(ModelUnavailable, match='GPU memory is full'):
        models.analyze(Image.new('RGB', (8, 8)), 'What is this?')
    assert models._vlm is None and 'vision' not in models.active

def test_missing_models_degrade_with_actionable_messages(tmp_path):
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'empty-models'))
    assert not any(state['available'] for state in models.status().values())
    with pytest.raises(ModelUnavailable, match='download_models.py'):
        models.analyze(Image.new('RGB', (8, 8)), 'What is this?')
    with pytest.raises(ModelUnavailable, match='download_models.py'):
        models.ocr(Image.new('RGB', (8, 8)), 'minicpm')

class _OffAwareModels(FakeModels):
    def ocr(self, image, backend=None):
        return {'text':'','backend':'off','error':None} if backend == 'off' else super().ocr(image, backend)

def test_ocr_off_reindex_skips_unchanged_files(env, tmp_path):
    cfg, db, _ = env; models = _OffAwareModels()
    folder = tmp_path/'pics'; folder.mkdir()
    Image.new('RGB', (30, 30), 'red').save(folder/'a.png')
    assert Ingestor(cfg, db, models).run(folder, ocr_backend='off')['indexed'] == 1
    assert Ingestor(cfg, db, models).run(folder, ocr_backend='off')['skipped'] == 1
    # Asking for OCR later still fills in the missing text.
    assert Ingestor(cfg, db, models).run(folder, ocr_backend='rapid')['updated'] == 1

def test_semantic_mode_shows_no_ocr_match_badge(env):
    _, db, models = env
    db.upsert(record(1, 'torch.OutOfMemoryError: CUDA out of memory. GPU 0 has 12.00 GiB'))
    semantic = Search(db, models).run('GPU memory problem', mode='semantic')['results'][0]
    hybrid = Search(db, models).run('GPU memory problem', mode='hybrid')['results'][0]
    assert semantic['exact_match'] is False and hybrid['exact_match'] is True
