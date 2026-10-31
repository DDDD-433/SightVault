"""Serial real-model acceptance for selected optional providers; offline setup required."""
from pathlib import Path
import argparse,json,sys,time,os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from sightvault.config import Config
from sightvault.models import Models,ModelUnavailable
from sightvault.database import Database
from sightvault.search import Search

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 c=Config();m=Models(c);s=Search(Database(c.db_path),m);image=Image.open(c.root/'samples/terminal_cuda_01.png').convert('RGB')
 report={'backend':c.vision,'reasoning_mode':m.reasoning_mode,'checks':{},'calls':[],'isolation':os.environ.get('SIGHTVAULT_QA_ISOLATION','not externally specified')}
 def search_check(key):
  r=s.run('GPU memory problem',mode='semantic');report['checks'][key]=r['results'][0]['filename'].startswith('terminal_cuda');return r
 def analyze(key):
  t=time.perf_counter();answer=m.analyze(image,'What visible error is shown? Separate the evidence from likely causes.','CUDA out of memory')
  report['calls'].append({'kind':key,'seconds':time.perf_counter()-t,'answer':answer,'status':m.status()['vision'],'generation':dict(m.last_vision_generation)})
  report['checks'][key]=bool('memory' in answer.lower() and m.status()['vision']['loaded'])
 def ocr(key):
  t=time.perf_counter();r=m.ocr(image,'minicpm');report['calls'].append({'kind':key,'seconds':time.perf_counter()-t,'result':r,'status':m.status()['ocr']});report['checks'][key]=r['backend']=='MiniCPM-OCR 1.0' and 'CUDA' in r['text']
 try:
  search_check('semantic_before');ocr('ocr_before');analyze('analysis_first');analyze('analysis_reuse');search_check('semantic_during_loaded_provider')
  if c.vision=='lfm3' and c.lfm3_thinking_tokens:
   report['checks']['bounded_thinking_metadata']=all(r['generation'].get('usable_final_answer') and r['generation'].get('thinking_tokens_observed',999)<=128 and r['generation'].get('final_answer_tokens',999)<=384 for r in report['calls'] if r['kind'].startswith('analysis'))
  if c.vision=='omni':
   runtime=m._omni;runtime.close();binary=runtime.binary;runtime.binary=binary.with_name('intentionally-missing-QA-binary')
   try:
    m.analyze(image,'Describe the error.')
    report['checks']['missing_optional_runtime_fails']=False
   except ModelUnavailable as exc:
    report['checks']['missing_optional_runtime_fails']=True;report['injected_error']=str(exc)
   search_check('semantic_after_optional_failure');runtime.binary=binary;analyze('analysis_recovered')
  ocr('ocr_after');report['checks']['single_generator_slot']=not m.status()['vision']['loaded'] and m.status()['ocr']['loaded']
  search_check('semantic_after_switch');report['models']=m.status()
 finally:
  m.close()
 report['checks']['owned_runtime_stopped']=m._omni is None
 report['passed']=all(report['checks'].values());a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'checks':report['checks']}));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
