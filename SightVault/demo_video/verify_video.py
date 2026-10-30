from pathlib import Path
import subprocess,json,hashlib,io
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parent.parent;OUT=ROOT/'exports/submission';WORK=ROOT/'demo_video'
files=['SightVault_AI_Voiceover.mp4','SightVault_No_Voiceover.mp4'];report={'files':{},'checks':{}}
for name in files:
 p=OUT/name;r=json.loads(subprocess.check_output(['ffprobe','-v','quiet','-show_format','-show_streams','-of','json',str(p)]));v=next(s for s in r['streams'] if s['codec_type']=='video');a=[s for s in r['streams'] if s['codec_type']=='audio']
 assert v['width']==1920 and v['height']==1080 and v['codec_name']=='h264' and v['avg_frame_rate']=='30/1'
 assert 120<=float(r['format']['duration'])<=180
 if 'No_Voiceover' in name:assert not a
 else:assert len(a)==1 and a[0]['codec_name']=='aac' and a[0]['sample_rate']=='48000'
 subprocess.run(['ffmpeg','-v','error','-i',str(p),'-f','null','-'],check=True,capture_output=True)
 h=subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-map','0:v:0','-c:v','copy','-f','hash','-hash','sha256','-']).decode().strip()
 report['files'][name]={'duration_seconds':float(r['format']['duration']),'bytes':p.stat().st_size,'resolution':[v['width'],v['height']],'fps':30,'audio_tracks':len(a),'video_payload_hash':h,'sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest()}
report['checks']['identical_picture_edit']=len({r['video_payload_hash'] for r in report['files'].values()})==1
assert report['checks']['identical_picture_edit']
# The exact corner that detected trim markers in the source should never be purple in the edit.
raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(OUT/files[1]),'-vf','crop=2:2:4:76,scale=1:1:flags=neighbor','-vsync','0','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
p=np.frombuffer(raw,np.uint8).reshape(-1,3);bad=(p[:,0]>35)&(p[:,1]<15)&(p[:,2]>35)&(np.abs(p[:,0].astype(int)-p[:,2].astype(int))<25)
report['checks']['trim_markers_removed']=int(bad.sum())==0;assert not bad.any(),int(bad.sum())
run=subprocess.run(['ffmpeg','-hide_banner','-i',str(OUT/files[0]),'-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'],capture_output=True,text=True,check=True)
loud=json.JSONDecoder().raw_decode(run.stderr[run.stderr.rfind('{'):])[0];report['audio_loudness']=loud
assert -17.5<float(loud['input_i'])<-14.5 and float(loud['input_tp'])<-.3
report['checks']['narration_level_checked']=True
story=json.loads((WORK/'edit_timeline.json').read_text());contact=Image.new('RGB',(1280,1900),'#0a0e13');d=ImageDraw.Draw(contact);font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',19)
for i,s in enumerate(story):
 t=s['render_start']+s['render_duration']*.62
 buf=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(OUT/files[0]),'-frames:v','1','-f','image2pipe','-vcodec','png','-'])
 image=Image.open(io.BytesIO(buf));image.thumbnail((640,360));x=(i%2)*640;y=(i//2)*380;contact.paste(image,(x,y));d.text((x+10,y+360),s['id']+' / '+f'{t:.1f}s',font=font,fill='white')
 image.save(WORK/(s['id']+'_encoded.png')) if i in (5,7) else None
contact.save(WORK/'qa_contact_sheet.jpg',quality=95)
report['checks']['decoder_checks']=True;report['passed']=all(report['checks'].values())
(OUT/'Video_QA.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
