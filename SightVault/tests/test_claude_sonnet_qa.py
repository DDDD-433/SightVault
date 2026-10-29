"""Independent QA (Claude Sonnet 5.5 xhigh): ingestion, database, metadata/date and API-boundary regressions.

Conventions
-----------
* Every test uses disposable tmp_path databases/folders and stub models; no real model, browser,
  canonical data/, .models/ or samples/ is touched. Mocks prove control flow only, never hardware behavior.
* Tests marked ``xfail(strict=True)`` demonstrate a CONFIRMED defect in current production code: they fail
  today and will turn into hard XPASS failures as soon as the defect is fixed, which tells the fixer to delete
  the marker. Run ``pytest --runxfail tests/test_claude_sonnet_qa.py`` to see the underlying failures.
* Unmarked tests are controls: behavior verified correct, kept so a later fix cannot regress it.
* Finding ids (S-1 ...) match the QA report returned to the QA lead:
  S-1 EXIF Exif-sub-IFD capture time ignored      S-8  forced refresh with models down destroys data
  S-2 EXIF year-1/9999 OverflowError aborts file   S-9  case-variant folder spelling duplicates rows (macOS)
  S-3 re-index cannot repair missing stored copy   S-10 encode_vector stores a zero vector on float32 overflow
  S-4 shared .partial name, concurrent writers     S-11 /api/index HTTP 500 for unusable folder strings
  S-5 .partial left behind after a failed write    S-12 /api/analyze 503 + path leak for missing stored copy
  S-6 edits orphan old stored copies               S-13 alpha channel dropped: transparent images become black
  S-7 reused vector tagged with embedding_error    S-14 unreadable subfolder silently skipped
  Tests named / marked SUGGESTION or HARDENING are design-dependent or defence-in-depth, not outright defects.
"""
import errno
import os
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image

from sightvault.config import Config
from sightvault.database import Database, decode_vector, encode_vector
from sightvault.ingestion import Ingestor, _atomic_save, scan_folder
from sightvault.metadata import image_metadata
from sightvault.search import Search, date_bounds
from sightvault.server import create_app
from test_core import FakeModels, record


# --------------------------------------------------------------------------------------------- helpers
@pytest.fixture
def env(tmp_path):
    cfg = Config(root=tmp_path, data=tmp_path / 'data', models=tmp_path / 'models')
    cfg.prepare()
    return cfg, Database(cfg.db_path), FakeModels()


def make_folder(tmp_path, name='pics'):
    folder = tmp_path / name
    folder.mkdir()
    return folder


def solid(path, size=(48, 32), color='red'):
    Image.new('RGB', size, color).save(path)
    return path


def stored_names(cfg):
    return {p.name for sub in ('images', 'thumbs') for p in (cfg.data / sub).iterdir()}


def referenced_names(db):
    rows = db.all()
    return {r['image_path'] for r in rows} | {r['thumbnail_path'] for r in rows}


def exif_image(path, date, offset, layouts=('exif_ifd',)):
    """Write an image whose EXIF carries DateTimeOriginal (36867) + OffsetTimeOriginal (36881).

    Standards-compliant cameras/phones store both tags in the Exif sub-IFD (0x8769), not IFD0.
    """
    exif = Image.Exif()
    if 'ifd0' in layouts:
        exif[36867], exif[36881] = date, offset
    if 'exif_ifd' in layouts:
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        sub[36867], sub[36881] = date, offset
    Image.new('RGB', (24, 24), 'red').save(path, exif=exif.tobytes())  # raw bytes: Pillow's PNG writer drops an Exif object
    if 'exif_ifd' in layouts:  # fixture sanity: fail loudly (RuntimeError) if the file is not what we think
        with Image.open(path) as check:
            if check.getexif().get_ifd(ExifTags.IFD.Exif).get(36867) != date:
                raise RuntimeError('fixture did not persist the Exif sub-IFD for ' + path.suffix)
    return path


def metadata_for(path):
    with Image.open(path) as opened:
        return image_metadata(path, opened)


def client_for(cfg, models):
    return TestClient(create_app(cfg, models), raise_server_exceptions=False)


def wait_for_job(client, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f'/api/jobs/{job_id}').json()
        if state['state'] != 'running':
            return state
        time.sleep(0.05)
    raise AssertionError('job did not finish: ' + str(state))


# =============================================================================== metadata / dates
@pytest.mark.parametrize('ext', ['jpg', 'png', 'webp'])
def test_exif_capture_time_in_exif_subifd_is_used(tmp_path, ext):
    path = exif_image(tmp_path / f'photo.{ext}', '2020:01:02 03:04:05', '+05:30')
    meta = metadata_for(path)
    assert meta['timestamp_source'] == 'EXIF capture time (with timezone)'
    assert meta['timestamp'] == '2020-01-01T21:34:05+00:00'  # 03:04:05+05:30 expressed in UTC


def test_exif_in_ifd0_layout_is_parsed_control(tmp_path):
    """Control for S-1: the parsing/conversion itself is right; only the tag *location* is wrong."""
    path = exif_image(tmp_path / 'ifd0.jpg', '2020:01:02 03:04:05', '+05:30', layouts=('ifd0',))
    meta = metadata_for(path)
    assert meta['timestamp'] == '2020-01-01T21:34:05+00:00'
    assert meta['timestamp_source'] == 'EXIF capture time (with timezone)'


def test_exif_without_offset_falls_back_to_mtime_control(tmp_path):
    path = tmp_path / 'naive.jpg'
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[36867] = '2020:01:02 03:04:05'  # no offset: must not invent a timezone
    Image.new('RGB', (16, 16)).save(path, exif=exif.tobytes())
    os.utime(path, (1_600_000_000, 1_600_000_000))
    meta = metadata_for(path)
    assert meta['timestamp_source'] == 'File modified time'
    assert meta['timestamp'] == '2020-09-13T12:26:40+00:00'


@pytest.mark.parametrize('date,offset', [('0001:01:01 00:00:00', '+05:00'), ('9999:12:31 23:59:59', '-05:00')])
def test_extreme_exif_dates_fall_back_to_mtime(tmp_path, date, offset):
    path = exif_image(tmp_path / 'extreme.jpg', date, offset, layouts=('ifd0', 'exif_ifd'))
    meta = metadata_for(path)  # must not raise
    assert meta['timestamp_source'] in ('File modified time', 'EXIF capture time (with timezone)')


def test_extreme_exif_date_does_not_block_indexing(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    exif_image(folder / 'extreme.jpg', '9999:12:31 23:59:59', '-05:00', layouts=('ifd0', 'exif_ifd'))
    solid(folder / 'fine.png')
    report = Ingestor(cfg, db, models).run(folder)
    assert report['failed'] == 0, report['errors']
    assert db.stats()['total'] == 2


@pytest.mark.parametrize('date,offset', [
    ('0000:00:00 00:00:00', '+00:00'), ('2020:13:40 25:61:61', '+00:00'), ('2020-01-02 03:04:05', '+00:00'),
    ('2020:01:02 03:04:05', '+25:00'), ('2020:01:02 03:04:05', 'garbage'), ('2020:01:02 23:59:60', '+00:00'),
])
def test_malformed_exif_dates_fall_back_without_raising_control(tmp_path, date, offset):
    path = exif_image(tmp_path / 'bad.jpg', date, offset, layouts=('ifd0',))
    assert metadata_for(path)['timestamp_source'] == 'File modified time'


def test_epoch_and_pre_epoch_mtimes_index_and_filter_control(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    stamps = {'zero.png': 0, 'minus_one.png': -1, 'y1940.png': -946_728_000, 'y2038.png': 2**31 + 5}
    for index, (name, seconds) in enumerate(stamps.items()):
        solid(folder / name, size=(20 + index, 20))
        os.utime(folder / name, (seconds, seconds))
    report = Ingestor(cfg, db, models).run(folder)
    assert report['failed'] == 0, report['errors']
    found = {r['filename']: r['timestamp'] for r in db.all()}
    assert found['zero.png'] == '1970-01-01T00:00:00+00:00'
    assert found['minus_one.png'] == '1969-12-31T23:59:59+00:00'
    search = Search(db, models)
    names = lambda **k: sorted(r['filename'] for r in search.run('', top_k=100, **k)['results'])  # noqa: E731
    assert names(start='1970-01-01', end='1970-01-01') == ['zero.png']
    assert names(start='1969-12-31', end='1969-12-31') == ['minus_one.png']
    assert names(end='1969-12-31') == ['minus_one.png', 'y1940.png']


def test_date_bounds_on_dst_transition_days_control(env):
    """UI sends local-midnight instants; the 23-hour (spring) and 25-hour (fall) days must be exact."""
    _, db, models = env
    spring = date_bounds('2026-03-08T00:00:00-05:00', '2026-03-09T00:00:00-04:00')
    assert spring == ('2026-03-08T05:00:00+00:00', '2026-03-09T04:00:00+00:00')
    fall = date_bounds('2026-11-01T00:00:00-04:00', '2026-11-02T00:00:00-05:00')
    assert fall == ('2026-11-01T04:00:00+00:00', '2026-11-02T05:00:00+00:00')
    db.upsert(record(1, stamp='2026-03-09T03:59:59+00:00'))
    db.upsert(record(2, stamp='2026-03-09T04:00:00+00:00'))
    found = Search(db, models).run('', start='2026-03-08T00:00:00-05:00', end='2026-03-09T00:00:00-04:00')
    assert [r['filename'] for r in found['results']] == ['1.png']


# ======================================================================================= ingestion
def test_cancel_mid_run_then_resume_is_consistent_control(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    for n in range(6):
        solid(folder / f'{n}.png', size=(30 + n, 30))
    cancel = threading.Event()

    def progress(state):
        if state['processed'] >= 2:
            cancel.set()

    first = Ingestor(cfg, db, models).run(folder, progress, cancel=cancel)
    assert first['cancelled'] and first['processed'] == 2 and 'missing' not in first
    assert db.stats()['total'] == 2 and not list(cfg.data.rglob('*.partial'))
    second = Ingestor(cfg, db, models).run(folder)
    assert (second['indexed'], second['skipped'], second['failed'], second['cancelled']) == (4, 2, 0, False)
    rows = db.all()
    assert len(rows) == 6 and all(r['embedding'] and r['ocr_text'] for r in rows)
    assert stored_names(cfg) == referenced_names(db)


def test_crash_between_inference_and_db_write_leaves_no_row_and_heals_control(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'a.png')
    real_upsert, calls = db.upsert, []

    def crashing_upsert(rec):
        calls.append(rec['filename'])
        raise sqlite3.OperationalError('simulated crash before commit')

    db.upsert = crashing_upsert
    first = Ingestor(cfg, db, models).run(folder)
    assert first['failed'] == 1 and db.stats()['total'] == 0  # no half-written row
    db.upsert = real_upsert
    second = Ingestor(cfg, db, models).run(folder)
    assert second['indexed'] == 1 and second['failed'] == 0
    row = db.all()[0]
    assert row['embedding'] and row['ocr_text']
    assert stored_names(cfg) == referenced_names(db) and not list(cfg.data.rglob('*.partial'))
    with db.connect() as conn:  # external-content FTS must agree with the table
        conn.execute("INSERT INTO assets_fts(assets_fts) VALUES('integrity-check')")


def test_identical_bytes_at_two_paths_share_one_stored_copy_and_one_embedding_control(env, tmp_path):
    cfg, db, models = env
    embedded = []
    models.embed_image = lambda image: embedded.append(1) or np.array([1, 0, 0], dtype=np.float32)
    folder = make_folder(tmp_path)
    solid(folder / 'a.png', color='green')
    (folder / 'b.png').write_bytes((folder / 'a.png').read_bytes())
    os.utime(folder / 'a.png', (1_700_000_000, 1_700_000_000))
    os.utime(folder / 'b.png', (1_700_086_400, 1_700_086_400))
    report = Ingestor(cfg, db, models).run(folder)
    rows = {r['filename']: r for r in db.all()}
    assert report['duplicates'] == 1 and len(embedded) == 1 and len(rows) == 2
    assert rows['a.png']['image_path'] == rows['b.png']['image_path']
    assert rows['a.png']['timestamp'] != rows['b.png']['timestamp']
    assert len(list((cfg.data / 'images').iterdir())) == 1


@pytest.mark.parametrize('mode', ['RGBA', 'LA'])
def test_transparent_images_are_flattened_onto_a_visible_background(env, tmp_path, mode):
    cfg, db, models = env
    seen = []
    models.embed_image = lambda image: seen.append(image.getextrema()) or np.array([1, 0, 0], dtype=np.float32)
    folder = make_folder(tmp_path)
    clear, ink = ((0, 0, 0, 0), (0, 0, 0, 255)) if mode == 'RGBA' else ((0, 0), (0, 255))
    picture = Image.new(mode, (120, 60), clear)
    picture.paste(Image.new(mode, (80, 40), ink), (20, 10))
    picture.save(folder / 'diagram.png')
    assert Ingestor(cfg, db, models).run(folder)['indexed'] == 1
    stored = Image.open(cfg.data / 'images' / db.all()[0]['image_path'])
    assert any(low != high for low, high in stored.convert('RGB').getextrema()), 'stored copy is a single flat colour'
    assert any(low != high for low, high in seen[0]), 'the embedding model received a flat image'


def test_exif_orientation_is_applied_to_stored_copy_and_dimensions_control(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise to display
    Image.new('RGB', (60, 40), 'red').save(folder / 'rotated.jpg', exif=exif.tobytes())
    Ingestor(cfg, db, models).run(folder)
    row = db.all()[0]
    assert (row['width'], row['height']) == (40, 60)
    with Image.open(cfg.data / 'images' / row['image_path']) as stored:
        assert stored.size == (40, 60)


def test_reindex_restores_missing_stored_copies(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'a.png')
    Ingestor(cfg, db, models).run(folder)
    row = db.all()[0]
    full, thumb = cfg.data / 'images' / row['image_path'], cfg.data / 'thumbs' / row['thumbnail_path']
    full.unlink(); thumb.unlink()
    with client_for(cfg, models) as client:  # exactly the remedy the API error message tells the user to run
        assert client.get(f"/api/assets/{row['id']}/image").status_code == 404
        assert client.get(f"/api/assets/{row['id']}/image?thumbnail=true").status_code == 404
    Ingestor(cfg, db, models).run(folder)
    assert full.is_file() and thumb.is_file()


def test_atomic_save_tolerates_two_concurrent_writers(tmp_path, monkeypatch):
    target = tmp_path / 'same-digest.png'
    barrier, errors = threading.Barrier(2), []
    real_save = Image.Image.save

    def save_then_rendezvous(self, fp, *args, **kwargs):
        real_save(self, fp, *args, **kwargs)
        try:
            barrier.wait(timeout=1.5)  # both writers have finished writing before either one renames
        except threading.BrokenBarrierError:
            pass  # a lock-based fix serialises writers; that is fine too

    monkeypatch.setattr(Image.Image, 'save', save_then_rendezvous)

    def writer():
        try:
            _atomic_save(Image.new('RGB', (40, 40), 'blue'), target, format='PNG')
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer) for _ in range(2)]
    [t.start() for t in threads]; [t.join(10) for t in threads]
    assert not errors, errors
    with Image.open(target) as final:
        final.load()


def test_two_concurrent_ingestion_runs_have_no_spurious_failures(env, tmp_path, monkeypatch):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'a.png', size=(80, 60), color='orange')
    barrier, reports = threading.Barrier(2), {}
    real_save = Image.Image.save

    def save_then_rendezvous(self, fp, *args, **kwargs):
        real_save(self, fp, *args, **kwargs)
        try:
            barrier.wait(timeout=1.5)
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr(Image.Image, 'save', save_then_rendezvous)

    def run(name):
        reports[name] = Ingestor(cfg, db, FakeModels()).run(folder)

    threads = [threading.Thread(target=run, args=(n,)) for n in ('one', 'two')]
    [t.start() for t in threads]; [t.join(30) for t in threads]
    assert [r['failed'] for r in reports.values()] == [0, 0], [r['errors'] for r in reports.values()]
    assert db.stats()['total'] == 1


def test_failed_write_does_not_leave_partial_file(env, tmp_path, monkeypatch):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'a.png')
    real_save = Image.Image.save

    def disk_full(self, fp, *args, **kwargs):
        real_save(self, fp, *args, **kwargs)
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(Image.Image, 'save', disk_full)
    report = Ingestor(cfg, db, models).run(folder)
    assert report['failed'] == 1 and db.stats()['total'] == 0
    assert not list(cfg.data.rglob('*.partial'))


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason='S-6 ingestion.py:70-77 never releases the stored copy/thumbnail of the previous version of an '
                          'edited file, so disk usage grows by two files per edit')
def test_editing_a_file_does_not_orphan_old_stored_copies(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    target = folder / 'screen.png'
    for color in ('red', 'green', 'blue'):
        solid(target, color=color)
        os.utime(target, None)
        Ingestor(cfg, db, models).run(folder)
    assert db.stats()['total'] == 1
    assert stored_names(cfg) == referenced_names(db), sorted(stored_names(cfg) - referenced_names(db))


def test_edit_keeps_copy_still_used_by_a_duplicate_control(env, tmp_path):
    """Guard for any future orphan cleanup for S-6: a copy shared with another path must survive an edit."""
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'a.png', color='green')
    (folder / 'b.png').write_bytes((folder / 'a.png').read_bytes())
    Ingestor(cfg, db, models).run(folder)
    solid(folder / 'a.png', color='black')
    Ingestor(cfg, db, models).run(folder)
    for row in db.all():
        assert (cfg.data / 'images' / row['image_path']).is_file()
        assert (cfg.data / 'thumbs' / row['thumbnail_path']).is_file()


def test_reused_embedding_is_not_tagged_with_an_embedding_error(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'b_orig.png', color='red')
    Ingestor(cfg, db, models).run(folder)  # healthy run stores a vector for b_orig
    solid(folder / 'a_new.png', color='blue', size=(50, 41))
    (folder / 'c_copy.png').write_bytes((folder / 'b_orig.png').read_bytes())

    def broken(_):
        raise RuntimeError('model exploded')

    models.embed_image = broken  # a_new sorts first, trips the run-level breaker; c_copy then reuses the cache
    Ingestor(cfg, db, models).run(folder)
    contradictory = [r['filename'] for r in db.all() if r['embedding'] and r['embedding_error']]
    assert not contradictory, contradictory


def test_forced_refresh_with_models_down_does_not_destroy_existing_data(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'a.png')
    Ingestor(cfg, db, models).run(folder)
    before = db.all()[0]
    assert before['embedding'] and before['ocr_text']

    def down(_):
        raise RuntimeError('model unavailable')

    models.embed_image = down
    models.ocr = lambda image, backend=None: {'text': '', 'backend': 'unavailable', 'error': 'ocr unavailable'}
    Ingestor(cfg, db, models).run(folder, force=True)  # README: "--force on the CLI refreshes existing inference"
    after = db.all()[0]
    assert after['embedding'] == before['embedding'] and after['ocr_text'] == before['ocr_text']


def test_case_variant_folder_spelling_does_not_duplicate_rows(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path, 'pics')
    solid(folder / 'Shot.png')
    alternate = tmp_path / 'PICS'
    if not alternate.exists():
        pytest.skip('needs a case-insensitive filesystem (default macOS APFS)')
    Ingestor(cfg, db, models).run(folder)
    second = Ingestor(cfg, db, models).run(alternate)
    assert db.stats()['total'] == 1, [r['filepath'] for r in db.all()]
    assert second['skipped'] == 1


@pytest.mark.skipif(os.geteuid() == 0, reason='permission bits are not enforced for root')
def test_unreadable_subfolder_is_reported_not_silently_skipped(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    (folder / 'locked').mkdir()
    solid(folder / 'ok.png')
    solid(folder / 'locked' / 'hidden.png', size=(50, 33))
    (folder / 'locked').chmod(0)
    try:
        report = Ingestor(cfg, db, models).run(folder)
    finally:
        (folder / 'locked').chmod(0o700)
    assert report['indexed'] == 1
    messages = [w.lower() for w in report['warnings']] + [e['message'].lower() for e in report['errors']]
    assert any(word in m for m in messages for word in ('permission', 'unreadable', 'could not be read', 'access')), messages


def test_one_failing_image_does_not_disable_embeddings_for_the_rest_of_the_run(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    for n, color in enumerate(('red', 'green', 'blue')):
        solid(folder / f'{n}.png', size=(40 + n, 30), color=color)
    calls = []

    def flaky(image):
        calls.append(image.size)
        if len(calls) == 1:
            raise RuntimeError('this particular image broke the processor')
        return np.array([1, 0, 0], dtype=np.float32)

    models.embed_image = flaky
    Ingestor(cfg, db, models).run(folder)
    assert sum(1 for r in db.all() if r['embedding']) == 2


# ============================================================================================ database
def test_vector_storage_roundtrip_shape_dtype_control():
    source = np.random.default_rng(3).normal(size=768)  # float64 in, float32 little-endian out
    blob = encode_vector(source)
    assert len(blob) == 768 * 4
    stored = decode_vector(blob)
    assert stored.dtype == np.dtype('<f4') and stored.shape == (768,)
    assert abs(float(np.linalg.norm(stored)) - 1.0) < 1e-5
    assert np.allclose(stored, source / np.linalg.norm(source), atol=1e-6)


@pytest.mark.parametrize('vector', [[1e20, 0], [3e19, 4e19]])
def test_encode_vector_never_stores_a_zero_vector(vector):
    try:
        blob = encode_vector(vector)
    except ValueError:
        return
    assert abs(float(np.linalg.norm(decode_vector(blob))) - 1.0) < 1e-3


def test_encode_vector_rejects_batch_shaped_input():
    try:
        blob = encode_vector(np.ones((2, 4), dtype=np.float32))
    except ValueError:
        return
    raise AssertionError(f'a (2, 4) batch was accepted and stored as one {len(blob) // 4}-dimensional vector')


def test_one_corrupt_embedding_blob_does_not_break_every_search(env):
    _, db, models = env
    db.upsert(record(1, 'CUDA out of memory'))
    corrupt = record(2, 'unrelated')
    corrupt['embedding'] = b'abc'
    db.upsert(corrupt)
    found = Search(db, models).run('CUDA')
    assert [r['filename'] for r in found['results']][:1] == ['1.png']


def test_fts_and_table_stay_consistent_under_threaded_upserts_control(tmp_path):
    db = Database(tmp_path / 'concurrent.sqlite3')
    errors = []

    def writer(worker):
        try:
            for i in range(40):
                db.upsert(record(i % 8, f'worker {worker} iteration {i} ERESOLVE'))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(4)]
    [t.start() for t in threads]; [t.join(60) for t in threads]
    assert not errors and db.stats()['total'] == 8
    with db.connect() as conn:
        conn.execute("INSERT INTO assets_fts(assets_fts) VALUES('integrity-check')")
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert len(db.fts(['ERESOLVE'])) == 8


def test_schema_enforces_unique_path_and_not_null_control(tmp_path):
    db = Database(tmp_path / 'schema.sqlite3')
    first = db.upsert(record(1))
    assert db.upsert(record(1, 'changed')) == first and db.stats()['total'] == 1
    missing = record(2)
    missing['timestamp'] = None
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert(missing)
    assert db.stats()['total'] == 1


# ================================================================================================= API
@pytest.mark.parametrize('folder', ['~no_such_user_zz/pictures', 'a' * 300], ids=['unknown-home', 'name-too-long'])
def test_index_rejects_unusable_folder_values_with_422(env, folder):
    cfg, _, models = env
    with client_for(cfg, models) as client:
        response = client.post('/api/index', json={'folder': folder})
    assert response.status_code == 422, (response.status_code, response.text)


@pytest.mark.skipif(os.geteuid() == 0, reason='permission bits are not enforced for root')
def test_index_rejects_folder_inside_unreadable_directory_with_422(env, tmp_path):
    cfg, _, models = env
    locked = tmp_path / 'locked'
    (locked / 'inner').mkdir(parents=True)
    locked.chmod(0)
    try:
        with client_for(cfg, models) as client:
            response = client.post('/api/index', json={'folder': str(locked / 'inner')})
    finally:
        locked.chmod(0o700)
    assert response.status_code == 422, (response.status_code, response.text)


def test_scan_folder_value_errors_are_the_documented_contract_control(tmp_path):
    with pytest.raises(ValueError):
        scan_folder(tmp_path / 'missing')
    with pytest.raises(ValueError):
        scan_folder(tmp_path / 'x\x00y')


def test_analyze_with_missing_stored_image_is_actionable_and_does_not_leak_paths(env, tmp_path):
    cfg, db, models = env
    folder = make_folder(tmp_path)
    solid(folder / 'a.png')
    Ingestor(cfg, db, models).run(folder)
    row = db.all()[0]
    (cfg.data / 'images' / row['image_path']).unlink()
    with client_for(cfg, models) as client:
        response = client.post('/api/analyze', json={'asset_id': row['id'], 'question': 'What is visible?'})
    assert response.status_code in (404, 409, 410), (response.status_code, response.text)
    assert str(cfg.data) not in response.text and 'Errno' not in response.text


def test_tampered_image_path_cannot_serve_files_outside_data_dir(env, tmp_path):
    cfg, db, models = env
    (tmp_path / 'secret.txt').write_text('TOP-SECRET-CONTENT')
    bad = record(1)
    bad['image_path'] = '../../secret.txt'  # data/images/../../secret.txt == tmp_path/secret.txt
    row_id = db.upsert(bad)
    with client_for(cfg, models) as client:
        response = client.get(f'/api/assets/{row_id}/image')
    assert b'TOP-SECRET-CONTENT' not in response.content


def test_traversal_and_foreign_routes_serve_nothing_control(env, tmp_path):
    cfg, db, models = env
    (tmp_path / 'secret.txt').write_text('TOP-SECRET-CONTENT')
    with client_for(cfg, models) as client:
        for path in ('/static/../secret.txt', '/static/%2e%2e/secret.txt', '/static/..%2f..%2fsecret.txt',
                     '/static//etc/passwd', '/api/assets/1/image/../../secret.txt', '/api/jobs/../../secret.txt'):
            response = client.get(path)
            assert response.status_code in (404, 422) and b'TOP-SECRET' not in response.content, path


def test_origin_and_host_guards_control(env):
    cfg, _, models = env
    with client_for(cfg, models) as client:
        assert client.post('/api/jobs/x/cancel', headers={'Origin': 'null'}).status_code == 403
        assert client.post('/api/jobs/x/cancel', headers={'Origin': 'http://evil.example'}).status_code == 403
        assert client.post('/api/jobs/x/cancel', headers={'Origin': 'http://testserver:81'}).status_code == 403
        assert client.post('/api/jobs/x/cancel', headers={'Origin': 'http://testserver'}).status_code == 404
        for host in ('evil.example', 'localhost.', 'localhost:7861@evil.example', '[::1]:7861'):
            assert client.get('/api/status', headers={'Host': host}).status_code == 400, host
        assert client.get('/api/status', headers={'Host': '127.0.0.1:7861'}).status_code == 200
        for method in ('put', 'patch', 'delete'):
            assert getattr(client, method)('/api/search').status_code == 405
        assert client.options('/api/status', headers={'Origin': 'http://evil.example'}).status_code == 405


def test_numeric_parameter_boundaries_never_crash_control(env):
    cfg, db, models = env
    for i in range(3):
        db.upsert(record(i, 'CUDA'))
    with client_for(cfg, models) as client:
        assert len(client.get('/api/search?top_k=-5').json()['results']) == 1  # clamped to 1
        assert len(client.get('/api/search?top_k=0').json()['results']) == 1
        assert len(client.get('/api/search?top_k=99999999999999999999999').json()['results']) == 3
        for path in ('/api/assets/-1', '/api/assets/0', f'/api/assets/{2**63}', f'/api/assets/{-2**63 - 1}'):
            assert client.get(path).status_code == 404, path
        for path in ('/api/assets/1.5', '/api/assets/abc', '/api/assets/1/image?thumbnail=maybe'):
            assert client.get(path).status_code == 422, path
        for body in ({'asset_id': 'x', 'question': 'q'}, {'asset_id': 1.5, 'question': 'q'}, {'asset_id': None, 'question': 'q'},
                     {'asset_id': 1, 'question': ''}, {'asset_id': 1, 'question': 'x' * 2001}, {}):
            assert client.post('/api/analyze', json=body).status_code == 422, body
        assert client.get('/api/search?q=' + 'a' * 1001).status_code == 422
        for path in ('/api/search?similar_id=-1', f'/api/search?similar_id={2**70}', '/api/search?start=2026-10-20T00:00:00'):
            assert client.get(path).status_code == 422, path


def test_index_job_cancel_conflict_and_resume_control(env, tmp_path):
    cfg, db, _ = env
    gate = threading.Event()
    seen = []

    class SlowOcr(FakeModels):
        def ocr(self, image, backend=None):
            seen.append(1)
            if len(seen) >= 2:
                gate.wait(15)
            return super().ocr(image, backend)

    folder = make_folder(tmp_path)
    for n in range(5):
        solid(folder / f'{n}.png', size=(30 + n, 30))
    try:
        with client_for(cfg, SlowOcr()) as client:
            job = client.post('/api/index', json={'folder': str(folder)}).json()['job_id']
            deadline = time.time() + 10
            while len(seen) < 2 and time.time() < deadline:
                time.sleep(0.02)
            assert client.post('/api/index', json={'folder': str(folder)}).status_code == 409
            assert client.post(f'/api/jobs/{job}/cancel').status_code == 200
            # Cancellation is cooperative: the job is still "running", so a new job must still be refused.
            assert client.get(f'/api/jobs/{job}').json()['state'] == 'running'
            assert client.post('/api/index', json={'folder': str(folder)}).status_code == 409
            gate.set()
            final = wait_for_job(client, job)
            assert final['state'] == 'cancelled' and final['processed'] == 2 == db.stats()['total']
            resume = wait_for_job(client, client.post('/api/index', json={'folder': str(folder)}).json()['job_id'])
            assert resume['state'] == 'complete' and (resume['indexed'], resume['skipped']) == (3, 2)
    finally:
        gate.set()
