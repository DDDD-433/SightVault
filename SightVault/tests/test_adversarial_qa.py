"""Focused edge cases found during the second independent-QA preparation."""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sightvault.models import Models,ModelUnavailable
from sightvault.server import create_app
from sightvault.search import Search
from sightvault.config import Config
from test_core import env,record

@pytest.mark.parametrize('params',[
    {'end':'9999-12-31'},
    {'start':'0001-01-01T00:00:00+23:59'},
])
def test_out_of_range_dates_are_validation_errors(env,params):
    cfg,_,models=env
    with TestClient(create_app(cfg,models),raise_server_exceptions=False) as client:
        response=client.get('/api/search',params=params)
        assert response.status_code==422,response.text

def test_recovered_embedding_status_is_truthful(tmp_path):
    models=Models(Config(root=tmp_path,data=tmp_path/'data',models=tmp_path/'weights',device='cpu'))
    class Flaky:
        device='cpu'
        def __init__(self):self.calls=0
        def encode(self,*args,**kwargs):
            self.calls+=1
            if self.calls==1:raise RuntimeError('transient input failure')
            return np.ones((1,768),dtype=np.float32)
    models._embedding=Flaky();models.active['embedding']='cpu'
    with pytest.raises(ModelUnavailable):models.embed_query('first')
    assert not models.status()['embedding']['loaded']
    models.embed_query('second')
    assert models.status()['embedding']['loaded']
    assert models.status()['embedding']['device']=='cpu'
    assert models.status()['embedding']['error'] is None

def test_zero_embedding_is_rejected(tmp_path):
    models=Models(Config(root=tmp_path,data=tmp_path/'data',models=tmp_path/'weights',device='cpu'))
    class Zero:
        device='cpu'
        def encode(self,*args,**kwargs):return np.zeros((1,768),dtype=np.float32)
    models._embedding=Zero()
    with pytest.raises(ModelUnavailable,match='zero|invalid|norm'):models.embed_query('anything')

def test_similar_images_refuses_incompatible_source_model(env):
    _,db,models=env
    wrong=record(1);wrong['embedding_model']='different-model'
    source=db.upsert(wrong);db.upsert(record(2))
    with pytest.raises(ValueError,match='model|Re-index'):
        Search(db,models).run(similar_id=source)

@pytest.mark.parametrize('path',[
    '/api/assets/99999999999999999999999999999',
    '/api/assets/99999999999999999999999999999/image',
    '/api/search?similar_id=99999999999999999999999999999',
])
def test_oversized_sqlite_ids_do_not_crash_server(env,path):
    cfg,_,models=env
    with TestClient(create_app(cfg,models),raise_server_exceptions=False) as client:
        response=client.get(path)
        assert response.status_code in (404,422),response.text

def test_oversized_analysis_id_is_rejected(env):
    cfg,_,models=env
    with TestClient(create_app(cfg,models),raise_server_exceptions=False) as client:
        response=client.post('/api/analyze',json={'asset_id':10**30,'question':'Describe this.'})
        assert response.status_code in (404,422),response.text
