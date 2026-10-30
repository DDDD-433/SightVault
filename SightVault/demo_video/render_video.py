from pathlib import Path
import json,subprocess,numpy as np,soundfile as sf,re,textwrap
from fractions import Fraction
ROOT=Path(__file__).resolve().parent.parent;WORK=ROOT/'demo_video';OUT=ROOT/'exports/submission';CLIPS=WORK/'clips';CLIPS.mkdir(exist_ok=True)
record=json.loads((WORK/'recording.json').read_text());raw=record['video'];story=json.loads((WORK/'timeline.json').read_text())
def run(args,**kw):return subprocess.run(args,check=True,**kw)
def ff(args):
 with (WORK/'render.log').open('a') as log:run(['ffmpeg','-hide_banner','-y',*args],stdout=log,stderr=log)
probe=json.loads(subprocess.check_output(['ffprobe','-v','quiet','-show_streams','-of','json',raw]));fps=float(Fraction(probe['streams'][0]['avg_frame_rate']))
pixels=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',raw,'-vf','crop=2:2:0:0,scale=1:1:flags=neighbor','-vsync','0','-f','rawvideo','-pix_fmt','rgb24','pipe:1']),dtype=np.uint8).reshape(-1,3)
mask=(pixels[:,0]>35)&(pixels[:,1]<15)&(pixels[:,2]>35)&(np.abs(pixels[:,0].astype(int)-pixels[:,2].astype(int))<25);edges=np.diff(np.r_[False,mask,False].astype(int));runs=[(a,b) for a,b in zip(np.where(edges==1)[0],np.where(edges==-1)[0]) if b-a>=3]
screens=[s for s in story if s['kind']=='screen'];assert len(runs)==2*len(screens),(fps,runs)
for i,s in enumerate(screens):s['source_start']=runs[2*i][1]/fps;s['source_end']=runs[2*i+1][0]/fps
start=0
for s in story:
 duration=round(s['duration']*30)/30;s['render_duration']=duration;s['render_start']=start;start+=duration;target=CLIPS/(s['id']+'.mp4')
 if target.exists():print('cached',s['id'],flush=True);continue
 fade=f'fade=t=in:st=0:d=0.18,fade=t=out:st={duration-.22}:d=0.22,format=yuv420p'
 if s['kind']=='card':
  filt=f"zoompan=z='1+0.008*on/{max(1,int(duration*30)-1)}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=1920x1080:fps=30,{fade}"
  ff(['-loop','1','-framerate','30','-i',str(WORK/'art'/(s['id']+'.png')),'-vf',filt,'-t',str(duration),'-an','-c:v','libx264','-preset','veryfast','-crf','18','-threads','4','-pix_fmt','yuv420p','-r','30',str(target)])
 else:
  assert s['source_end']-s['source_start']>=duration-.18,(s['id'],s['source_end']-s['source_start'],duration)
  crop='crop=1440:702:80:39,' if s['id'] in ('03_exact','05_analysis') else ('crop=900:438:0:342,' if s['id']=='07_private' else '')
  filters=f'[0:v]{crop}scale=1920:936:flags=lanczos,fps=30,pad=1920:1080:0:72:color=0x0a0e13[screen];[screen][1:v]overlay=0:0,{fade}[v]'
  ff(['-ss',str(s['source_start']),'-i',raw,'-loop','1','-framerate','30','-i',str(WORK/'art'/(s['id']+'_overlay.png')),'-filter_complex',filters,'-map','[v]','-t',str(duration),'-an','-c:v','libx264','-preset','veryfast','-crf','18','-threads','4','-pix_fmt','yuv420p','-r','30',str(target)])
 print('Rendered',s['id'],duration,flush=True)
(WORK/'edit_timeline.json').write_text(json.dumps(story,indent=2));total=start
concat=WORK/'clips.ffconcat';concat.write_text('ffconcat version 1.0\n'+''.join("file '"+str(CLIPS/(s['id']+'.mp4'))+"'\n" for s in story))
master=OUT/'SightVault_No_Voiceover.mp4';ff(['-f','concat','-safe','0','-i',str(concat),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart','-metadata','title=SightVault submission demo - clean voiceover version',str(master)])
track=np.zeros(round(total*24000),dtype=np.float32)
for s in story:
 audio,sr=sf.read(WORK/'audio'/(s['id']+'.wav'),dtype='float32');assert sr==24000
 offset=round((s['render_start']+.65)*sr);assert offset+len(audio)<len(track)
 track[offset:offset+len(audio)]=audio
sf.write(WORK/'narration_aligned.wav',track,24000,subtype='PCM_24')
cmd=['ffmpeg','-hide_banner','-i',str(WORK/'narration_aligned.wav'),'-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-']
r=subprocess.run(cmd,capture_output=True,text=True,check=True);stats=json.JSONDecoder().raw_decode(r.stderr[r.stderr.rfind('{'):])[0];(WORK/'audio-loudness.json').write_text(json.dumps(stats,indent=2))
normal=f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true"
voice=OUT/'SightVault_AI_Narration.wav';ff(['-i',str(WORK/'narration_aligned.wav'),'-af',normal,'-ar','48000','-c:a','pcm_s24le',str(voice)])
ff(['-i',str(master),'-i',str(voice),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-t',str(total),'-movflags','+faststart','-metadata','title=SightVault submission demo - AI voiceover',str(OUT/'SightVault_AI_Voiceover.mp4')])
def stamp(t):
 ms=round(t*1000);return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
srt=[];i=1
for s in story:
 words=s['narration'].split();chunks=[' '.join(words[j:j+12]) for j in range(0,len(words),12)];cursor=s['render_start']+.65
 for chunk in chunks:
  dur=s['audio_seconds']*len(chunk.split())/len(words)
  srt.append(f'{i}\n{stamp(cursor)} --> {stamp(cursor+dur)}\n'+textwrap.fill(chunk,width=60)+'\n');i+=1;cursor+=dur
(OUT/'SightVault_Captions.srt').write_text('\n'.join(srt))
lines=['# SightVault — timed voiceover script','',f'Picture edit: {total:.2f} seconds. The clean video has no audio track. The AI version uses this same picture edit.','', 'Read at a natural, clear pace. Start each scene about 0.65 seconds after its cue. The text can be adapted by your teammate; keep the synthetic-date qualification and describe roadmap items as future work.','']
for s in story:
 lines += [f"**{stamp(s['render_start']).replace(',','.')[:-4]} – {stamp(s['render_start']+s['render_duration']).replace(',','.')[:-4]} / {s['title']}**",'',s['narration'],'']
(OUT/'SightVault_Timed_Script.md').write_text('\n'.join(lines));(OUT/'Video_Production.json').write_text(json.dumps({'duration_seconds':total,'width':1920,'height':1080,'fps':30,'voice':json.loads((WORK/'voice_source.json').read_text()),'footage':'Real localhost SightVault browser capture; trim markers removed; no mocked responses','edit_timeline':story},indent=2))
print('DELIVERED',total,flush=True)
