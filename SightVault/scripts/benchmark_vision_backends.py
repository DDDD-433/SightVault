"""Run a serialized, same-image/same-question comparison of local vision backends.

The parent process starts one backend worker at a time so Metal never has both
generative models resident together. The worker reads the isolated demo database
through a read-only SQLite connection and records final answers only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent

def repo_relative(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return '<external>/' + Path(path).name

CASES = [
    ('terminal_cuda_01.png', 'terminal', 'What error is displayed, and what does the visible text suggest about the failure?'),
    ('terminal_eresolve_01.png', 'terminal', 'Which package versions appear to conflict, and what is the immediate install error?'),
    ('terminal_module_01.png', 'terminal', 'What missing dependency is reported, and what command led to it?'),
    ('terminal_database_01.png', 'terminal', 'Which database error is visible, and what condition does it indicate?'),
    ('terminal_success_01.png', 'terminal', 'What completed task and visible validation result are shown? Use only visible evidence.'),
    ('lecture_normalization_01.png', 'slide', 'Summarize the normal forms named and the rule given for each.'),
    ('lecture_joins_01.png', 'slide', 'Compare what the slide says INNER JOIN and LEFT JOIN return.'),
    ('lecture_biology_01.png', 'slide', 'What inputs and output does the slide give for photosynthesis?'),
    ('receipt_022.png', 'receipt', 'What item was purchased and what total is shown? Use only visible evidence.'),
    ('receipt_024.png', 'receipt', 'What item was purchased and what total is shown? Use only visible evidence.'),
    ('bridge_photo.png', 'public_photo', 'Describe the visible structure and surroundings.'),
    ('kitchen_photo.jpg', 'public_photo', 'Describe the visible objects and room setting.'),
    ('project_board_01.png', 'ui', 'What status columns are visible, and name one task shown in progress?'),
    ('document_05.png', 'document', 'What behavior does the design note specify for GET /items and POST /items?'),
    ('object_study_01.png', 'visual_object', 'Describe the main visible subject, its colors, and the setting.'),
    ('object_study_02.png', 'visual_object', 'Describe the main visible subject, its colors, and the setting.'),
]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _metric(mx, name):
    method = getattr(mx, name, None) or getattr(mx.metal, name, None)
    if method is None:
        return None
    try:
        return method()
    except Exception as exc:
        return f'Unavailable: {exc}'


def _machine_pressure():
    try:
        result = subprocess.run(['memory_pressure', '-Q'], text=True, capture_output=True, check=False)
        return {'exit_code':result.returncode, 'stdout':result.stdout.strip(), 'stderr':result.stderr.strip()}
    except OSError as exc:
        return {'error':str(exc)}


def run_worker(backend, data_dir, models_dir, output):
    os.environ.update({
        'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1',
        'SIGHTVAULT_DATA':str(data_dir), 'SIGHTVAULT_MODELS':str(models_dir),
        'SIGHTVAULT_VISION':backend,
    })
    sys.path.insert(0, str(ROOT))
    from PIL import Image
    import mlx.core as mx
    import mlx_vlm
    from sightvault.config import Config
    from sightvault.models import Models

    config = Config(root=ROOT, data=data_dir, models=models_dir, vision=backend)
    config.prepare()
    uri = f"file:{config.db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute('SELECT filename,image_path,ocr_text,ocr_backend FROM assets').fetchall()
    by_name = {row[0]:row for row in rows}
    absent = [filename for filename, _, _ in CASES if filename not in by_name]
    if absent:
        raise RuntimeError(f'Benchmark database is missing cases: {absent}')

    original_load = mlx_vlm.load
    load_times = []
    def measured_load(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_load(*args, **kwargs)
        finally:
            load_times.append(time.perf_counter() - started)
    mlx_vlm.load = measured_load

    model = Models(config)
    initial_rss = None
    try:
        import psutil
        process = psutil.Process(os.getpid())
        initial_rss = process.memory_info().rss
    except Exception:
        process = None

    answers = []
    for index, (filename, category, question) in enumerate(CASES, 1):
        _, relative_image, ocr_text, ocr_backend = by_name[filename]
        image_path = data_dir/'images'/relative_image
        with Image.open(image_path) as source:
            image = source.convert('RGB')
        started = time.perf_counter()
        answer = model.analyze(image, question, ocr_text)
        elapsed = time.perf_counter() - started
        load_s = load_times[-1] if len(load_times) > len(answers) else 0.0
        try:
            processor = model._vlm[0]
            tokenizer = getattr(processor, 'tokenizer', None)
            answer_tokens = len(tokenizer.encode(answer, add_special_tokens=False)) if tokenizer else None
        except Exception:
            answer_tokens = None
        record = {
            'filename':filename, 'category':category, 'question':question,
            'image_sha256':sha256(image_path), 'ocr_backend':ocr_backend,
            'answer':answer, 'answer_chars':len(answer), 'visible_answer_tokens':answer_tokens,
            'max_new_tokens':384, 'thinking_enabled':False,
            'latency_s':round(elapsed, 3), 'model_load_s':round(load_s, 3),
            'generation_and_preparation_s':round(max(0.0, elapsed-load_s), 3),
        }
        answers.append(record)
        print(json.dumps({'backend':backend, 'case':index, 'total':len(CASES),
                          'filename':filename, 'latency_s':record['latency_s'],
                          'load_s':record['model_load_s'], 'answer':answer}), flush=True)

    try:
        rss = process.memory_info().rss if process else None
    except Exception:
        rss = None
    status = model.status()['vision']
    report = {
        'backend':backend, 'model':status['name'], 'model_id':status['model_id'],
        'runtime':status['runtime'], 'precision':status['precision'],
        'device':model.active.get('vision'), 'instance_root':repo_relative(config.root),
        'dataset':{'database':repo_relative(config.db_path), 'image_count':len(CASES), 'ocr_source':'isolated copied demo database'},
        'settings':{'max_new_tokens':384, 'thinking_enabled':False, 'workers':1, 'repeats_per_case':1},
        'timing':{
            'model_load_s':round(load_times[0], 3) if load_times else None,
            'warm_latency_s':[answer['latency_s'] for answer in answers[1:]],
            'median_warm_latency_s':None,
        },
        'memory':{
            'process_initial_rss_bytes':initial_rss, 'process_current_rss_bytes':rss,
            'process_peak_rss_bytes':int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            'mlx_active_memory_bytes':_metric(mx, 'get_active_memory'),
            'mlx_peak_memory_bytes':_metric(mx, 'get_peak_memory'),
            'mlx_device_info':_metric(mx, 'device_info'), 'whole_machine_pressure':_machine_pressure(),
        },
        'status':status, 'answers':answers,
    }
    if len(answers) > 1:
        ordered = sorted(answer['latency_s'] for answer in answers[1:])
        mid = len(ordered) // 2
        report['timing']['median_warm_latency_s'] = round(ordered[mid] if len(ordered) % 2 else (ordered[mid-1]+ordered[mid])/2, 3)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    del model
    import gc
    gc.collect()
    mx.clear_cache()
    print(json.dumps({'backend':backend, 'status':status, 'model_load_s':report['timing']['model_load_s'],
                      'median_warm_latency_s':report['timing']['median_warm_latency_s'],
                      'peak_rss_bytes':report['memory']['process_peak_rss_bytes'],
                      'mlx_peak_memory_bytes':report['memory']['mlx_peak_memory_bytes']}), flush=True)


def run_suite(data_dir, models_dir, output):
    reports = {}
    for backend in ('minicpm', 'lfm3'):
        with tempfile.NamedTemporaryFile(prefix=f'sightvault-{backend}-', suffix='.json', delete=False) as tmp:
            worker_output = Path(tmp.name)
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', backend,
                   '--data-dir', str(data_dir), '--models-dir', str(models_dir), '--worker-output', str(worker_output)]
        print('Starting serialized backend worker: ' + backend, flush=True)
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, bufsize=1, env={**os.environ, 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1'})
        for line in process.stdout:
            print(line.rstrip(), flush=True)
        return_code = process.wait()
        if return_code:
            worker_output.unlink(missing_ok=True)
            raise RuntimeError(f'{backend} worker exited with status {return_code}')
        reports[backend] = json.loads(worker_output.read_text(encoding='utf-8'))
        worker_output.unlink(missing_ok=True)
        if reports[backend]['status']['backend'] != backend:
            raise RuntimeError(f'{backend} worker reported a different model identity')

    by_backend = {name:{answer['filename']:answer for answer in report['answers']} for name, report in reports.items()}
    comparisons = []
    for filename, category, question in CASES:
        comparisons.append({
            'filename':filename, 'category':category, 'question':question,
            'image_sha256':by_backend['minicpm'][filename]['image_sha256'],
            'same_image':by_backend['minicpm'][filename]['image_sha256']==by_backend['lfm3'][filename]['image_sha256'],
            'same_question':by_backend['minicpm'][filename]['question']==by_backend['lfm3'][filename]['question'],
            'reviewer_assessment':None,
        })
    result = {
        'result':'completed; answer quality requires reviewer assessment',
        'provenance':{
            'source':'copied synthetic demo database and evidence images in the experimental worktree',
            'source_database':'data/sightvault.sqlite3', 'instance_root':'.',
            'backends_ran_serially':True,
        },
        'method':{'same_question_per_image':True, 'same_stored_ocr_per_image':True,
                  'thinking_tokens_disabled':True, 'max_new_tokens':384, 'generation_workers':1},
        'comparisons':comparisons, 'backends':reports,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'output':str(output), 'images':len(comparisons),
                      'identical_inputs':all(row['same_image'] and row['same_question'] for row in comparisons),
                      'median_warm_latency_s':{key:value['timing']['median_warm_latency_s'] for key,value in reports.items()},
                      'peak_rss_bytes':{key:value['memory']['process_peak_rss_bytes'] for key,value in reports.items()},
                      'mlx_peak_memory_bytes':{key:value['memory']['mlx_peak_memory_bytes'] for key,value in reports.items()}}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data')
    parser.add_argument('--models-dir', type=Path, default=ROOT/'.models')
    parser.add_argument('--output', type=Path, default=ROOT/'qa'/'vision-benchmark.json')
    parser.add_argument('--worker', choices=('minicpm','lfm3'), help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.worker_output:
            parser.error('--worker-output is required for a worker')
        run_worker(args.worker, args.data_dir, args.models_dir, args.worker_output)
    else:
        run_suite(args.data_dir, args.models_dir, args.output)


if __name__ == '__main__':
    main()
