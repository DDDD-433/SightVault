"""Regression for upgraded UI caches and missing portable static assets."""
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from fastapi.testclient import TestClient
from scripts.build_portable_package import collect_source_files
from sightvault.server import create_app
from test_core import env

ROOT = Path(__file__).resolve().parents[1]

def test_frontend_cache_keys_match_served_bytes_and_assets_ship(env):
    config, _, models = env
    selected = collect_source_files(ROOT, ROOT/'packaging/PORTABLE_SOURCE_ALLOWLIST.json',
                                    ROOT/'packaging/EMPTY_QA_ALLOWLIST.json')
    with TestClient(create_app(config, models)) as client:
        html = client.get('/').text
        urls = re.findall(r'(?:href|src)="(/static/[^\"]+)"', html)
        assert len(urls) >= 3
        for url in urls:
            parsed = urlsplit(url)
            relative = 'sightvault/web/' + parsed.path.removeprefix('/static/')
            assert relative in selected, f'Asset omitted from portable bundle: {relative}'
            response = client.get(url)
            assert response.status_code == 200
            if parsed.path.endswith(('.css', '.js')):
                # A reused WebView must get a new asset URL when the bytes change.
                assert parse_qs(parsed.query).get('v') == [hashlib.sha256(response.content).hexdigest()[:12]]
