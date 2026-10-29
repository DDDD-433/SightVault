import re
import unicodedata
import time
from datetime import date, datetime, timedelta, timezone
import numpy as np
from .database import decode_vector
from .models import EMBEDDING_ID

STOPWORDS = set('a an the i my me you your our it this that these those is was were be been being of for to from in on at with where when did does do find show search screenshot screenshots picture pictures image images photo photos please containing contains about what how earliest oldest first occur appeared appear seen saw see time happen happened out'.split())

def normalized_text(value):
    return unicodedata.normalize('NFC', value).casefold()


def unspaced_script(value):
    return any('\u3400' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af' or '\U00020000' <= c <= '\U000323af' for c in value)


def contains_term(text, term):
    # Word/code boundaries prevent E404 matching E4040 or cat matching Application.
    # CJK text commonly has no spaces, so a character/word can occur inside a run.
    if unspaced_script(term):
        return term in text
    return re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text) is not None


def query_terms(query):
    return [t for t in re.findall(r'[\w]+', normalized_text(query)) if t not in STOPWORDS and (len(t)>1 or unspaced_script(t))][:32]

def public_asset(row):
    return {k:v for k,v in row.items() if k not in ('embedding','image_path','thumbnail_path')}

TEMPORAL_INTENT = re.compile(r'\b(earliest|oldest|first (time|occurr\w*|appear\w*|seen|saw|happen\w*|occur\w*|show\w*)|when (did|was|were)\b.*\bfirst)\b', re.I)

def _instant(value):
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise ValueError('Date-time filters need a timezone offset.')
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds')

def date_bounds(start, end):
    # Full ISO instants (sent by the UI for local-midnight boundaries) are used as-is: start inclusive, end exclusive.
    # Plain YYYY-MM-DD dates are UTC days, end inclusive.
    if (start and 'T' in start) or (end and 'T' in end):
        try:
            low = _instant(start) if start else None
            high = _instant(end) if end else None
        except (ValueError, OverflowError) as exc:
            raise ValueError('Dates must use YYYY-MM-DD or an ISO date-time with an offset.') from exc
        if low and high and low >= high:
            raise ValueError('The end date must be on or after the start date.')
        return low, high
    try:
        low = date.fromisoformat(start).isoformat()+'T00:00:00+00:00' if start else None
        high = (date.fromisoformat(end)+timedelta(days=1)).isoformat()+'T00:00:00+00:00' if end else None
    except (ValueError, OverflowError) as exc:
        raise ValueError('Dates must use YYYY-MM-DD.') from exc
    if low and high and low >= high:
        raise ValueError('The end date must be on or after the start date.')
    return low, high

class Search:
    def __init__(self, db, models):
        self.db, self.models = db, models

    def run(self, query='', mode='hybrid', sort='relevance', top_k=5, start=None,end=None,folder=None,similar_id=None):
        began = time.perf_counter()
        query = query.strip()
        if sort == 'relevance' and TEMPORAL_INTENT.search(query):
            sort = 'oldest'
        low, high = date_bounds(start,end)
        rows = self.db.all(low,high,folder)
        terms = query_terms(query)
        warning, qvec = None, None
        source = None
        if similar_id is not None:
            source = self.db.get(similar_id)
            if not source or not source['embedding'] or source['embedding_model'] != EMBEDDING_ID:
                raise ValueError('This image has no semantic embedding. Re-index after installing the model.')
            qvec = decode_vector(source['embedding'])
        elif query and mode != 'exact' and any(row['embedding'] for row in rows):
            try: qvec = self.models.embed_query(query)
            except Exception as exc: warning = 'Semantic model unavailable; showing text matches only. ' + str(exc)
        elif query and mode != 'exact' and rows:
            warning = 'No semantic vectors are indexed. Showing text matches only.'
        fts = self.db.fts(terms) if query and mode != 'semantic' and similar_id is None else {}
        max_fts = max(fts.values(), default=1) or 1
        ranked=[]
        for row in rows:
            if source and row['id'] == source['id']: continue
            ocr = normalized_text(row['ocr_text'])
            filename = normalized_text(row['filename']).replace('_',' ')
            text = ocr + ' ' + filename
            matched = [t for t in terms if contains_term(text, t)]
            ocr_matched = [t for t in terms if contains_term(ocr, t)]
            coverage = len(matched) / max(len(terms),1)
            exact = bool(terms) and all(contains_term(ocr, t) for t in terms)
            phrase = normalized_text(query).strip('"\'')
            phrase_match = bool(phrase) and contains_term(ocr, phrase)
            # Error identifiers and quoted phrases get a strong literal-match boost.
            literals = re.findall(r'"([^"]+)"',query) or [w for w in re.findall(r'\b[A-Za-z][A-Za-z0-9_]+\b',query) if any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w)]
            literal_match = bool(literals) and all(contains_term(ocr, normalized_text(w)) for w in literals)
            lexical = 0.8*coverage + 0.2*(fts.get(row['id'],0)/max_fts)
            semantic = None
            if qvec is not None and row['embedding'] and row['embedding_model'] == EMBEDDING_ID:
                try:
                    vector = decode_vector(row['embedding'])
                    if vector.shape == qvec.shape and np.isfinite(vector).all() and np.linalg.norm(vector) > 1e-8:
                        semantic = float(np.clip(np.dot(qvec,vector), -1, 1))
                    else:
                        raise ValueError('Invalid embedding shape or values')
                except ValueError:
                    warning = warning or 'Some indexed embeddings are invalid. Re-index their source folder to repair semantic search.'
            if not query and source is None:
                score = 0.0
            elif mode == 'exact':
                if not matched: continue
                score = lexical + 0.7*exact + 0.4*phrase_match + 0.6*literal_match
            elif mode == 'semantic' or source is not None:
                if semantic is None: continue
                score = semantic
            else:
                if semantic is None and not matched: continue
                score = 0.7*max(semantic or 0,0) + 0.3*lexical + 0.65*literal_match + 0.35*exact + 0.25*phrase_match
            result = public_asset(row)
            result.update(score=round(score,4), semantic_score=round(semantic,4) if semantic is not None else None,
                lexical_score=round(lexical,4),
                # The OCR badge only appears where OCR text actually contributes to ranking (not Visual meaning / similar images).
                exact_match=bool(exact or phrase_match or literal_match) and mode != 'semantic' and source is None,
                matched_terms=ocr_matched,ocr_preview=row['ocr_text'][:250])
            ranked.append(result)
        ranked.sort(key=lambda r:(-r['score'],r['timestamp'],r['id']))
        temporal = sort in ('oldest','newest')
        if temporal and query and ranked:
            # Filter before chronological sorting across the entire corpus, never just top K.
            exact_rows = [r for r in ranked if r['exact_match']]
            if exact_rows and mode != 'semantic':
                ranked = exact_rows
            elif mode == 'exact' or qvec is None:
                best = max(r['lexical_score'] for r in ranked)
                ranked = [r for r in ranked if r['lexical_score'] >= best * 0.8]
            else:
                best = max(r['semantic_score'] if r['semantic_score'] is not None else -1 for r in ranked)
                ranked = [r for r in ranked if r['semantic_score'] is not None and r['semantic_score'] >= best-0.035]
        total = len(ranked)
        if temporal: ranked.sort(key=lambda r:(r['timestamp'],r['id']),reverse=sort=='newest')
        return {'results':ranked[:top_k], 'total_matches':total, 'query':query, 'mode':mode,'sort':sort,
                'latency_ms':round((time.perf_counter()-began)*1000,1), 'warning':warning,
                'temporal_note':'Earliest matching file in your index. File timestamps are evidence of the file date, not proof of when an event happened.' if temporal and query else None}
