from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

root=Path(__file__).resolve().parent.parent
out=root/'exports'/'SightVault_Source_Demo.zip';out.parent.mkdir(exist_ok=True)
files=[root/name for name in ['app.py','requirements.txt','Start-SightVault.cmd','README.md','DEMO.md','QA_REPORT.md','QA_RESULTS.json','OFFLINE_VERIFICATION.json','.gitignore']]
files.extend(p for p in (root/'qa').glob('*') if p.is_file() and p.suffix in ('.json','.log'))  # QA evidence; screenshots/private data excluded
for folder in ['sightvault','scripts','tests','samples']:
    files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
with ZipFile(out,'w',ZIP_DEFLATED) as archive:
    for file in files:
        if file.exists():archive.write(file,Path('SightVault')/file.relative_to(root))
with ZipFile(out) as archive:
    assert archive.testzip() is None
    print(f'{out}: {len(archive.namelist())} files, {out.stat().st_size:,} bytes')
