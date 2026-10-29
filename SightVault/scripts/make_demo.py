"""Create 60 explicitly synthetic screenshots, documents and object illustrations.

The indexer reads only image pixels and file metadata. Labels in manifest.json
are used by the benchmark, never by ingestion or retrieval.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import json, os, sys
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'samples'
W,H=1100,700

def font(size,mono=False,bold=False):
    windows=Path('C:/Windows/Fonts')
    choices=[windows/('consola.ttf' if mono else 'segoeuib.ttf' if bold else 'segoeui.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    for file in choices:
        if file.exists(): return ImageFont.truetype(str(file),size)
    return ImageFont.load_default(size=size)

def draw_text(d,xy,text,size=24,color='#202633',mono=False,bold=False,spacing=12):
    d.multiline_text(xy,text,font=font(size,mono,bold),fill=color,spacing=spacing)

def frame(title,dark=False):
    im=Image.new('RGB',(W,H),'#111824' if dark else '#f5f3ee');d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,54),fill='#222c3c' if dark else '#e5e3df')
    for x,c in [(24,'#ed6b60'),(47,'#f4bf4f'),(70,'#62c554')]:d.ellipse((x,19,x+12,31),fill=c)
    draw_text(d,(110,15),title,16,'#c5cfde' if dark else '#4d5664')
    draw_text(d,(35,H-32),'SYNTHETIC DEMO / no personal information',13,'#758297')
    return im,d

def terminal(kind,variant):
    scenarios={
      'cuda':('training.py — Python',[
          '$ python train.py --batch-size 64','Epoch 1/10 | training on cuda:0','',
          'Traceback (most recent call last):','  File "train.py", line 87, in train','    loss.backward()',
          'torch.OutOfMemoryError: CUDA out of memory.',
          f'Tried to allocate {512+variant*128}.00 MiB. GPU 0 has 12.00 GiB total.',
          'Of which 96.00 MiB is free. Process holds 11.62 GiB.',
          'Batch size: 64. Sequence length: 4096.']),
      'eresolve':('project — npm install',[
          '$ npm install','npm ERR! code ERESOLVE','npm ERR! ERESOLVE unable to resolve dependency tree','',
          'While resolving: dashboard@1.0.0','Found: react@19.0.0','Could not resolve dependency:',
          'peer react@"^18.0.0" from legacy-widget@2.3.1','',
          'Fix the upstream dependency conflict,','or choose compatible package versions.']),
      'module':('Python — import failed',[
          '$ python app.py','Traceback (most recent call last):','  File "app.py", line 2, in <module>',
          '    import pandas','ModuleNotFoundError: No module named \'pandas\'','',
          'Python 3.12 | virtual environment: .venv','Check the selected interpreter and dependencies.']),
      'database':('MongoDB — write failed',[
          '$ node seed.js','Connected to database: inventory','MongoServerError: E11000 duplicate key error',
          'collection: inventory.products index: sku_1','dup key: { sku: "KB-104" }','',
          'A document with this unique identifier already exists.']),
      'success':('training.py — completed',[
          '$ python train.py --batch-size 4','Epoch 10/10 | loss: 0.021','Validation accuracy: 94.2%',
          'Training completed successfully.','Checkpoint saved to ./runs/experiment-04','',
          'Device: GPU | Peak memory: 5.8 GiB','Batch size: 4 | Gradient accumulation: 16'])}
    title,lines=scenarios[kind];im,d=frame(title,True)
    for n,line in enumerate(lines):
        color='#f2a6a0' if any(s in line for s in ['Error','ERR!','out of memory']) else '#bce3ad' if 'success' in line or line.startswith('$') else '#c4cfdf'
        draw_text(d,(40,90+n*44),line,22,color,mono=True)
    draw_text(d,(40,620),f'Session {variant+1:02d} · developer workstation',15,'#708199')
    return im

def slide(topic,variant):
    titles={
      'normalization':('Database normalization',['First normal form: atomic values','Second normal form: no partial dependencies','Third normal form: no transitive dependencies','Goal: reduce redundancy and update anomalies']),
      'erd':('Entity relationships',['Customer places Order','Order contains Product','One customer may place many orders','Primary keys identify each record']),
      'joins':('Relational algebra and joins',['INNER JOIN: only matching rows','LEFT JOIN: keep every row on the left','A foreign key connects related tables','SELECT customers.name, orders.total']),
      'physics':('Conservation of momentum',['Momentum = mass x velocity','Total momentum is conserved in a closed system','Elastic collisions conserve kinetic energy','Measure initial and final velocities']),
      'biology':('Photosynthesis',['Chloroplasts convert light into chemical energy','Carbon dioxide + water -> glucose + oxygen','Chlorophyll absorbs red and blue wavelengths','Light-dependent reactions occur in thylakoids']),
      'economics':('Supply, demand and equilibrium',['Demand slopes downward','Supply slopes upward','Equilibrium occurs where supply meets demand','A shortage puts upward pressure on price'])}
    title,bullets=titles[topic];im,d=frame('Lecture notes / Week '+str(4+variant))
    d.rectangle((0,54,17,H),fill='#516cca')
    draw_text(d,(65,94),title,43,'#233555',bold=True)
    draw_text(d,(67,158),'CONCEPTS AND WORKED EXAMPLES',15,'#7b88a1')
    for n,line in enumerate(bullets):
        d.ellipse((70,230+n*70,80,240+n*70),fill='#516cca');draw_text(d,(100,217+n*70),line,26)
    d.rounded_rectangle((65,530,1025,620),radius=12,fill='#e6eaf5')
    draw_text(d,(88,557),'Discussion: explain the idea with a concrete example.',25,'#485b88')
    return im

def receipt(item,variant):
    im=Image.new('RGB',(W,H),'#d8d4cb');d=ImageDraw.Draw(im)
    d.rectangle((295,25,805,675),fill='#fffef8');draw_text(d,(350,56),'CAMPUS CORNER',34,bold=True)
    draw_text(d,(350,110),'SYNTHETIC DEMO RECEIPT\n123 Example Lane\nTransaction #'+str(4200+variant),19,mono=True)
    d.line((335,230,765,230),fill='#6b6b60',width=2)
    prices={'Mechanical keyboard':79.99,'Coffee and muffin':7.50,'USB-C charger':24.95,'Notebook and pens':9.20,'Badminton shuttlecocks':18.00,'Sandwich and juice':12.30}
    price=prices[item]
    draw_text(d,(335,263),f'{item}\nQty: 1\n\nSubtotal:     ${price:.2f}\nTax:          ${price*.06:.2f}\n\nTOTAL:        ${price*1.06:.2f}',20,mono=True,spacing=14)
    d.line((335,530,765,530),fill='#6b6b60',width=2);draw_text(d,(335,553),'PAID — DEMO DATA\nThank you for shopping locally.',18,mono=True)
    return im

def dashboard(variant):
    im,d=frame('Project board / development workspace')
    draw_text(d,(45,82),'Sprint planning',39,bold=True);draw_text(d,(47,137),'Local team workspace · example project',18,'#7b8493')
    for col,(heading,tasks) in enumerate([('To do',['Design search view','Write README']),('In progress',['Build image index','Add OCR extraction']),('Completed',['Set up environment','Create demo files'])]):
        x=45+col*345;draw_text(d,(x,200),heading,23,'#586a89',bold=True)
        for k,task in enumerate(tasks):
            y=250+k*150;d.rounded_rectangle((x,y,x+315,y+125),radius=12,fill='#fff',outline='#d9dee6');draw_text(d,(x+18,y+19),task,20);draw_text(d,(x+18,y+77),f'Task #{100+variant*6+col*2+k}',14,'#6d7989')
    return im

def document(variant):
    titles=['Project meeting notes','Internship application checklist','Recipe: tomato pasta','Travel packing list','API design notes','Study plan for finals']
    bodies=[['Decision: keep inference on the local machine.','Action: test image search before the presentation.','Owner: demo team. Target: Friday afternoon.'],['Prepare a one-page resume.','Collect two project links and a short introduction.','Track deadlines and follow-up dates.'],['Ingredients: pasta, tomatoes, garlic, olive oil.','Boil salted water and cook pasta until al dente.','Simmer the sauce and combine before serving.'],['Bring a charger, passport and travel adapter.','Pack a light jacket and a reusable water bottle.','Check baggage allowance before departure.'],['GET /items returns a list of stored records.','POST /items creates a validated new record.','Return clear errors and preserve request IDs.'],['Monday: relational algebra and SQL joins.','Tuesday: probability and hypothesis testing.','Wednesday: practice exams and review mistakes.']]
    idx=variant%6;im,d=frame('Documents / '+titles[idx]);draw_text(d,(80,110),titles[idx],40,bold=True)
    draw_text(d,(83,177),'Working draft · fictional example',17,'#7c8492')
    for n,line in enumerate(bodies[idx]):draw_text(d,(85,270+n*84),line,25)
    return im

def object_diagram(color,object_name,variant):
    im,d=frame('Object study / illustrated demo')
    if object_name=='backpack':
        d.ellipse((330,575,770,635),fill='#c5c9c1')
        d.rounded_rectangle((410,125,685,555),radius=65,outline='#363e47',width=30)
        d.rounded_rectangle((350,175,750,590),radius=80,fill=color,outline='#252f39',width=7)
        d.rounded_rectangle((395,370,705,555),radius=40,fill=color,outline='#3b3336',width=5)
        d.line((410,410,690,410),fill='#e8ded0',width=6);d.rectangle((660,410,672,442),fill='#e2ded7')
        d.line((400,260,700,260),fill='#eddbcb',width=5);d.rounded_rectangle((505,284,592,332),radius=8,fill='#ddd4c2')
    elif object_name=='mug':
        d.ellipse((670,230,850,490),outline=color,width=42);d.rounded_rectangle((330,195,715,565),radius=45,fill=color);d.ellipse((330,172,715,265),fill='#383238',outline='#eee5da',width=8)
    else:
        d.rectangle((290,200,810,510),fill=color,outline='#344351',width=9);d.rectangle((330,235,770,465),fill='#c9dfb3');d.polygon([(265,510),(835,510),(910,560),(190,560)],fill='#717b86')
    draw_text(d,(35,90),'ILLUSTRATION / visual retrieval test',16,'#778392')
    return im

def main():
    OUT.mkdir(exist_ok=True)
    manifest=[]
    def save(image,name,topic,index):
        path=OUT/name;image.save(path)
        stamp=datetime(2026,10,14,15,34,tzinfo=timezone.utc)+timedelta(days=index%14,hours=index//14)
        os.utime(path,(stamp.timestamp(),stamp.timestamp()))
        manifest.append({'file':name,'topic':topic,'timestamp':stamp.isoformat(),'synthetic':True})
    i=0
    for v in range(2):
        for kind in ['cuda','eresolve','module','database','success']:
            save(terminal(kind,v),f'terminal_{kind}_{v+1:02d}.png',kind,i);i+=1
    for v in range(2):
        for topic in ['normalization','erd','joins','physics','biology','economics']:
            save(slide(topic,v),f'lecture_{topic}_{v+1:02d}.png',topic,i);i+=1
    for v in range(2):
        for item in ['Mechanical keyboard','Coffee and muffin','USB-C charger','Notebook and pens','Badminton shuttlecocks','Sandwich and juice']:
            save(receipt(item,v),f'receipt_{i:03d}.png',item,i);i+=1
    for v in range(8):save(dashboard(v),f'project_board_{v+1:02d}.png','dashboard',i);i+=1
    for v in range(8):save(document(v),f'document_{v+1:02d}.png','document',i);i+=1
    for v,(color,name) in enumerate([('#c8483e','backpack'),('#357dc0','backpack'),('#699751','backpack'),('#e2b73e','backpack'),('#c8483e','mug'),('#357dc0','mug'),('#699751','mug'),('#7e626e','laptop'),('#4c687e','laptop'),('#ad9f7a','laptop')]):
        save(object_diagram(color,name,v),f'object_study_{v+1:02d}.png',('red ' if v in [0,4] else '')+name,i);i+=1
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(f'Created {len(manifest)} synthetic images in {OUT}')

if __name__=='__main__':main()
