"""Ground-truth metrics. All edit distance terms penalize insertions and omissions."""
from __future__ import annotations

import re
import unicodedata

_IDENTIFIER_RE = re.compile(
    r"https?://[^\s]+|"
    r"(?<!\w)(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)"
    r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*(?!\w)|"
    r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|"
    r"\b[A-Z]?[a-z]+(?:[A-Z][A-Za-z0-9]*)+\b|\b[A-Z]{4,}\b"
)
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM))?\b", re.I)
_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])(?:v\d+(?:\.\d+){0,3}|\d+(?:\.\d+){2,3})(?:[-+][A-Za-z0-9.-]+)?(?![A-Za-z0-9])", re.I)
_PRICE_RE = re.compile(r"(?<!\w)(?:\$|€|£|USD\s*)\s*\d+(?:,\d{3})*(?:\.\d{1,2})?(?:%?)(?!\w)", re.I)
_NUMBER_RE = re.compile(r"(?<![\w./:-])\d+(?:,\d{3})*(?:\.\d+)?%?(?![\w./:-])")


def extract_special_tokens(text: str):
    """Find protected strings; date/price/version segments aren't split into digits."""
    dates = sorted(set(_DATE_RE.findall(text)))
    times = sorted(set(_TIME_RE.findall(text)))
    versions = sorted(set(_VERSION_RE.findall(text)))
    prices = sorted(set(_PRICE_RE.findall(text)))
    protected_spans = []
    for pattern in (_DATE_RE, _TIME_RE, _VERSION_RE, _PRICE_RE):
        protected_spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    identifiers = []
    for match in _IDENTIFIER_RE.finditer(text):
        value = match.group()
        if value.startswith(("http://", "https://")) or not any(
            match.start() < end and match.end() > start for start, end in protected_spans
        ):
            identifiers.append(value)
    numbers = [
        match.group() for match in _NUMBER_RE.finditer(text)
        if not any(match.start() < end and match.end() > start for start, end in protected_spans)
    ]
    return {
        "identifiers": sorted(set(identifiers)), "dates": dates, "times": times,
        "versions": versions, "prices": prices, "numbers": sorted(set(numbers)),
    }


def normalize_for_distance(text: str):
    """Keep case/punctuation exact; normalize Unicode and collapse layout whitespace."""
    text = unicodedata.normalize("NFC", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip()


def _distance_counts(reference, hypothesis):
    """Levenshtein distance with a deterministic S/D/I decomposition."""
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    previous = [(j, 0, 0, j) for j in range(cols)]
    for i in range(1, rows):
        current = [(i, 0, i, 0)] + [(0, 0, 0, 0)] * (cols - 1)
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
                current[j] = previous[j - 1]
            else:
                substitution = previous[j - 1]
                deletion = previous[j]
                insertion = current[j - 1]
                candidates = [
                    (substitution[0] + 1, substitution[1] + 1, substitution[2], substitution[3]),
                    (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3]),
                    (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1),
                ]
                current[j] = min(candidates, key=lambda value: (value[0], value[2], value[3], value[1]))
        previous = current
    return {"distance": previous[-1][0], "substitutions": previous[-1][1],
            "deletions": previous[-1][2], "insertions": previous[-1][3]}


def _set_metrics(expected, predicted):
    expected, predicted = set(expected), set(predicted)
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    # Undefined categories remain null when neither side has support. This avoids
    # inflating macro averages with empty dates/times/etc. Micro summaries use counts.
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall
          else (0.0 if expected or predicted else None))
    return {"expected": len(expected), "predicted": len(predicted), "tp": tp, "fp": fp,
            "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def evaluate_plain(ground_truth, prediction):
    ref = normalize_for_distance(ground_truth["expected_text"])
    hyp = normalize_for_distance(prediction)
    char_ops = _distance_counts(ref, hyp)
    word_ops = _distance_counts(ref.split(), hyp.split())
    expected_tokens = {k: set(ground_truth.get(k, [])) for k in
                       ("identifiers", "dates", "times", "versions", "prices", "numbers")}
    predicted_tokens = extract_special_tokens(hyp)
    result = {
        "cer": char_ops["distance"] / max(1, len(ref)),
        "wer": word_ops["distance"] / max(1, len(ref.split())),
        "character_ops": char_ops, "word_ops": word_ops,
    }
    for key, expected in expected_tokens.items():
        result[key] = _set_metrics(expected, predicted_tokens[key])
    expected_numeric = set().union(*(expected_tokens[k] for k in ("dates", "times", "versions", "prices", "numbers")))
    predicted_numeric = set().union(*(set(predicted_tokens[k]) for k in ("dates", "times", "versions", "prices", "numbers")))
    result["numeric_values"] = _set_metrics(expected_numeric, predicted_numeric)
    result["reference_chars"] = len(ref)
    result["hypothesis_chars"] = len(hyp)
    result["reference_words"] = len(ref.split())
    result["hypothesis_words"] = len(hyp.split())
    return result


def summarize_plain(rows):
    """Corpus CER/WER and count-weighted token metrics across per-image results."""
    char = {key: 0 for key in ("distance", "substitutions", "deletions", "insertions")}
    word = dict(char)
    ref_chars = ref_words = hyp_chars = hyp_words = 0
    token_groups = ("identifiers", "dates", "times", "versions", "prices", "numbers", "numeric_values")
    token_counts = {key: {part: 0 for part in ("tp", "fp", "fn")} for key in token_groups}
    for row in rows:
        result = row["metrics"]
        for total, source in ((char, result["character_ops"]), (word, result["word_ops"])):
            for key in total:
                total[key] += source[key]
        ref_chars += result["reference_chars"]
        ref_words += result["reference_words"]
        hyp_chars += result["hypothesis_chars"]
        hyp_words += result["hypothesis_words"]
        for key in token_groups:
            for part in token_counts[key]:
                token_counts[key][part] += result[key][part]

    def finish(counts):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f1 = (2*p*r/(p+r) if p is not None and r is not None and p+r
              else (0.0 if tp + fp + fn else None))
        return {**counts, "precision": p, "recall": r, "f1": f1,
                "ground_truth_support": tp + fn, "predicted_support": tp + fp}
    return {
        "cer": char["distance"] / max(1, ref_chars), "wer": word["distance"] / max(1, ref_words),
        "character_ops": char, "word_ops": word,
        "reference_chars": ref_chars, "hypothesis_chars": hyp_chars,
        "reference_words": ref_words, "hypothesis_words": hyp_words,
        "extra_char_count": char["insertions"], "omitted_char_count": char["deletions"],
        "extra_word_count": word["insertions"], "omitted_word_count": word["deletions"],
        **{key: finish(value) for key, value in token_counts.items()},
    }
