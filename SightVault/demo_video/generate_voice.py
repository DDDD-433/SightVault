from pathlib import Path
import os,json,numpy as np,soundfile as sf
os.environ['HF_HUB_OFFLINE']='0';os.environ['TRANSFORMERS_OFFLINE']='0'
from huggingface_hub import snapshot_download
from mlx_audio.tts.utils import load_model
ROOT=Path(__file__).resolve().parent.parent
out=ROOT/'exports/submission';work=ROOT/'demo_video';(work/'audio').mkdir(exist_ok=True)
repo='mlx-community/Kokoro-82M-bf16';revision='a71e4d38b236d968966a2002c4c895dbd12b1c3c'
path=snapshot_download(repo,revision=revision,allow_patterns=['config.json','*.safetensors','voices/af_heart.safetensors'])
model=load_model(path, model_type="kokoro")
story=json.loads((work/'story.json').read_text());start=0
for scene in story:
 target=work/'audio'/(scene['id']+'.wav')
 if not target.exists():
  chunks=[]
  for r in model.generate(scene['narration'],voice='af_heart',speed=1.0,lang_code='a'):
   chunks.append(np.asarray(r.audio,dtype=np.float32).reshape(-1))
  sf.write(target,np.concatenate(chunks),24000)
 info=sf.info(target);scene['audio_seconds']=info.duration
 scene['duration']=max(scene['min_seconds'],round(info.duration+1.8,2))
 scene['start']=round(start,2);start+=scene['duration']
 print(scene['id'],round(info.duration,2),scene['duration'],flush=True)
(work/'timeline.json').write_text(json.dumps(story,indent=2))
(work/'voice_source.json').write_text(json.dumps({'model':repo,'revision':revision,'voice':'af_heart','runtime':'mlx-audio','speed':1.0,'local_synthesis':True,'total_seconds':start},indent=2))
print('Total duration',start,flush=True)
