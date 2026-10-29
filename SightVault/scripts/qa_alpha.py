"""Real OCR and direct embeddings for a disposable transparent diagram."""
from pathlib import Path
import sys,tempfile,json
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image,ImageDraw,ImageFont
from sightvault.config import Config
from sightvault.models import Models
from sightvault.database import Database,decode_vector
from sightvault.ingestion import Ingestor
ROOT=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='sightvault-alpha-qa-') as folder:
    work=Path(folder);pics=work/'pics';pics.mkdir()
    image=Image.new('RGBA',(1000,240),(0,0,0,0));font_path=Path('/System/Library/Fonts/Supplemental/Arial.ttf')
    font=ImageFont.truetype(str(font_path),40) if font_path.is_file() else ImageFont.load_default(size=40)
    ImageDraw.Draw(image).text((30,80),'ERESOLVE dependency conflict',fill=(0,0,0,255),font=font)
    image.save(pics/'transparent.png')
    cfg=Config(root=ROOT,data=work/'data',models=ROOT/'.models');cfg.prepare();m=Models(cfg);db=Database(cfg.db_path)
    legacy=image.convert('RGB');old_vector=m.embed_image(legacy);query=m.embed_query('ERESOLVE dependency conflict');old_ocr=m.ocr(legacy,'rapid')
    result=Ingestor(cfg,db,m).run(pics,ocr_backend='minicpm');row=db.all()[0]
    new_vector=decode_vector(row['embedding']);old_score=float(old_vector@query);new_score=float(new_vector@query)
    report={'indexed':result['indexed'],'legacy_ocr':old_ocr,'corrected_ocr':row['ocr_text'],
            'legacy_image_query_cosine':old_score,'corrected_image_query_cosine':new_score,
            'models':m.status(),'passed':bool(result['indexed']==1 and 'ERESOLVE' in row['ocr_text'] and new_score>old_score)}
    (ROOT/'qa/independent-review/alpha.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    raise SystemExit(0 if report['passed'] else 1)
