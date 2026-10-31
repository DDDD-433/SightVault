"""Verify locally converted Mac artifacts against the reviewed fingerprints; no downloads."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
def main():
 manifest=json.loads((ROOT/'handoff/MAC_CONVERTED_MODEL_MANIFEST.json').read_text())
 failures=[]
 for item in manifest['files']:
  path=(ROOT/item['path']).resolve()
  if not path.is_relative_to((ROOT/'.models').resolve()):raise ValueError('Model path escapes local model root')
  if not path.is_file() or path.stat().st_size!=item['size']:
   failures.append({'path':item['path'],'reason':'missing or size mismatch'});continue
  with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
  if digest!=item['sha256']:failures.append({'path':item['path'],'reason':'SHA-256 mismatch'})
 report={'checked':len(manifest['files']),'failures':failures,'passed':not failures};print(json.dumps(report,indent=2));return int(bool(failures))
if __name__=='__main__':sys.exit(main())
