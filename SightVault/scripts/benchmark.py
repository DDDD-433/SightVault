"""Retrieval benchmark against the real index and real EmbeddingGemma 2 inference.

Labels come from file names chosen when the synthetic set was generated (manifest.json);
they are used only for scoring here, never by ingestion or retrieval.
"""
import argparse,json,sys,socket
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from sightvault.config import Config
from sightvault.database import Database,decode_vector
from sightvault.models import Models
from sightvault.search import Search

# (query, mode, sort, expected filename prefix(es) for top-1, note)
CASES=[
  ('Find the screenshot where my GPU ran out of memory','hybrid','relevance','terminal_cuda','demo'),
  ('GPU memory problem','semantic','relevance','terminal_cuda','paraphrase; "GPU memory problem" is not an OCR phrase'),
  ('Find ERESOLVE','hybrid','relevance','terminal_eresolve','exact code'),
  ('ModuleNotFoundError','exact','relevance','terminal_module','exact code'),
  ('Show slides about databases','semantic','relevance',('lecture_normalization','lecture_erd','lecture_joins'),'"database" absent from these slides'),
  ('Find the image with a red backpack','semantic','relevance','object_study_01','no text in image; colour+object'),
  ('When did the CUDA error first occur?','hybrid','relevance','terminal_cuda_01','natural-language first occurrence'),
  ('Find the receipt where I bought a keyboard','hybrid','relevance',('receipt_022','receipt_028'),'keyboard receipt'),
  # Paraphrases whose words do not occur in the target OCR or filename (semantic only).
  ('javascript package installer version conflict','semantic','relevance','terminal_eresolve','paraphrase'),
  ('python could not find an installed library','semantic','relevance','terminal_module','paraphrase'),
  ('plants turning sunlight into sugar','semantic','relevance','lecture_biology','paraphrase'),
  ('graphics card ran out of VRAM while training a neural network','semantic','relevance','terminal_cuda','paraphrase'),
  ('blue rucksack drawing','semantic','relevance','object_study_02','colour discrimination'),
  ('a cup for hot drinks','semantic','relevance',('object_study_05','object_study_06','object_study_07'),'object paraphrase'),
  ('kanban board with tasks','semantic','relevance','project_board','layout semantics'),
  ('photograph of a kitchen','semantic','relevance','kitchen_photo','real photo, no OCR text'),
  ('a bridge over water','semantic','relevance','bridge_photo','real photo'),
]
# Negative queries: nothing relevant exists; the best cosine should stay below positives.
NEGATIVES=['a dog playing in the snow','fireworks over a city skyline at night','handwritten wedding invitation','satellite image of a hurricane']

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--offline-check',action='store_true');parser.add_argument('--output',default='QA_RESULTS.json');args=parser.parse_args()
    if args.offline_check:
        original_connect=socket.socket.connect
        def reject_network(self,address):
            if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1','localhost'):
                return original_connect(self,address)
            raise RuntimeError('Outbound network blocked by offline verification')
        socket.socket.connect=reject_network
    config=Config();db=Database(config.db_path);models=Models(config);search=Search(db,models)
    rows=[]
    for query,mode,sort,expected,note in CASES:
        r=search.run(query,mode=mode,sort=sort)
        names=[a['filename'] for a in r['results']]
        good=bool(names) and names[0].startswith(expected)
        row={'query':query,'mode':mode,'note':note,'top1_correct':good,'expected_prefix':expected,'results':names,
             'top_semantic':r['results'][0]['semantic_score'] if r['results'] else None,'sort':r['sort'],'latency_ms':r['latency_ms'],'warning':r['warning']}
        rows.append(row);print(json.dumps(row),flush=True)
    positives=[r['top_semantic'] for r in rows if r['mode']=='semantic' and r['top_semantic'] is not None]
    negatives=[]
    for q in NEGATIVES:
        r=search.run(q,mode='semantic')
        negatives.append({'query':q,'top':r['results'][0]['filename'],'top_semantic':r['results'][0]['semantic_score']})
    # Proof that stored vectors are image embeddings: re-embed source pixels and compare with the stored vector.
    from PIL import Image
    proof=[]
    for name in ('terminal_cuda_01.png','object_study_01.png','kitchen_photo.jpg'):
        row=next(a for a in db.all() if a['filename']==name)
        with Image.open(config.data/'images'/row['image_path']) as im:
            fresh=models.embed_image(im.convert('RGB'))
        proof.append({'file':name,'cosine_fresh_vs_stored':round(float(fresh@decode_vector(row['embedding'])),4)})
    report={'offline_network_blocked':args.offline_check,'cases':rows,'passed':sum(r['top1_correct'] for r in rows),'total':len(rows),
            'negative_queries':negatives,'min_positive_semantic_top':min(positives),'max_negative_semantic_top':max(n['top_semantic'] for n in negatives),
            'image_embedding_proof':proof,'database':db.stats()}
    report['negatives_below_all_positives']=report['max_negative_semantic_top']<report['min_positive_semantic_top']
    Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ('passed','total','min_positive_semantic_top','max_negative_semantic_top','negatives_below_all_positives','image_embedding_proof')},indent=2))
    return int(report['passed']!=report['total'])

if __name__=='__main__':sys.exit(main())
