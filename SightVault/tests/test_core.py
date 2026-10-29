import os
from datetime import datetime,timezone,timedelta
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sightvault.config import Config
from sightvault.database import Database,encode_vector,decode_vector
from sightvault.ingestion import Ingestor
from sightvault.models import EMBEDDING_ID
from sightvault.search import Search,date_bounds
from sightvault.server import create_app

class FakeModels:
    """Deterministic unit-test adapter; never imported by the app."""
    def embed_image(self,image):return np.array([1,0,0],dtype=np.float32)
    def embed_query(self,query):return np.array([1,0,0],dtype=np.float32)
    def ocr(self,image,backend=None):return {'text':'CUDA out of memory\nERESOLVE error','backend':'unit test','error':None}
    def analyze(self,*args):return 'Unit test output'
    def status(self):return {}

@pytest.fixture
def env(tmp_path):
    cfg=Config(root=tmp_path,data=tmp_path/'data',models=tmp_path/'models')
    cfg.prepare();db=Database(cfg.db_path)
    return cfg,db,FakeModels()

def record(index,text='',stamp='2026-10-20T15:00:00+00:00',vector=None):
    return dict(filepath=f'C:/fixtures/{index}.png',filename=f'{index}.png',folder='C:/fixtures',sha256=str(index),size=100,
        mtime_ns=1,created_at=stamp,modified_at=stamp,timestamp=stamp,timestamp_source='File modified time',indexed_at=stamp,
        width=100,height=100,image_path='test.png',thumbnail_path='test.webp',ocr_text=text,ocr_backend='test',ocr_error=None,
        embedding=encode_vector(vector if vector is not None else [1,0,0]),embedding_model=EMBEDDING_ID,embedding_error=None)

def test_vector_roundtrip():
    assert np.allclose(decode_vector(encode_vector([3,4,0])),[.6,.8,0])

@pytest.mark.parametrize('value',[[0,0],[float('nan'),1],[float('inf'),0]])
def test_invalid_vectors_rejected(value):
    with pytest.raises(ValueError):encode_vector(value)

def test_database_updates_replace_fts(env):
    _,db,_=env
    r=record(1,'ERESOLVE');first=db.upsert(r)
    r['ocr_text']='ModuleNotFoundError';second=db.upsert(r)
    assert first==second and db.stats()['total']==1
    assert not db.fts(['ERESOLVE']) and first in db.fts(['ModuleNotFoundError'])

def test_ingestion_idempotence_and_change(env,tmp_path):
    cfg,db,models=env;folder=tmp_path/'pictures';folder.mkdir()
    file=folder/'sample.png';Image.new('RGB',(60,40),'red').save(file)
    ingestion=Ingestor(cfg,db,models)
    assert ingestion.run(folder)['indexed']==1
    assert ingestion.run(folder)['skipped']==1
    Image.new('RGB',(80,70),'blue').save(file)
    result=ingestion.run(folder)
    assert result['updated']==1 and db.stats()['total']==1
    assert db.all()[0]['width']==80
    assert (cfg.data/'thumbs'/db.all()[0]['thumbnail_path']).exists()

def test_duplicate_content_keeps_distinct_timestamps(env,tmp_path):
    cfg,db,models=env;folder=tmp_path/'pictures';folder.mkdir()
    for n in range(2):
        file=folder/f'{n}.png';Image.new('RGB',(40,40),'red').save(file);os.utime(file,(100000+n*86400,100000+n*86400))
    result=Ingestor(cfg,db,models).run(folder)
    assert result['duplicates']==1 and db.stats()['total']==2
    assert db.all()[0]['timestamp']!=db.all()[1]['timestamp']

def test_bad_files_do_not_block_good_files(env,tmp_path):
    cfg,db,models=env;folder=tmp_path/'pictures';folder.mkdir()
    (folder/'bad.png').write_bytes(b'not an image');(folder/'notes.txt').write_text('no')
    Image.new('RGB',(40,40),'red').save(folder/'good.png')
    result=Ingestor(cfg,db,models).run(folder)
    assert result['indexed']==1 and result['failed']==1 and result['unsupported']==1

def test_empty_and_invalid_folder(env,tmp_path):
    cfg,db,models=env;empty=tmp_path/'empty';empty.mkdir()
    assert Ingestor(cfg,db,models).run(empty)['total']==0
    with pytest.raises(ValueError):Ingestor(cfg,db,models).run(tmp_path/'missing')

def test_model_failure_keeps_ocr_search(env,tmp_path):
    cfg,db,models=env
    def missing(_):raise RuntimeError('Model is missing')
    models.embed_image=missing
    folder=tmp_path/'pictures';folder.mkdir();Image.new('RGB',(30,30),'red').save(folder/'a.png')
    report=Ingestor(cfg,db,models).run(folder)
    assert report['indexed']==1 and report['warnings']
    found=Search(db,models).run('ERESOLVE')
    assert found['results'][0]['exact_match'] and found['warning']

def test_exact_code_boost_beats_visual_similarity(env):
    _,db,models=env
    db.upsert(record(1,'Beautiful landscape',vector=[1,0,0]))
    db.upsert(record(2,'npm ERR! ERESOLVE unable to resolve dependency tree',vector=[.5,.866,0]))
    result=Search(db,models).run('Find ERESOLVE')
    assert result['results'][0]['filename']=='2.png'
    assert result['results'][0]['exact_match']

def test_semantic_mode_does_not_use_ocr_boost(env):
    _,db,models=env
    db.upsert(record(1,'not relevant text',vector=[1,0,0]))
    db.upsert(record(2,'ERESOLVE',vector=[0,1,0]))
    assert Search(db,models).run('ERESOLVE',mode='semantic')['results'][0]['filename']=='1.png'

def test_first_occurrence_searches_entire_corpus(env):
    _,db,models=env
    for n in range(20):db.upsert(record(n,'CUDA out of memory',stamp=f'2026-10-{10+n:02d}T15:00:00+00:00',vector=[.3+n*.02,.8,0]))
    found=Search(db,models).run('CUDA out of memory',sort='oldest',top_k=5)
    assert found['results'][0]['timestamp'].startswith('2026-10-10')
    assert found['total_matches']==20

def test_natural_temporal_query_uses_oldest_order(env):
    _,db,models=env
    db.upsert(record(1,'CUDA out of memory',stamp='2026-10-20T12:00:00+00:00'))
    db.upsert(record(2,'CUDA out of memory',stamp='2026-10-10T12:00:00+00:00'))
    found=Search(db,models).run('When did CUDA first occur?')
    assert found['sort']=='oldest' and found['results'][0]['filename']=='2.png'

def test_temporal_does_not_pick_unrelated_old_image(env):
    _,db,models=env
    db.upsert(record(1,'A generic error',stamp='2026-01-01T00:00:00+00:00'))
    db.upsert(record(2,'CUDA error: out of memory',stamp='2026-10-01T00:00:00+00:00'))
    found=Search(db,models).run('When did the CUDA error first occur?',sort='oldest')
    assert found['results'][0]['filename']=='2.png'

def test_empty_query_and_empty_db(env):
    _,db,models=env
    assert Search(db,models).run('anything')['results']==[]
    db.upsert(record(1));assert len(Search(db,models).run('')['results'])==1

def test_fts_input_cannot_inject_operators(env):
    _,db,models=env;db.upsert(record(1,'ERESOLVE'))
    result=Search(db,models).run('" OR * : NEAR(-) "; DROP TABLE assets;',mode='exact')
    assert db.stats()['total']==1
    assert isinstance(result['results'],list)

def test_date_filter_includes_whole_last_day(env):
    _,db,models=env
    db.upsert(record(1,stamp='2026-10-20T23:59:59+00:00'))
    db.upsert(record(2,stamp='2026-10-21T00:00:00+00:00'))
    assert len(Search(db,models).run('',start='2026-10-20',end='2026-10-20')['results'])==1

@pytest.mark.parametrize('start,end',[('not-a-date',None),('2026-10-22','2026-10-20')])
def test_invalid_dates(start,end):
    with pytest.raises(ValueError):date_bounds(start,end)

def test_similar_images_excludes_source(env):
    _,db,models=env
    one=db.upsert(record(1));two=db.upsert(record(2,vector=[.9,.1,0]))
    result=Search(db,models).run(similar_id=one)
    assert [r['id'] for r in result['results']]==[two]

def test_vectors_from_different_model_are_not_compared(env):
    _,db,models=env;r=record(1);r['embedding_model']='different';db.upsert(r)
    assert Search(db,models).run('photo',mode='semantic')['results']==[]

def test_api_security_and_validation(env):
    cfg,db,models=env
    with TestClient(create_app(cfg,models)) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/assets/999/image').status_code==404
        assert client.get('/api/search?mode=bogus').status_code==422
        assert client.get('/api/search?start=no').status_code==422
        assert client.post('/api/index',json={'folder':'no'}).status_code==422
        assert client.post('/api/index',headers={'Origin':'https://evil.example'},json={'folder':'no'}).status_code==403
        assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==400
        assert client.post('/api/analyze',json={'asset_id':999,'question':'hello'}).status_code==404
        assert client.get('/api/search').json()['results']==[]

def test_api_analyze_returns_attached_evidence(env,tmp_path):
    cfg,db,models=env;folder=tmp_path/'pics';folder.mkdir();Image.new('RGB',(40,40),'blue').save(folder/'test.png')
    Ingestor(cfg,db,models).run(folder)
    with TestClient(create_app(cfg,models)) as client:
        row=db.all()[0]
        r=client.post('/api/analyze',json={'asset_id':row['id'],'question':'What is visible?'})
        assert r.status_code==200 and r.json()['evidence']['id']==row['id']
        image=client.get(f"/api/assets/{row['id']}/image?thumbnail=true")
        assert image.status_code==200 and image.headers['content-type']=='image/webp'

