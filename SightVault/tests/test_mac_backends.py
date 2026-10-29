from sightvault.models import Models,ModelUnavailable
from sightvault.config import Config
import pytest

def model(tmp_path,device='auto'):
    return Models(Config(root=tmp_path,data=tmp_path/'data',models=tmp_path/'models',device=device))

def test_selection_requires_available_backend(tmp_path,monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    monkeypatch.setattr(torch.backends.mps,'is_available',lambda:False)
    assert model(tmp_path)._device()=='cpu'
    with pytest.raises(ModelUnavailable,match='MPS requested'):
        model(tmp_path,'mps')._device()

def test_auto_mps_and_embedding_precision(tmp_path,monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    monkeypatch.setattr(torch.backends.mps,'is_built',lambda:True)
    monkeypatch.setattr(torch.backends.mps,'is_available',lambda:True)
    m=model(tmp_path)
    assert m._device()=='mps'
    assert m._dtype('mps')==torch.float32

def test_mlx_is_never_reported_loaded_before_inference(tmp_path):
    m=model(tmp_path)
    path=m.config.models/'minicpm-ocr-mlx-4bit';path.mkdir(parents=True)
    (path/'config.json').write_text('{}')
    assert m.status()['ocr']['loaded'] is False
    assert m.status()['ocr']['device'] is None

def test_mps_operator_fallback_is_labeled(tmp_path,monkeypatch):
    import torch,numpy as np
    m=model(tmp_path)
    monkeypatch.setattr(m,'_clear_device_cache',lambda:None)
    class Embedder:
        device='mps'
        def encode(self,inputs,**kwargs):
            if self.device=='mps': raise NotImplementedError('operator not implemented for MPS')
            return np.ones((1,768),dtype=np.float32)
        def to(self,device): self.device=device;return self
        def float(self): return self
    v=m._encode(Embedder(),['query'],None)
    assert v.shape==(1,768)
    assert m.active['embedding']=='cpu (backend fallback)'
    assert 'MPS' in m.errors['embedding']

def test_arbitrary_embedding_error_is_not_silently_retried(tmp_path):
    m=model(tmp_path)
    class Broken:
        device='mps'
        def encode(self,*args,**kwargs): raise RuntimeError('invalid input')
    with pytest.raises(RuntimeError,match='invalid input'):
        m._encode(Broken(),['q'],None)
