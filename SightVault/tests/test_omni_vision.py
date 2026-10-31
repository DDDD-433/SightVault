import io
import sys
import types

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from sightvault.config import Config
from sightvault.llama_vision import (OmniRuntimeError, OmniVisionRuntime,
                                     offload_device_from_log, offload_evidence_from_line)
from sightvault.models import ModelUnavailable, Models
from sightvault.database import Database, encode_vector
from sightvault.server import create_app
from sightvault.search import Search
from test_core import record


def test_config_selects_opt_in_omni_and_bounded_lfm3_mode(monkeypatch):
    monkeypatch.setenv('SIGHTVAULT_VISION', 'omni')
    monkeypatch.delenv('SIGHTVAULT_LFM3_THINKING_TOKENS', raising=False)
    config = Config()
    assert config.vision == 'omni'
    assert config.lfm3_thinking_tokens == 0
    assert Models(config).vision_label == 'LFM3-Omni 2B'

    config = Config(vision='lfm3', lfm3_thinking_tokens=128)
    models = Models(config)
    assert models.reasoning_mode == 'bounded-thinking'
    assert models.thinking_token_budget == 128
    assert models.status()['vision']['reasoning_mode'] == 'bounded-thinking'
    assert models.status()['vision']['thinking_token_budget'] == 128


def test_config_rejects_unbounded_or_unknown_lfm3_thinking_budget():
    with pytest.raises(ValueError, match='must be 0 or 128'):
        Config(lfm3_thinking_tokens=256)


def test_omni_status_explains_missing_local_artifacts(tmp_path):
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='omni')
    vision = Models(config).status()['vision']
    assert vision['available'] is False
    assert 'prepare_omni_vision.py --download' in vision['availability_reason']
    assert vision['model_id'] == 'LiquidAI/LFM3-Omni-2B-GGUF'
    assert vision['reasoning_mode'] == 'off'
    assert vision['thinking_token_budget'] == 0
    assert vision['device'] is None
    assert vision['device_evidence'] == 'owned llama-server is not loaded; execution device unverified'


def test_omni_status_keeps_concise_offload_line_evidence(tmp_path):
    runtime = OmniVisionRuntime(Config(root=tmp_path, models=tmp_path/'models', vision='omni'))
    runtime._process = types.SimpleNamespace(poll=lambda: None)
    runtime._device_label, runtime._device_evidence = offload_evidence_from_line(
        'load_tensors: offloaded 32/33 layers to GPU')
    state = runtime.status()
    assert state['device'] == 'GPU (32/33 layers offloaded)'
    assert state['device_evidence'] == 'offloaded 32/33 layers to GPU'
    runtime._process = None


def test_omni_device_is_unverified_without_offload_evidence(tmp_path):
    runtime = OmniVisionRuntime(Config(root=tmp_path, models=tmp_path/'models', vision='omni'))
    runtime._process = types.SimpleNamespace(poll=lambda: None)
    state = runtime.status()
    assert state['device'] == 'unverified (no GPU offload evidence)'
    assert state['device_evidence'] == (
        'no layer-offload line observed in the owned server startup log; execution device unverified')
    runtime._process = None


def test_omni_device_reports_cpu_only_when_explicitly_configured(tmp_path):
    runtime = OmniVisionRuntime(Config(root=tmp_path, models=tmp_path/'models', vision='omni', device='cpu'))
    runtime._process = types.SimpleNamespace(poll=lambda: None)
    state = runtime.status()
    assert state['device'] == 'CPU (configured; --gpu-layers 0)'
    assert state['device_evidence'] == 'SIGHTVAULT_DEVICE=cpu; owned server command uses --gpu-layers 0'
    runtime._process = None


def test_omni_model_failure_has_no_minicpm_fallback_and_search_components_survive(tmp_path, monkeypatch):
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='omni')
    models = Models(config)
    models._embedding = object()
    monkeypatch.setattr(models, '_load_generative', lambda *_: pytest.fail('must not fall back to MiniCPM'))
    with pytest.raises(OmniRuntimeError, match='pinned Omni model provenance'):
        models._generate('vision', Image.new('RGB', (2, 2)), 'inspect', 384)
    assert models._embedding is not None
    assert models._vlm is None


def test_omni_route_releases_native_models_but_keeps_embedding(tmp_path, monkeypatch):
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='omni'))
    embedding = object()
    models._embedding = embedding
    models._ocr = (object(), object())
    models._vlm = (object(), object())
    models.active.update(ocr='cpu', vision='cpu')

    class Runner:
        device_label = 'unverified (no GPU offload evidence)'
        def analyze(self, image, prompt, max_tokens):
            assert prompt == 'same evidence prompt'
            assert max_tokens == 384
            return 'local answer'

    runner = Runner()
    monkeypatch.setattr(models, '_omni_runtime', lambda: runner)
    assert models._generate('vision', Image.new('RGB', (2, 2)), 'same evidence prompt', 384) == 'local answer'
    assert models._ocr is None and models._vlm is None
    assert models._embedding is embedding
    assert models.active['vision'] == 'unverified (no GPU offload evidence)'


def test_omni_analysis_failure_does_not_disable_semantic_search(tmp_path, monkeypatch):
    import numpy as np
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='omni', device='cpu')
    models = Models(config)

    class Embedder:
        device = 'cpu'
        def encode(self, inputs, **kwargs):
            vectors = np.zeros((len(inputs), 768), dtype=np.float32)
            vectors[:, 0] = 1.0
            return vectors

    models._embedding = Embedder()
    monkeypatch.setattr(models, '_generate', lambda *args: (_ for _ in ()).throw(
        OmniRuntimeError('simulated local runtime failure')))
    with pytest.raises(ModelUnavailable, match='LFM3-Omni 2B could not analyze'):
        models.analyze(Image.new('RGB', (4, 4)), 'What is visible?')

    db = Database(config.db_path)
    item = record(1, 'blue sky')
    item['embedding'] = encode_vector([1.0] + [0.0] * 767)
    db.upsert(item)
    assert Search(db, models).run('blue sky', mode='semantic')['results'][0]['filename'] == '1.png'


def test_ocr_request_stops_owned_omni_process_before_loading_ocr(tmp_path, monkeypatch):
    models = Models(Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='omni'))

    class Runner:
        closed = False
        def close(self):
            self.closed = True

    runner = Runner()
    models._omni = runner
    models.active['vision'] = 'CPU'
    monkeypatch.setattr(models, '_mlx_path', lambda _kind: tmp_path)
    monkeypatch.setattr(models, '_generate_mlx', lambda kind, image, prompt, cap: 'ocr result')
    assert models._generate('ocr', object(), 'transcribe', 768) == 'ocr result'
    assert runner.closed
    assert 'vision' not in models.active


def test_owned_server_uses_local_transport_and_image_data_url(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setenv('OPENAI_API_KEY', 'do-not-forward')
    monkeypatch.setenv('HTTPS_PROXY', 'http://must-not-proxy.invalid')

    class FakeResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {'choices': [{'message': {'content': 'answer'}}]}

    class FakeClient:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.closed = False
            self.request = None
        def post(self, url, *, json, timeout):
            self.request = (url, json, timeout)
            return FakeResponse()
        def close(self):
            self.closed = True

    client = FakeClient()
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: (client.options.update(kwargs) or client))

    class FakeProcess:
        stdout = io.StringIO('')
        returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = 0
        def wait(self, timeout=None):
            return self.returncode
        def kill(self):
            self.returncode = -9

    process = FakeProcess()
    commands = []
    monkeypatch.setattr('sightvault.llama_vision.subprocess.Popen',
                        lambda command, **kwargs: (commands.append((command, kwargs)) or process))
    runtime = OmniVisionRuntime(Config(root=tmp_path, models=tmp_path/'models', data=tmp_path/'data', vision='omni'))
    monkeypatch.setattr(runtime, '_verify_artifacts', lambda: {})
    monkeypatch.setattr(runtime, '_new_port', lambda: 43210)
    def ready(child, http, alias, port, timeout_s=240):
        assert child is process and http is client and port == 43210
        runtime._projector_loaded = True
    monkeypatch.setattr(runtime, '_wait_ready', ready)

    answer = runtime.analyze(Image.new('RGB', (4, 4), 'red'), 'What is visible?')
    assert answer == 'answer'
    assert client.options['trust_env'] is False
    url, payload, timeout = client.request
    assert url == 'http://127.0.0.1:43210/v1/chat/completions'
    assert timeout == 180
    assert payload['max_tokens'] == 384
    assert payload['temperature'] == 0 and payload['repeat_penalty'] == 1.0
    assert payload['chat_template_kwargs'] == {'enable_thinking': False}
    assert payload['messages'][0]['content'] == 'Respond in text.'
    image_part = payload['messages'][1]['content'][1]['image_url']['url']
    assert image_part.startswith('data:image/png;base64,')
    assert 'http://' not in image_part and 'https://' not in image_part
    command, options = commands[0]
    assert '--host' in command and command[command.index('--host') + 1] == '127.0.0.1'
    assert '--offline' in command
    assert options['env']['HF_HUB_OFFLINE'] == '1'
    assert 'OPENAI_API_KEY' not in options['env']
    assert 'HTTPS_PROXY' not in options['env']
    runtime.close()
    assert client.closed and process.returncode == 0


def test_props_identity_mismatch_is_rejected_before_image_request(tmp_path):
    runtime = OmniVisionRuntime(Config(root=tmp_path, models=tmp_path/'models', vision='omni'))

    class Response:
        def __init__(self, body):
            self.status_code = 200
            self.body = body
        def raise_for_status(self):
            return None
        def json(self):
            return self.body

    class Client:
        def get(self, url, **kwargs):
            if url.endswith('/health'):
                return Response({})
            return Response({'model_path': str(tmp_path/'different.gguf'),
                             'model_alias': 'test-alias', 'modalities': {'vision': True}})

    class Process:
        returncode = None
        def poll(self):
            return self.returncode

    with pytest.raises(OmniRuntimeError, match='model path does not match'):
        runtime._wait_ready(Process(), Client(), 'test-alias', 43210, timeout_s=0.1)


def test_startup_identity_failure_terminates_only_owned_child(tmp_path, monkeypatch):
    import httpx

    class Client:
        def close(self):
            pass
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: Client())

    class Process:
        stdout = io.StringIO('')
        returncode = None
        terminated = False
        def poll(self):
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = 0
        def wait(self, timeout=None):
            return self.returncode
        def kill(self):
            self.returncode = -9
    process = Process()
    monkeypatch.setattr('sightvault.llama_vision.subprocess.Popen', lambda *args, **kwargs: process)
    runtime = OmniVisionRuntime(Config(root=tmp_path, models=tmp_path/'models', vision='omni'))
    monkeypatch.setattr(runtime, '_verify_artifacts', lambda: {})
    monkeypatch.setattr(runtime, '_new_port', lambda: 43210)
    monkeypatch.setattr(runtime, '_wait_ready', lambda *args, **kwargs: (_ for _ in ()).throw(
        OmniRuntimeError('simulated /props identity mismatch')))
    with pytest.raises(OmniRuntimeError, match='identity mismatch'):
        runtime._start()
    assert process.terminated
    assert runtime._process is None
    assert runtime.port is None


def test_device_label_requires_explicit_runtime_offload_log():
    assert offload_device_from_log(['server ready, using GPU layers=99']) is None
    label = offload_device_from_log(['llama.cpp: offloaded 32/33 layers to Metal'])
    assert label == 'Apple Metal GPU (32/33 layers offloaded)'
    label, evidence = offload_evidence_from_line(
        'load_tensors: offloaded 32/33 layers to GPU')
    assert label == 'GPU (32/33 layers offloaded)'
    assert evidence == 'offloaded 32/33 layers to GPU'


def test_device_evidence_accepts_pinned_llama_cpp_repeating_layer_log():
    label, evidence = offload_evidence_from_line(
        'load_tensors: offloading 32 repeating layers to GPU')
    assert label == 'GPU (32 repeating layers offloaded)'
    assert evidence == 'offloading 32 repeating layers to GPU'
    assert offload_evidence_from_line('llama_model_load: offloading 0 repeating layers to GPU') is None


def test_status_and_analysis_api_attribute_omni_and_close_models(tmp_path):
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models', vision='omni')
    config.prepare()
    db = Database(config.db_path)
    row = record(1, 'screenshot')
    Image.new('RGB', (8, 8), 'blue').save(config.data/'images'/row['image_path'])
    Image.new('RGB', (8, 8), 'blue').save(config.data/'thumbs'/row['thumbnail_path'], format='WEBP')
    db.upsert(row)

    class FakeModels:
        vision_label = 'LFM3-Omni 2B'
        last_vision_generation = {'reasoning_mode': 'off', 'final_answer_token_cap': 384}
        closed = False
        def status(self):
            vision = {'name': self.vision_label, 'model_id': 'LiquidAI/LFM3-Omni-2B-GGUF',
                      'model_revision': '3d29af2ad166f8299c2d820937e47dc0add20ffc',
                      'model_quantization': 'Q4_K_M', 'backend': 'omni', 'runtime': 'upstream llama.cpp',
                      'projector_id': 'mmproj-LFM3-Omni-2B-Q4_K_M.gguf',
                      'projector_quantization': 'Q4_K_M',
                      'runtime_commit': 'e0f142d5ddc1461ce721644d92af33db23545105',
                      'device_evidence': 'offloaded 32/33 layers to GPU',
                      'precision': 'GGUF Q4_K_M', 'reasoning_mode': 'off', 'thinking_token_budget': 0,
                      'device': 'unverified (no GPU offload evidence)'}
            return {'vision': vision}
        def analyze(self, image, question, ocr_text):
            return 'blue image'
        def close(self):
            self.closed = True

    models = FakeModels()
    with TestClient(create_app(config, models)) as client:
        status = client.get('/api/status').json()
        assert status['vision_backend'] == 'omni'
        assert status['vision_model_id'] == 'LiquidAI/LFM3-Omni-2B-GGUF'
        assert status['vision_model_revision'] == '3d29af2ad166f8299c2d820937e47dc0add20ffc'
        assert status['vision_reasoning_mode'] == 'off'
        response = client.post('/api/analyze', json={'asset_id': 1, 'question': 'What color?'})
        assert response.status_code == 200
        payload = response.json()
        assert payload['backend'] == 'omni'
        assert payload['model_quantization'] == 'Q4_K_M'
        assert payload['projector_id'] == 'mmproj-LFM3-Omni-2B-Q4_K_M.gguf'
        assert payload['projector_quantization'] == 'Q4_K_M'
        assert payload['runtime_commit'] == 'e0f142d5ddc1461ce721644d92af33db23545105'
        assert payload['device_evidence'] == 'offloaded 32/33 layers to GPU'
        assert payload['reasoning_mode'] == 'off'
        assert payload['generation']['final_answer_token_cap'] == 384
    assert models.closed


class _StreamItem:
    def __init__(self, token):
        self.token = token
        self.finish_reason = None
    @property
    def text(self):
        raise AssertionError('hidden or mixed GenerationResult.text must never be inspected')


class _ClosableStream:
    def __init__(self, items):
        self.items = items
        self.closed = False
    def __iter__(self):
        yield from self.items
    def close(self):
        self.closed = True


def _stub_mlx_vlm(monkeypatch, stream, encoded_prompt=(90, 10)):
    mlx_vlm = types.ModuleType('mlx_vlm')
    mlx_prompt = types.ModuleType('mlx_vlm.prompt_utils')
    mlx_utils = types.ModuleType('mlx_vlm.utils')
    mlx_vlm.stream_generate = lambda *args, **kwargs: stream
    mlx_prompt.apply_chat_template = lambda *args, **kwargs: 'formatted assistant prefix'
    class Criteria:
        def __init__(self, eos, tokenizer=None):
            self.eos_token_ids = [99]
    mlx_utils.StoppingCriteria = Criteria
    monkeypatch.setitem(sys.modules, 'mlx_vlm', mlx_vlm)
    monkeypatch.setitem(sys.modules, 'mlx_vlm.prompt_utils', mlx_prompt)
    monkeypatch.setitem(sys.modules, 'mlx_vlm.utils', mlx_utils)

    class Tokenizer:
        eos_token_id = 99
        def encode(self, value, add_special_tokens=False):
            if value == '<think>':
                return [10]
            if value == '</think>':
                return [11]
            return list(encoded_prompt)
        def decode(self, ids, skip_special_tokens=True):
            return f'{len(ids)} final tokens'
    class Processor:
        tokenizer = Tokenizer()
    class Model:
        config = types.SimpleNamespace(eos_token_id=99)
    return Model(), Processor()


def test_lfm3_thinking_parser_honors_separate_final_384_cap(monkeypatch):
    from sightvault.lfm3_thinking import generate_bounded_thinking

    stream = _ClosableStream([_StreamItem(token) for token in [1, 2, 11] + [22] * 390])
    model, processor = _stub_mlx_vlm(monkeypatch, stream)
    answer, metadata = generate_bounded_thinking(
        model, processor, Image.new('RGB', (2, 2)), 'same bounded prompt')
    assert answer == '384 final tokens'
    assert metadata['thinking_tokens_observed'] == 2
    assert metadata['final_answer_tokens'] == 384
    assert metadata['truncated_final_answer'] is True
    assert metadata['finish_reason'] == 'final_answer_cap'
    assert metadata['hidden_reasoning_read_or_saved'] is False
    assert stream.closed


def test_lfm3_thinking_template_must_end_in_open_assistant_think_block(monkeypatch):
    from sightvault.lfm3_thinking import ThinkingOutputError, generate_bounded_thinking

    stream = _ClosableStream([])
    model, processor = _stub_mlx_vlm(monkeypatch, stream, encoded_prompt=(10, 5, 11))
    with pytest.raises(ThinkingOutputError, match='does not end with an open assistant'):
        generate_bounded_thinking(model, processor, Image.new('RGB', (2, 2)), 'prompt')


def test_lfm3_incomplete_thinking_stream_is_reported_as_analysis_failure(tmp_path, monkeypatch):
    import types
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models',
                    vision='lfm3', lfm3_thinking_tokens=128)
    models = Models(config)
    stream = _ClosableStream([_StreamItem(1)])
    model, processor = _stub_mlx_vlm(monkeypatch, stream)
    fake_vlm = sys.modules['mlx_vlm']
    fake_vlm.load = lambda *args, **kwargs: (model, processor)
    fake_vlm.generate = lambda *args, **kwargs: pytest.fail('bounded mode must use the tested stream parser')
    fake_mlx = types.ModuleType('mlx')
    fake_mlx.__path__ = []
    fake_mx = types.ModuleType('mlx.core')
    fake_mx.clear_cache = lambda: None
    fake_mlx.core = fake_mx
    monkeypatch.setitem(sys.modules, 'mlx', fake_mlx)
    monkeypatch.setitem(sys.modules, 'mlx.core', fake_mx)
    monkeypatch.setattr(models, '_mlx_platform_supported', lambda: True)
    monkeypatch.setattr(models, '_lfm3_adapter_available', lambda: True)
    monkeypatch.setattr(models, '_lfm3_runtime_matches', lambda: True)
    monkeypatch.setattr(models, '_lfm3_missing_files', lambda _path: [])
    monkeypatch.setattr(models, '_lfm3_source_matches', lambda _path: True)
    monkeypatch.setattr(models, '_mlx_path', lambda _kind: tmp_path)

    with pytest.raises(ModelUnavailable, match='could not analyze.*no usable final answer'):
        models.analyze(Image.new('RGB', (2, 2)), 'What is visible?')
    assert stream.closed
    assert models.last_vision_generation['finish_reason'] == 'incomplete_thinking_block'
    assert models.last_vision_generation['hidden_reasoning_read_or_saved'] is False
    assert models._vlm is None


def test_lfm3_thinking_stream_is_closed_on_generation_error(monkeypatch):
    from sightvault.lfm3_thinking import generate_bounded_thinking

    class BrokenStream(_ClosableStream):
        def __iter__(self):
            yield _StreamItem(1)
            raise RuntimeError('simulated stream failure')
    stream = BrokenStream([])
    model, processor = _stub_mlx_vlm(monkeypatch, stream)
    with pytest.raises(RuntimeError, match='simulated stream failure'):
        generate_bounded_thinking(model, processor, Image.new('RGB', (2, 2)), 'prompt')
    assert stream.closed
