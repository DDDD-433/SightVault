from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageFilter
import json,textwrap,math
ROOT=Path(__file__).resolve().parent.parent;WORK=ROOT/'demo_video';ART=WORK/'art';ART.mkdir(exist_ok=True)
BG=(10,14,19);GREEN=(184,245,138);WHITE=(237,241,246);MUTED=(151,164,181);LINE=(38,48,61)
def font(size,bold=False):return ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial'+(' Bold' if bold else '')+'.ttf',size)
def text(d,xy,s,size=32,color=WHITE,bold=False):d.text(xy,s,font=font(size,bold),fill=color)
def background():
 im=Image.new('RGB',(1920,1080),BG)
 glow=Image.new('RGB',im.size,BG);g=ImageDraw.Draw(glow)
 for r in range(720,0,-8):
  a=(1-r/720)*.14;c=tuple(int(BG[i]*(1-a)+GREEN[i]*a) for i in range(3));g.ellipse((1610-r,-100-r,1610+r,-100+r),fill=c)
 return glow

def brand(d):
 d.rounded_rectangle((100,70,152,122),radius=13,fill=GREEN);d.ellipse((110,88,142,106),outline=BG,width=3);d.ellipse((122,91,130,100),fill=BG)
 text(d,(168,78),'SightVault.',42,bold=True);text(d,(1470,88),'SUBMISSION DEMO',20,MUTED)

im=background();d=ImageDraw.Draw(im);brand(d)
text(d,(100,240),'PRIVATE VISUAL MEMORY',24,GREEN,bold=True)
text(d,(100,312),'You remember',96,bold=True);text(d,(100,416),'the image.',96,bold=True)
text(d,(102,554),'Not the filename.',58,MUTED)
d.rounded_rectangle((100,690,675,766),radius=17,fill=(29,43,31),outline=(68,94,58),width=2);text(d,(129,708),'Search what you remember.',32,GREEN,bold=True)
text(d,(100,943),'REAL APP FOOTAGE  /  LOCAL MODELS  /  INSPECTABLE EVIDENCE',20,MUTED)
for idx,(name,box) in enumerate([('terminal_cuda_01.png',(1110,230,1770,650)),('object_study_01.png',(1240,675,1530,875)),('lecture_erd_01.png',(1550,675,1840,875))]):
 x0,y0,x1,y1=box;d.rounded_rectangle((x0-12,y0-12,x1+12,y1+12),radius=18,fill=(24,32,43),outline=LINE,width=2)
 photo=Image.open(ROOT/'samples'/name).convert('RGB');photo.thumbnail((x1-x0,y1-y0));im.paste(photo,(x0+(x1-x0-photo.width)//2,y0+(y1-y0-photo.height)//2))
im.save(ART/'00_hook.png')

im=background();d=ImageDraw.Draw(im);brand(d)
text(d,(100,236),'A prototype you can use.',82,bold=True)
text(d,(102,340),'Native Apple Silicon. Real inference. Verified workflows.',34,MUTED)
for x,big,label,sub in [(100,'123','Passing tests','Regression and edge-case coverage'),(690,'17/17','Demo retrieval cases','Actual models and image embeddings'),(1280,'30','Browser checks','Complete evidence and analysis flow')]:
 d.rounded_rectangle((x,465,x+540,775),radius=25,fill=(19,27,36),outline=LINE,width=2)
 text(d,(x+34,492),big,110,GREEN,bold=True);text(d,(x+36,634),label,34,bold=True);text(d,(x+36,697),sub,22,MUTED)
text(d,(102,856),'+ Normal-CSP edge checks  + Model recovery  + CPU / MPS agreement',28,MUTED)
text(d,(102,956),'Demo validation on Apple M1 Pro · 16 GB unified memory',22,MUTED)
im.save(ART/'08_progress.png')

im=background();d=ImageDraw.Draw(im);brand(d)
text(d,(100,250),'Less searching.',88,bold=True);text(d,(100,359),'More remembering.',88,GREEN,bold=True)
text(d,(102,532),'Private visual memory for students,',33,MUTED);text(d,(102,580),'developers, and screenshot collectors.',33,MUTED)
d.rounded_rectangle((1190,250,1810,730),radius=27,fill=(19,27,36),outline=LINE,width=2)
text(d,(1230,292),'WHERE IT GOES NEXT',22,GREEN,bold=True)
for i,(y,line) in enumerate([(385,'PDF + slide imports'),(490,'Smarter collections'),(595,'Faster bulk indexing')]):
 text(d,(1230,y),f'0{i+1}',27,GREEN,bold=True);text(d,(1300,y-1),line,31,bold=True)
text(d,(102,841),'Remember what you saw.',40,bold=True)
text(d,(102,902),'Find the evidence. Keep it local.',40,GREEN,bold=True)
im.save(ART/'09_close.png')

for s in json.loads((WORK/'timeline.json').read_text()):
 if s['kind']=='card':continue
 over=Image.new('RGBA',(1920,1080),(0,0,0,0));d=ImageDraw.Draw(over)
 d.rectangle((0,0,1920,72),fill=(*BG,255));d.line((0,71,1920,71),fill=(*LINE,255),width=1)
 text(d,(35,19),'SightVault.',29,GREEN,bold=True);text(d,(265,12),s['title'],26,bold=True);text(d,(266,43),s['subtitle'],17,MUTED)
 text(d,(1620,26),'REAL APP FOOTAGE',17,MUTED,bold=True)
 d.rectangle((0,1008,1920,1080),fill=(*BG,255));d.line((0,1008,1920,1008),fill=(*LINE,255),width=1)
 text(d,(37,1030),s['cue'],25,GREEN if s['id']!='04_timeline' else (240,206,131))
 over.save(ART/(s['id']+'_overlay.png'))
print('Created title cards and overlays')
