from pathlib import Path
import hashlib,json
from datetime import datetime,timezone
from zipfile import ZipFile,ZIP_DEFLATED

root=Path(__file__).resolve().parent.parent
out=root/'exports'/'SightVault_Source_Demo.zip';out.parent.mkdir(exist_ok=True)
files=[root/name for name in ['app.py','requirements.txt','Start-SightVault.cmd','README.md','DEMO.md','QA_REPORT.md','QA_RESULTS.json','OFFLINE_VERIFICATION.json','.gitignore','AGENTS.md','READ_ME_FIRST.md','Setup-Mac.sh','Start-SightVault.command','requirements-mac.lock.txt','MAC_QA_REPORT.md']]
# Only reviewed, public QA artifacts belong in a distributable archive.
# Raw agent logs, session/account metadata and failed exploratory probes stay local.
files.extend(p for p in (root/'qa').rglob('*') if p.is_file() and p.name in {
    'MAC_MODEL_MANIFEST.json','MAC_MODEL_VERIFICATION.json','MAC_OFFLINE_VERIFICATION.json',
    'MAC_QA_RESULTS.json','MAC_BROWSER_QA.json','MAC_CSP_BROWSER_QA.json','MAC_XSS_QA.json',
    'MAC_DEMO_INTEGRITY.json','MAC_FINAL_LAUNCH.json','MAC_INGESTION_VALIDATION.json',
    'MAC_PACKAGE_VERIFICATION.json','MAC_SWITCHING.json','mac_ingestion_qa.json',
    'mac-cpu-embedding.json','mac-mps-embedding.json','mac-ocr-smoke.json',
    'AGENT_COORDINATION.md','CODEX_REPORT.md','OPUS_REPORT.md','SONNET_REPORT.md',
    'FINAL_QA_REPORT.md','CLAUDE_QA_SUMMARY.json','retrieval.json','concurrency.json',
    'cross-device.json','model-recovery.json','browser-races.json','alpha.json','retrieval-final.json','package-audit.json',
    'mac-unit-tests.log','tests-final.log','mac-setup.log','mac-final-launch.log'
})
for folder in ['sightvault','scripts','tests','samples','handoff']:
    files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
# Export only the verified public demo using SQLite backup; omit private indexes and journals.
import sqlite3, tempfile
with sqlite3.connect(root/'data/sightvault.sqlite3') as db:
    rows=db.execute('SELECT filename,filepath,image_path,thumbnail_path,sha256 FROM assets').fetchall()
    assert len(rows)==62 and all(Path(path).parent==root/'samples' for _,path,_,_,_ in rows), 'Refuse to package a non-demo database'
    sample_manifest = {Path(m['path']).name:m for m in json.loads((root/'handoff/SAMPLE_MANIFEST.json').read_text())}
    for name,path,image,thumb,digest in rows:
        expected = sample_manifest.get(name)
        assert expected and digest == expected['sha256'], 'Refuse to package an unknown demo asset'
        assert hashlib.file_digest(Path(path).open('rb'),'sha256').hexdigest() == digest, 'Demo source has changed'
        assert image == digest+'.png' and thumb == digest+'.webp', 'Invalid demo evidence filename'
    stage=Path(tempfile.mkdtemp(prefix='sightvault-package-'))/'sightvault.sqlite3'
    with sqlite3.connect(stage) as destination: db.backup(destination)
files.extend(root/'data'/folder/name for _,_,image,thumb,_ in rows for folder,name in [('images',image),('thumbs',thumb)])
entries=[]
files.extend(p for p in (root/'output/playwright').rglob('*') if p.is_file() and p.suffix in ('.png','.json'))
files=sorted(set(files))
with ZipFile(out,'w',ZIP_DEFLATED) as archive:
    archive.write(stage, 'SightVault/data/sightvault.sqlite3')
    entries.append({'path':'data/sightvault.sqlite3','size':stage.stat().st_size,'sha256':hashlib.file_digest(stage.open('rb'),'sha256').hexdigest()})
    for file in files:
        if file.exists() and file != root/'handoff/PACKAGE_MANIFEST.json':
            relative=file.relative_to(root)
            archive.write(file,Path('SightVault')/relative)
            entries.append({'path':str(relative),'size':file.stat().st_size,'sha256':hashlib.file_digest(file.open('rb'),'sha256').hexdigest()})
    archive.writestr('SightVault/handoff/PACKAGE_MANIFEST.json',json.dumps({'created_at_utc':datetime.now(timezone.utc).isoformat(),'files':entries},indent=2))
with ZipFile(out) as archive:
    assert archive.testzip() is None
    print(f'{out}: {len(archive.namelist())} files, {out.stat().st_size:,} bytes')

stage.unlink();stage.parent.rmdir()
