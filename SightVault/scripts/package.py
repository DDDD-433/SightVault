from pathlib import Path
import hashlib,json
from datetime import datetime,timezone
from zipfile import ZipFile,ZIP_DEFLATED

root=Path(__file__).resolve().parent.parent
out=root/'exports'/'SightVault_Source_Demo.zip';out.parent.mkdir(exist_ok=True)
files=[root/name for name in ['app.py','requirements.txt','Start-SightVault.cmd','README.md','DEMO.md','QA_REPORT.md','QA_RESULTS.json','OFFLINE_VERIFICATION.json','.gitignore','AGENTS.md','READ_ME_FIRST.md','Setup-Mac.sh','Start-SightVault.command','requirements-mac.lock.txt','MAC_QA_REPORT.md']]
files.extend(p for p in (root/'qa').rglob('*') if p.is_file() and p.suffix in ('.json','.log','.md') and 'screenshots' not in p.parts and p.name != 'ingestion_qa.json')  # QA evidence; screenshots/private data excluded
for folder in ['sightvault','scripts','tests','samples','handoff']:
    files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
# Export only the verified public demo using SQLite backup; omit private indexes and journals.
import sqlite3, tempfile
with sqlite3.connect(root/'data/sightvault.sqlite3') as db:
    rows=db.execute('SELECT filename,filepath,image_path,thumbnail_path FROM assets').fetchall()
    assert len(rows)==62 and all(Path(path).parent==root/'samples' for _,path,_,_ in rows), 'Refuse to package a non-demo database'
    stage=Path(tempfile.mkdtemp(prefix='sightvault-package-'))/'sightvault.sqlite3'
    with sqlite3.connect(stage) as destination: db.backup(destination)
files.extend(root/'data'/folder/name for _,_,image,thumb in rows for folder,name in [('images',image),('thumbs',thumb)])
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
