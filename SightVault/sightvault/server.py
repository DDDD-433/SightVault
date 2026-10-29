from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import threading
import time
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from PIL import Image
from starlette.concurrency import run_in_threadpool
from .config import Config
from .database import Database
from .models import Models, ModelUnavailable
from .ingestion import Ingestor, scan_folder
from .search import Search, public_asset

class IndexRequest(BaseModel):
    folder: str = Field(default='', max_length=2048)
    demo: bool = False
    ocr: str = Field(default='auto', pattern='^(auto|minicpm|rapid|off)$')
    force: bool = False

class AnalysisRequest(BaseModel):
    asset_id: int
    question: str = Field(default='What is visible here, and what probably caused it?', min_length=1, max_length=2000)

def create_app(config=None, models=None):
    config = config or Config()
    config.prepare()
    db = Database(config.db_path)
    models = models or Models(config)
    search = Search(db, models)
    ingestor = Ingestor(config, db, models)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='indexer')
    jobs, job_lock = {}, threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        for job in jobs.values(): job['_cancel'].set()
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title='SightVault', docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.db, app.state.models = db, models
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','testserver'])

    @app.middleware('http')
    async def local_guard(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Cross-origin requests are not allowed.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cross-Origin-Resource-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/api/status')
    def status():
        return {**db.stats(), 'models':models.status(), 'embedding_model':'EmbeddingGemma 2 · 768 dimensions',
            'ocr_model':'MiniCPM-OCR 1.0 / RapidOCR', 'vision_model':'MiniCPM-V 4.7 1B',
            'instance_root':str(config.root),'local_only':True,'cloud_api_calls':0,'demo_folder':str(config.root/'samples'), 'ocr_default':config.ocr}

    @app.get('/api/search')
    def find(q:str='',mode:str='hybrid',sort:str='relevance',top_k:int=5,start:str|None=None,end:str|None=None,folder:str|None=None,similar_id:int|None=None):
        if mode not in ('hybrid','semantic','exact') or sort not in ('relevance','oldest','newest'):
            raise HTTPException(422,'Invalid search mode or sort order.')
        if len(q)>1000: raise HTTPException(422,'Please shorten the search query to 1,000 characters.')
        try: return search.run(q,mode,sort,min(max(top_k,1),100),start,end,folder,similar_id)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc

    @app.get('/api/assets/{asset_id}')
    def asset(asset_id:int):
        row = db.get(asset_id)
        if not row: raise HTTPException(404,'Image not found.')
        return {**public_asset(row), 'source_exists': Path(row['filepath']).is_file()}

    @app.get('/api/assets/{asset_id}/image')
    def image_file(asset_id:int,thumbnail:bool=False):
        row = db.get(asset_id)
        if not row: raise HTTPException(404,'Image not found.')
        file = config.data / ('thumbs' if thumbnail else 'images') / row['thumbnail_path' if thumbnail else 'image_path']
        if not file.is_file(): raise HTTPException(404,'Stored image is missing. Re-index the source folder.')
        return FileResponse(file, media_type='image/webp' if thumbnail else 'image/png')

    def job_public(job):
        return {k:v for k,v in job.items() if not k.startswith('_')}

    @app.post('/api/index')
    def index(body:IndexRequest):
        folder = str(config.root/'samples') if body.demo else body.folder.strip()
        if not folder: raise HTTPException(422,'Enter a folder path.')
        try: scan_folder(folder,config.max_files)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        with job_lock:
            if any(job['state']=='running' for job in jobs.values()):
                raise HTTPException(409,'An indexing job is already running.')
            job_id = uuid.uuid4().hex
            job = {'id':job_id,'state':'running','folder':folder,'processed':0,'total':0,'indexed':0,'skipped':0,'updated':0,'failed':0,'elapsed_s':0,'current':'Loading models…','_cancel':threading.Event()}
            jobs[job_id]=job
            while len(jobs)>20: jobs.pop(next(iter(jobs)))
        def progress(value):
            with job_lock: job.update(value)
        def work():
            try:
                report = ingestor.run(folder,progress,body.ocr,job['_cancel'],body.force)
                with job_lock: job.update(report,state='cancelled' if report['cancelled'] else 'complete')
            except Exception as exc:
                logging.exception('Indexing failed')
                with job_lock: job.update(state='error',error=str(exc))
        executor.submit(work)
        return {'job_id':job_id}

    @app.get('/api/jobs/{job_id}')
    def job_status(job_id:str):
        with job_lock:
            if job_id not in jobs: raise HTTPException(404,'Job not found.')
            return job_public(jobs[job_id])

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id:str):
        with job_lock:
            if job_id not in jobs: raise HTTPException(404,'Job not found.')
            jobs[job_id]['_cancel'].set()
        return {'message':'Will stop after the current image.'}

    @app.post('/api/analyze')
    async def analyze(body:AnalysisRequest):
        row = db.get(body.asset_id)
        if not row: raise HTTPException(404,'Image not found.')
        if not body.question.strip(): raise HTTPException(422,'Enter a question about this image.')
        started = time.perf_counter()
        def run():
            with Image.open(config.data/'images'/row['image_path']) as image:
                return models.analyze(image.convert('RGB'),body.question,row['ocr_text'])
        try: answer = await run_in_threadpool(run)
        except ModelUnavailable as exc: raise HTTPException(503,str(exc)) from exc
        except Exception as exc:
            logging.exception('Analysis failed')
            raise HTTPException(503,'Analysis could not finish. Search and OCR remain available. '+str(exc)[:300]) from exc
        return {'answer':answer,'evidence':public_asset(row),'model':'MiniCPM-V 4.7 1B','latency_ms':round((time.perf_counter()-started)*1000,1)}

    static = Path(__file__).resolve().parent/'web'
    app.mount('/static',StaticFiles(directory=static),name='static')

    @app.get('/')
    def home(): return FileResponse(static/'index.html')
    return app

