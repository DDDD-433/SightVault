import json
import pytest
import numpy as np
from fastapi.testclient import TestClient

from sightvault.config import Config
from sightvault.database import Database, encode_vector
from sightvault.models import LFM3_REQUIRED_FILES, LFM3_RUNTIME_COMMIT, ModelUnavailable, Models
from sightvault.search import Search
from sightvault.server import create_app
from test_core import FakeModels, record


def test_vision_config_defaults_to_minicpm(monkeypatch):
    monkeypatch.delenv('SIGHTVAULT_VISION', raising=False)
    assert Config().vision == 'minicpm'


@pytest.mark.parametrize('backend', ['minicpm', 'lfm3'])
def test_vision_config_accepts_supported_backends(monkeypatch, backend):
    monkeypatch.setenv('SIGHTVAULT_VISION', backend)
    assert Config().vision == backend


def test_vision_config_rejects_unknown_backend():
    with pytest.raises(ValueError, match="SIGHTVAULT_VISION must be 'minicpm', 'lfm3' or 'omni'"):
        Config(vision='unknown')


def _force_mlx_platform(monkeypatch):
    import platform
    monkeypatch.setattr(platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(platform, 'machine', lambda: 'arm64')


def test_lfm3_status_reports_configured_identity_and_missing_model(tmp_path, monkeypatch):
    _force_mlx_platform(monkeypatch)
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3'))
    monkeypatch.setattr(models, '_lfm3_adapter_available', lambda: True)
    monkeypatch.setattr(models, '_lfm3_runtime_matches', lambda: True)
    vision = models.status()['vision']
    assert vision['name'] == 'LFM3-VL-2B'
    assert vision['model_id'] == 'LiquidAI/LFM3-VL-2B-MLX-4bit'
    assert vision['backend'] == 'lfm3'
    assert vision['runtime'] == 'MLX-VLM / MLX'
    assert 'QAD affine 4-bit' in vision['precision']
    assert vision['available'] is False and vision['loaded'] is False
    assert '--vision lfm3' in vision['availability_reason']


def test_lfm3_status_explains_missing_runtime_adapter(tmp_path, monkeypatch):
    _force_mlx_platform(monkeypatch)
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3'))
    monkeypatch.setattr(models, '_lfm3_adapter_available', lambda: False)
    vision = models.status()['vision']
    assert vision['available'] is False
    assert 'requirements-lfm3.lock.txt' in vision['availability_reason']


def test_lfm3_status_rejects_unpinned_runtime(tmp_path, monkeypatch):
    _force_mlx_platform(monkeypatch)
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3'))
    monkeypatch.setattr(models, '_lfm3_adapter_available', lambda: True)
    monkeypatch.setattr(models, '_lfm3_runtime_matches', lambda: False)
    vision = models.status()['vision']
    assert vision['available'] is False
    assert 'commit pinned in requirements-lfm3.lock.txt' in vision['availability_reason']


def test_lfm3_routes_to_local_mlx_and_never_minicpm_fallback(tmp_path, monkeypatch):
    _force_mlx_platform(monkeypatch)
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3')
    checkpoint = config.models/'lfm3-vl-2b-mlx-4bit'
    checkpoint.mkdir(parents=True)
    for name in LFM3_REQUIRED_FILES:
        target = checkpoint/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'local weights marker' if name == 'model.safetensors' else b'{}')
    (checkpoint/'source.json').write_text(json.dumps({'repo':'LiquidAI/LFM3-VL-2B-MLX-4bit',
        'revision':'4146d7fa689833cfa7a06c474e748350b458f41e',
        'runtime_commit':LFM3_RUNTIME_COMMIT}))
    models = Models(config)
    monkeypatch.setattr(models, '_lfm3_adapter_available', lambda: True)
    monkeypatch.setattr(models, '_lfm3_runtime_matches', lambda: True)
    called = []

    def local_generation(kind, image, prompt, max_tokens):
        called.append((kind, prompt, max_tokens))
        return 'LFM3 output'

    monkeypatch.setattr(models, '_generate_mlx', local_generation)
    monkeypatch.setattr(models, '_load_generative', lambda *_: pytest.fail('must not fall through to MiniCPM'))
    assert models._generate('vision', object(), 'prompt', 96) == 'LFM3 output'
    assert called == [('vision', 'prompt', 96)]
    assert models.status()['vision']['available'] is True


def test_lfm3_thinking_flag_does_not_change_minicpm_generation(tmp_path, monkeypatch):
    import sys
    import types

    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models',
                           vision='minicpm', lfm3_thinking_tokens=128))
    fake_mlx = types.ModuleType('mlx')
    fake_mlx.__path__ = []
    fake_mx = types.ModuleType('mlx.core')
    fake_mx.clear_cache = lambda: None
    fake_mlx.core = fake_mx
    fake_vlm = types.ModuleType('mlx_vlm')
    fake_prompt_utils = types.ModuleType('mlx_vlm.prompt_utils')
    calls = []

    class Model:
        config = object()
    class Processor:
        pass

    fake_vlm.load = lambda *args, **kwargs: (Model(), Processor())
    def apply_template(processor, config, prompt, **kwargs):
        calls.append(('template', kwargs))
        return 'formatted'
    def generate(model, processor, formatted, **kwargs):
        calls.append(('generate', kwargs))
        return types.SimpleNamespace(text='MiniCPM output')
    fake_vlm.generate = generate
    fake_prompt_utils.apply_chat_template = apply_template
    monkeypatch.setitem(sys.modules, 'mlx', fake_mlx)
    monkeypatch.setitem(sys.modules, 'mlx.core', fake_mx)
    monkeypatch.setitem(sys.modules, 'mlx_vlm', fake_vlm)
    monkeypatch.setitem(sys.modules, 'mlx_vlm.prompt_utils', fake_prompt_utils)
    monkeypatch.setattr(models, '_mlx_path', lambda _kind: tmp_path)

    assert models._generate('vision', object(), 'prompt', 384) == 'MiniCPM output'
    assert calls[0] == ('template', {'num_images': 1, 'enable_thinking': False})
    assert calls[1][0] == 'generate'
    assert models.reasoning_mode == 'off'


def test_lfm3_missing_model_has_actionable_error_without_fallback(tmp_path, monkeypatch):
    _force_mlx_platform(monkeypatch)
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3'))
    monkeypatch.setattr(models, '_lfm3_adapter_available', lambda: True)
    monkeypatch.setattr(models, '_lfm3_runtime_matches', lambda: True)
    monkeypatch.setattr(models, '_load_generative', lambda *_: pytest.fail('must not fall through to MiniCPM'))
    with pytest.raises(ModelUnavailable, match='LFM3-VL snapshot is incomplete'):
        models._generate('vision', object(), 'prompt', 96)


def test_lfm3_incomplete_snapshot_lists_processor_and_tokenizer_files(tmp_path, monkeypatch):
    _force_mlx_platform(monkeypatch)
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3')
    checkpoint = config.models/'lfm3-vl-2b-mlx-4bit'
    checkpoint.mkdir(parents=True)
    (checkpoint/'config.json').write_text('{}')
    (checkpoint/'model.safetensors').write_bytes(b'local weights marker')
    models = Models(config)
    monkeypatch.setattr(models, '_lfm3_adapter_available', lambda: True)
    monkeypatch.setattr(models, '_lfm3_runtime_matches', lambda: True)
    with pytest.raises(ModelUnavailable, match='tokenizer.json'):
        models._generate('vision', object(), 'prompt', 96)


def test_lfm3_requires_apple_silicon_and_mlx_device(tmp_path, monkeypatch):
    _force_mlx_platform(monkeypatch)
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3', device='cpu'))
    with pytest.raises(ModelUnavailable, match='requires Apple Silicon'):
        models._generate('vision', object(), 'prompt', 96)


def test_mlx_failure_releases_model_slot_and_traceback_references(tmp_path, monkeypatch):
    import weakref
    import mlx.core as mx
    import mlx_vlm
    import mlx_vlm.prompt_utils

    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models'))
    models._ocr = (object(), object())
    models.active['ocr'] = 'mlx (Metal GPU)'
    references = []

    class FakeModel:
        config = {}

    class FakeProcessor:
        pass

    def fake_load(*args, **kwargs):
        model, processor = FakeModel(), FakeProcessor()
        references.extend((weakref.ref(model), weakref.ref(processor)))
        return model, processor

    monkeypatch.setattr(mlx_vlm, 'load', fake_load)
    monkeypatch.setattr(mlx_vlm, 'generate', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('simulated Metal out of memory')))
    monkeypatch.setattr(mlx_vlm.prompt_utils, 'apply_chat_template', lambda *args, **kwargs: 'formatted')
    live_references_at_cache_clear = []
    def clear_cache():
        live_references_at_cache_clear.append(sum(reference() is not None for reference in references))
    monkeypatch.setattr(mx, 'clear_cache', clear_cache)
    monkeypatch.setattr(models, '_mlx_path', lambda _kind: tmp_path)

    with pytest.raises(RuntimeError, match='simulated Metal out of memory'):
        models._generate_mlx('vision', object(), 'prompt', 96)
    assert models._ocr is None and models._vlm is None
    assert 'ocr' not in models.active and 'vision' not in models.active
    assert live_references_at_cache_clear == [0, 0]
    assert all(reference() is None for reference in references)


def test_vision_failure_does_not_disable_semantic_search(tmp_path, monkeypatch):
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3')
    models = Models(config)

    class Embedder:
        device = 'cpu'

        def encode(self, inputs, **kwargs):
            vector = np.zeros((len(inputs), 768), dtype=np.float32)
            vector[:, 0] = 1.0
            return vector

    models._embedding = Embedder()
    monkeypatch.setattr(models, '_generate', lambda *args: (_ for _ in ()).throw(RuntimeError('simulated Metal out of memory')))
    from PIL import Image
    with pytest.raises(ModelUnavailable, match='LFM3-VL-2B could not analyze'):
        models.analyze(Image.new('RGB', (8, 8)), 'What is visible?')

    db = Database(config.db_path)
    item = record(1, 'blue sky')
    item['embedding'] = encode_vector([1.0] + [0.0] * 767)
    db.upsert(item)
    result = Search(db, models).run('blue sky', mode='semantic')
    assert result['results'][0]['filename'] == '1.png'


@pytest.mark.parametrize('output', ['', '<think>the hidden thought only', '<think>hidden</think>'])
def test_analysis_rejects_empty_or_thinking_only_output(tmp_path, monkeypatch, output):
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3'))
    monkeypatch.setattr(models, '_generate', lambda *args: output)
    from PIL import Image
    with pytest.raises(ModelUnavailable, match='no final answer'):
        models.analyze(Image.new('RGB', (8, 8)), 'What is visible?')


def test_status_and_analysis_api_report_selected_vision_backend(tmp_path):
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='lfm3')
    config.prepare()
    db = Database(config.db_path)
    class Lfm3Fake(FakeModels):
        vision_label = 'LFM3-VL-2B'

        def status(self):
            return {'vision':{'name':self.vision_label,'backend':'lfm3','runtime':'MLX-VLM / MLX',
                              'precision':'QAD affine 4-bit, group 32; token embeddings 8-bit'}}

    models = Lfm3Fake()
    row = record(1, 'example')
    (config.data/'images').mkdir(parents=True, exist_ok=True)
    (config.data/'thumbs').mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.new('RGB', (16, 16), 'blue').save(config.data/'images'/row['image_path'])
    Image.new('RGB', (16, 16), 'blue').save(config.data/'thumbs'/row['thumbnail_path'], format='WEBP')
    db.upsert(row)

    with TestClient(create_app(config, models)) as client:
        status = client.get('/api/status').json()
        assert status['vision_model'] == 'LFM3-VL-2B'
        assert status['vision_backend'] == 'lfm3'
        assert status['models']['vision']['runtime'] == 'MLX-VLM / MLX'
        response = client.post('/api/analyze', json={'asset_id':1,'question':'What is visible?'})
        assert response.status_code == 200
        assert response.json()['model'] == 'LFM3-VL-2B'
        assert response.json()['backend'] == 'lfm3'
