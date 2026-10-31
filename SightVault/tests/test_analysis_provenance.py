import threading

from fastapi.testclient import TestClient
from PIL import Image

from sightvault.config import Config
from sightvault.database import Database
from sightvault.server import create_app
from test_core import record


def test_analysis_response_uses_metadata_snapshot_from_its_generation(tmp_path, monkeypatch):
    config = Config(root=tmp_path, data=tmp_path/'data', models=tmp_path/'models')
    config.prepare()
    db = Database(config.db_path)
    row = record(1, 'fixture OCR')
    Image.new('RGB', (12, 8), 'navy').save(config.data/'images'/row['image_path'])
    db.upsert(row)

    class FakeModels:
        def __init__(self):
            self.lock = threading.RLock()
            self.last_vision_generation = {}
            self.vision = {}
            self.status_calls = 0

        def analyze(self, image, question, ocr_text):
            assert question == 'What is shown?'
            assert ocr_text == 'fixture OCR'
            self.last_vision_generation = {
                'reasoning_mode': 'bounded-thinking',
                'thinking_token_budget': 128,
                'final_answer_token_cap': 384,
            }
            self.vision = {
                'name': 'LFM3-VL-2B', 'backend': 'lfm3',
                'model_revision': 'revision-for-this-answer',
                'device': 'mlx (Metal GPU)',
                'device_evidence': 'active generation device',
                'reasoning_mode': 'bounded-thinking',
                'thinking_token_budget': 128,
            }
            return 'answer from first generation'

        def status(self):
            self.status_calls += 1
            return {'vision': self.vision}

    models = FakeModels()
    app = create_app(config, models)

    import sightvault.server as server_module
    run_in_threadpool = server_module.run_in_threadpool

    async def mutate_after_worker_returns(function, *args, **kwargs):
        result = await run_in_threadpool(function, *args, **kwargs)
        # Simulate the next queued generation winning the lock before the route
        # formats its response. The current response must retain its own snapshot.
        with models.lock:
            models.last_vision_generation = {
                'reasoning_mode': 'off', 'thinking_token_budget': 0,
                'final_answer_token_cap': 384,
            }
            models.vision = {
                'name': 'MiniCPM-V 4.7 1B', 'backend': 'minicpm',
                'model_revision': 'revision-for-next-answer',
                'device': 'cpu', 'device_evidence': 'next generation',
                'reasoning_mode': 'off', 'thinking_token_budget': 0,
            }
        return result

    monkeypatch.setattr(server_module, 'run_in_threadpool', mutate_after_worker_returns)
    with TestClient(app) as client:
        response = client.post('/api/analyze', json={
            'asset_id': 1, 'question': 'What is shown?',
        })

    assert response.status_code == 200
    payload = response.json()
    assert payload['answer'] == 'answer from first generation'
    assert payload['backend'] == 'lfm3'
    assert payload['model'] == 'LFM3-VL-2B'
    assert payload['model_revision'] == 'revision-for-this-answer'
    assert payload['device_evidence'] == 'active generation device'
    assert payload['generation'] == {
        'reasoning_mode': 'bounded-thinking',
        'thinking_token_budget': 128,
        'final_answer_token_cap': 384,
    }
    assert models.status_calls == 1
