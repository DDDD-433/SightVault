from contextlib import contextmanager
from pathlib import Path
import sqlite3
import numpy as np

class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    created_at TEXT,
                    modified_at TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    timestamp_source TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    image_path TEXT NOT NULL,
                    thumbnail_path TEXT NOT NULL,
                    ocr_text TEXT NOT NULL DEFAULT '',
                    ocr_backend TEXT NOT NULL DEFAULT 'unavailable',
                    ocr_error TEXT,
                    embedding BLOB,
                    embedding_model TEXT,
                    embedding_error TEXT
                );
                CREATE INDEX IF NOT EXISTS assets_hash ON assets(sha256);
                CREATE INDEX IF NOT EXISTS assets_timestamp ON assets(timestamp);
                CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
                    filename, ocr_text, content='assets', content_rowid='id',
                    tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS assets_ai AFTER INSERT ON assets BEGIN
                    INSERT INTO assets_fts(rowid, filename, ocr_text) VALUES (new.id,new.filename,new.ocr_text);
                END;
                CREATE TRIGGER IF NOT EXISTS assets_ad AFTER DELETE ON assets BEGIN
                    INSERT INTO assets_fts(assets_fts,rowid,filename,ocr_text) VALUES ('delete',old.id,old.filename,old.ocr_text);
                END;
                CREATE TRIGGER IF NOT EXISTS assets_au AFTER UPDATE ON assets BEGIN
                    INSERT INTO assets_fts(assets_fts,rowid,filename,ocr_text) VALUES ('delete',old.id,old.filename,old.ocr_text);
                    INSERT INTO assets_fts(rowid,filename,ocr_text) VALUES (new.id,new.filename,new.ocr_text);
                END;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def upsert(self, record):
        fields = list(record)
        sql = f"INSERT INTO assets ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)}) ON CONFLICT(filepath) DO UPDATE SET "
        sql += ','.join(f'{name}=excluded.{name}' for name in fields if name != 'filepath')
        with self.connect() as db:
            db.execute(sql, [record[x] for x in fields])
            return db.execute('SELECT id FROM assets WHERE filepath=?', (record['filepath'],)).fetchone()[0]

    def get(self, asset_id):
        # SQLite INTEGER bindings are signed 64-bit; larger IDs cannot exist.
        if not -(2**63) <= asset_id < 2**63:
            return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
            return dict(row) if row else None

    def by_path(self, path):
        with self.connect() as db:
            row = db.execute('SELECT * FROM assets WHERE filepath=?', (str(path),)).fetchone()
            return dict(row) if row else None

    def by_hash(self, digest):
        with self.connect() as db:
            row = db.execute('SELECT * FROM assets WHERE sha256=? ORDER BY id LIMIT 1', (digest,)).fetchone()
            return dict(row) if row else None

    def all(self, start=None, end=None, folder=None):
        where, args = [], []
        if start:
            where.append('timestamp>=?'); args.append(start)
        if end:
            where.append('timestamp<?'); args.append(end)
        if folder:
            where.append('folder=?'); args.append(folder)
        query = 'SELECT * FROM assets' + (' WHERE ' + ' AND '.join(where) if where else '')
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, args)]

    def fts(self, terms):
        if not terms:
            return {}
        query = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
        with self.connect() as db:
            rows = db.execute('SELECT rowid,bm25(assets_fts,0.3,1.0) AS score FROM assets_fts WHERE assets_fts MATCH ? ORDER BY score', (query,))
            return {row['rowid']: -row['score'] for row in rows}

    def stats(self):
        with self.connect() as db:
            total = db.execute('SELECT count(*) FROM assets').fetchone()[0]
            vectors = db.execute('SELECT count(*) FROM assets WHERE embedding IS NOT NULL').fetchone()[0]
            text = db.execute("SELECT count(*) FROM assets WHERE ocr_text != ''").fetchone()[0]
            folders = [dict(row) for row in db.execute('SELECT folder,count(*) AS count FROM assets GROUP BY folder')]
        return {'total':total, 'with_embeddings':vectors, 'with_ocr':text, 'folders':folders}

def encode_vector(vector):
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not np.isfinite(array).all() or np.linalg.norm(array) < 1e-8:
        raise ValueError('The embedding contains invalid values.')
    return (array / np.linalg.norm(array)).astype('<f4').tobytes()

def decode_vector(blob):
    return np.frombuffer(blob, dtype='<f4')

