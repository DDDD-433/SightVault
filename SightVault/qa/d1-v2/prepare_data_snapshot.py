#!/usr/bin/env python3
"""Create an experiment-owned SQLite backup and asset copy without touching the live DB."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--source-data", required=True, type=Path)
    parser.add_argument("--source-samples", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--provenance-out", required=True, type=Path)
    args = parser.parse_args()

    source_db = args.source_db.expanduser().resolve()
    source_data = args.source_data.expanduser().resolve()
    samples = args.source_samples.expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    provenance_out = args.provenance_out.expanduser().resolve()
    if not source_db.is_file() or not source_data.is_dir() or not samples.is_dir():
        raise SystemExit("Source database, data directory, or samples directory does not exist")
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty experiment destination: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("images", "thumbs", "sources"):
        (destination / name).mkdir(exist_ok=True)

    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    dst_path = destination / "sightvault.sqlite3"
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
        dst.row_factory = sqlite3.Row
        rows = dst.execute(
            "SELECT id, filepath, filename, folder, sha256, image_path, thumbnail_path, "
            "embedding, embedding_model, created_at, modified_at, timestamp, timestamp_source, indexed_at, "
            "mtime_ns, size FROM assets ORDER BY id"
        ).fetchall()
        if len(rows) != 62:
            raise SystemExit(f"Expected 62 frozen baseline assets, found {len(rows)}")
        expected_hashes: dict[str, str] = {}
        vector_fingerprint = hashlib.sha256()
        for row in rows:
            filename = str(row["filename"])
            source_image = samples / filename
            if not source_image.is_file() or sha256(source_image) != row["sha256"]:
                raise SystemExit(f"Sample image does not match indexed asset {filename}")
            copied_source = destination / "sources" / filename
            shutil.copy2(source_image, copied_source)
            expected_hashes[filename] = row["sha256"]

            for kind, relative in (("images", row["image_path"]), ("thumbs", row["thumbnail_path"])):
                source_asset = source_data / kind / str(relative)
                copied_asset = destination / kind / str(relative)
                if not source_asset.is_file():
                    raise SystemExit(f"Indexed {kind} asset is missing: {source_asset}")
                copied_asset.parent.mkdir(parents=True, exist_ok=True)
                if not copied_asset.exists():
                    shutil.copy2(source_asset, copied_asset)

            embedding = bytes(row["embedding"] or b"")
            vector_fingerprint.update(str(row["id"]).encode())
            vector_fingerprint.update(embedding)

        # Rebase only paths in the experiment-owned backup. Preserve embeddings,
        # dimensions, source hashes, and every indexed/creation/modification time.
        dst.executemany(
            "UPDATE assets SET filepath=?, folder=? WHERE id=?",
            [
                (str(destination / "sources" / str(row["filename"])), str(destination / "sources"), row["id"])
                for row in rows
            ],
        )
        dst.commit()
        checks = dst.execute(
            "SELECT COUNT(*), SUM(CASE WHEN embedding_model='google/embeddinggemma-2:768:text-image' "
            "AND length(embedding)=3072 THEN 1 ELSE 0 END) FROM assets"
        ).fetchone()
        if tuple(checks) != (62, 62):
            raise SystemExit(f"Snapshot validation failed: {tuple(checks)}")
    finally:
        dst.close()
        src.close()

    provenance = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_database": str(source_db),
        "source_data_directory": str(source_data),
        "source_samples_directory": str(samples),
        "destination": str(destination),
        "sqlite_backup_api_used": True,
        "source_database_wal_read_via_backup_api": True,
        "asset_count": len(rows),
        "unique_stored_image_files": len(list((destination / "images").glob("*"))),
        "unique_stored_thumbnail_files": len(list((destination / "thumbs").glob("*"))),
        "source_file_hashes": expected_hashes,
        "embedding_model": "google/embeddinggemma-2:768:text-image",
        "embedding_dimension": 768,
        "embedding_bytes_per_row": 3072,
        "embedding_rows_fingerprint_sha256": vector_fingerprint.hexdigest(),
        "rebased_fields": ["filepath", "folder"],
        "preserved_fields": ["embedding", "embedding_model", "created_at", "modified_at", "timestamp", "timestamp_source", "indexed_at", "mtime_ns", "size"],
        "snapshot_database_sha256": sha256(dst_path),
    }
    provenance_out.parent.mkdir(parents=True, exist_ok=True)
    provenance_out.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"destination": str(destination), "asset_count": len(rows), "database_sha256": provenance["snapshot_database_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
