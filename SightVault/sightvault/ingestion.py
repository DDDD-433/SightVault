from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import time
from PIL import Image, ImageOps, UnidentifiedImageError
from .database import encode_vector
from .metadata import image_metadata
from .models import EMBEDDING_ID

EXTENSIONS = {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}

def scan_folder(folder, limit=2000):
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError('Choose an existing folder containing images.')
    files, unsupported = [], 0
    # Symlinked folders and hidden system directories are intentionally excluded.
    import os
    for base, dirs, names in os.walk(path, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(base)/d).is_symlink())
        for name in sorted(names):
            item = Path(base)/name
            if name.startswith('.') or item.is_symlink(): continue
            if item.suffix.lower() not in EXTENSIONS:
                unsupported += 1
                continue
            files.append(item)
            if len(files) > limit:
                raise ValueError(f'Choose a smaller folder: the limit is {limit:,} images per run.')
    return path, files, unsupported

def _atomic_save(image, target, **kwargs):
    temporary = target.with_name(target.name + '.partial')
    image.save(temporary, **kwargs)
    os.replace(temporary, target)

class Ingestor:
    def __init__(self, config, db, models):
        self.config, self.db, self.models = config, db, models

    def run(self, folder, progress=None, ocr_backend=None, cancel=None, force=False):
        root, files, unsupported = scan_folder(folder, self.config.max_files)
        began = time.perf_counter()
        report = {'total':len(files), 'processed':0, 'indexed':0, 'updated':0, 'skipped':0,
                  'duplicates':0,'failed':0,'unsupported':unsupported,'warnings':[], 'errors':[], 'elapsed_s':0,'current':'','cancelled':False}
        blocked_embedding = None
        for path in files:
            if cancel and cancel.is_set():
                report['cancelled'] = True
                break
            report['current'] = path.name
            try:
                previous = self.db.by_path(path)
                stat = path.stat()
                if stat.st_size > self.config.max_file_bytes:
                    raise ValueError('Image exceeds the 30 MB limit.')
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if previous and previous['sha256'] == digest and previous['mtime_ns'] == stat.st_mtime_ns and not force:
                    ocr_done = previous['ocr_backend'] not in ('unavailable','off') or (previous['ocr_backend'] == 'off' and (ocr_backend or self.config.ocr) == 'off')
                    if previous['embedding'] and previous['embedding_model'] == EMBEDDING_ID and ocr_done and not previous['ocr_error']:
                        report['skipped'] += 1
                        continue
                with Image.open(path) as opened:
                    if opened.width * opened.height > self.config.max_pixels:
                        raise ValueError('Image exceeds the 40 megapixel limit.')
                    meta = image_metadata(path, opened)
                    image = ImageOps.exif_transpose(opened).convert('RGB')
                meta.update(width=image.width, height=image.height)
                image_name, thumbnail_name = digest + '.png', digest + '.webp'
                full = self.config.data / 'images' / image_name
                thumb = self.config.data / 'thumbs' / thumbnail_name
                # Atomic writes: an interrupted run must never leave a truncated file that is reused forever.
                if not full.exists(): _atomic_save(image, full, format='PNG')
                if not thumb.exists():
                    thumbnail = image.copy(); thumbnail.thumbnail((720,480))
                    _atomic_save(thumbnail, thumb, format='WEBP', quality=84)
                cached = self.db.by_hash(digest)
                vector, vector_error = None, blocked_embedding
                if cached and cached['embedding_model'] == EMBEDDING_ID and cached['embedding'] and not force:
                    vector = cached['embedding']
                    report['duplicates'] += int(cached['filepath'] != str(path))
                elif not blocked_embedding:
                    try:
                        vector = encode_vector(self.models.embed_image(image))
                    except Exception as exc:
                        vector_error = blocked_embedding = str(exc)
                        report['warnings'].append('Semantic indexing unavailable: ' + vector_error)
                if cached and cached['ocr_backend'] not in ('unavailable','off') and not cached['ocr_error'] and not force:
                    ocr = {'text':cached['ocr_text'], 'backend':cached['ocr_backend'], 'error':None}
                else:
                    ocr = self.models.ocr(image, ocr_backend)
                if ocr['error'] and ocr['error'] not in report['warnings']:
                    report['warnings'].append(ocr['error'])
                record = dict(filepath=str(path),filename=path.name,folder=str(root),sha256=digest,
                    indexed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    image_path=image_name,thumbnail_path=thumbnail_name,ocr_text=ocr['text'],
                    ocr_backend=ocr['backend'],ocr_error=ocr['error'],embedding=vector,
                    embedding_model=EMBEDDING_ID if vector else None,embedding_error=vector_error, **meta)
                self.db.upsert(record)
                report['updated' if previous else 'indexed'] += 1
            except Exception as exc:
                report['failed'] += 1
                report['errors'].append({'file':path.name,'message':str(exc)[:500]})
            finally:
                report['processed'] += 1
                report['elapsed_s'] = round(time.perf_counter()-began,2)
                if progress: progress(dict(report))
        if not report['cancelled']:
            seen = {str(p) for p in files}
            missing = [r['filename'] for r in self.db.all(folder=str(root)) if r['filepath'] not in seen and not Path(r['filepath']).exists()]
            report['missing'] = len(missing)
            if missing:
                report['warnings'].append(f'{len(missing)} previously indexed file(s) are no longer in this folder (renamed, moved or deleted); their saved copies stay searchable: ' + ', '.join(missing[:5]))
        report['elapsed_s'] = round(time.perf_counter()-began,2)
        if progress: progress(dict(report))
        return report

