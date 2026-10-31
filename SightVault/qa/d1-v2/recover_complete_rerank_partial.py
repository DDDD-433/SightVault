#!/usr/bin/env python3
"""Recover final metadata from a complete atomic rerank checkpoint, without inference."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SIGHTVAULT_ROOT = HERE.parents[1]
REPO_ROOT = HERE.parents[2]
DEFAULT_RUN_COMMIT = '828f192'
EXPECTED_PAIRS = 16 * 20
RUNNER_RELATIVE = 'SightVault/qa/d1-v2/run_d1_rerank.py'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git_text(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=REPO_ROOT, text=True, stderr=subprocess.PIPE).strip()


def recover(args) -> dict:
    partial_path = args.partial.resolve()
    partial_bytes = partial_path.read_bytes()
    partial_sha = hashlib.sha256(partial_bytes).hexdigest()
    partial = json.loads(partial_bytes)
    embedding = json.loads(args.embedding.read_text(encoding='utf-8'))
    image_manifest = json.loads(args.image_manifest.read_text(encoding='utf-8'))
    runtime_smoke = json.loads(args.runtime_smoke.read_text(encoding='utf-8'))
    model_provenance = json.loads(args.model_provenance.read_text(encoding='utf-8'))
    post_exit = json.loads(args.post_exit_snapshot.read_text(encoding='utf-8'))

    expected = []
    for query in embedding['queries']:
        if len(query['top20']) != 20:
            raise ValueError(f'Frozen candidate list is not top-20 for {query["query_id"]}')
        expected.extend((query['query_id'], candidate['image_id'], candidate['filename'])
                        for candidate in query['top20'])
    actual_rows = partial.get('candidate_results', [])
    actual = [(row.get('query_id'), row.get('image_id'), row.get('filename')) for row in actual_rows]
    if partial.get('attempted_pair_count') != EXPECTED_PAIRS or partial.get('successful_pair_count') != EXPECTED_PAIRS:
        raise ValueError('Atomic partial checkpoint does not record all 320 successful pair calls')
    if partial.get('expected_pair_count') != EXPECTED_PAIRS or partial.get('forward_passes') != EXPECTED_PAIRS:
        raise ValueError('Atomic partial checkpoint does not record all 320 expected forward passes')
    if len(actual_rows) != EXPECTED_PAIRS or actual != expected:
        raise ValueError('Persisted query/candidate results differ from the frozen 16x20 input order')
    if partial.get('failures') or any(row.get('error') for row in actual_rows):
        raise ValueError('Cannot recover a complete-success artifact from a checkpoint with model errors')
    if len({(row['query_id'], row['image_id']) for row in actual_rows}) != EXPECTED_PAIRS:
        raise ValueError('Persisted results contain duplicate query/candidate pairs')
    for row in actual_rows:
        if row.get('question_count') != 1 or row.get('forward_passes') != 1:
            raise ValueError(f'Unexpected per-pair scoring cardinality at {row.get("query_id")}/{row.get("image_id")}')
        if not 0.0 <= float(row['p_yes']) <= 1.0:
            raise ValueError(f'Invalid typed P(yes) at {row.get("query_id")}/{row.get("image_id")}')
        if 'elapsed_with_memory_policy_s' not in row or 'answer_release_and_cache_policy_s' not in row:
            raise ValueError(f'Missing per-pair runtime cost at {row.get("query_id")}/{row.get("image_id")}')

    manifest_by_id = {item['id']: item for item in image_manifest['images']}
    query_summaries = {row['query_id']: deepcopy(row) for row in partial.get('query_timings', [])}
    if len(query_summaries) != 15:
        raise ValueError(f'Expected 15 finalized query loop timings before the failed report assembly; found {len(query_summaries)}')
    per_query = []
    for query in embedding['queries']:
        qid = query['query_id']
        rows = [row for row in actual_rows if row['query_id'] == qid]
        if len(rows) != 20:
            raise ValueError(f'Expected 20 scored candidates for {qid}, found {len(rows)}')
        call_sum = sum(float(row['elapsed_with_memory_policy_s']) for row in rows)
        inference_sum = sum(float(row['elapsed_s']) for row in rows)
        cleanup_sum = sum(float(row['answer_release_and_cache_policy_s']) for row in rows)
        summary = query_summaries.get(qid)
        if summary is None:
            summary = {
                'query_id': qid,
                'candidate_count': 20,
                'successful_calls': 20,
                'serial_candidate_call_seconds_including_memory_policy': call_sum,
                'query_loop_wall_seconds_including_checkpoints': None,
                'query_loop_wall_measurement': 'unknown: the atomic checkpoint was written before Q16 timing was appended; final metadata assembly then failed',
                'inference_only_candidate_call_seconds': inference_sum,
                'answer_release_and_cache_policy_seconds': cleanup_sum,
            }
        else:
            if summary.get('successful_calls') != 20 or summary.get('candidate_count') != 20:
                raise ValueError(f'Existing query timing is incomplete for {qid}')
            summary['serial_candidate_call_seconds_including_memory_policy'] = call_sum
            summary['inference_only_candidate_call_seconds'] = inference_sum
            summary['answer_release_and_cache_policy_seconds'] = cleanup_sum
            summary['query_loop_wall_measurement'] = 'measured by the original runner'
        for row in rows:
            manifest_record = manifest_by_id.get(row['image_id'])
            if (not manifest_record or manifest_record['file'] != row['filename']
                    or manifest_record['sha256'] != row['sha256']):
                raise ValueError(f'Image provenance mismatch at {qid}/{row["image_id"]}')
        per_query.append(summary)

    run_commit = git_text('rev-parse', args.run_commit)
    runner_bytes = subprocess.check_output(['git', 'show', f'{run_commit}:{RUNNER_RELATIVE}'], cwd=REPO_ROOT)
    runner_sha = hashlib.sha256(runner_bytes).hexdigest()
    runtime = runtime_smoke['runtime']
    platform = runtime_smoke['platform']
    model = model_provenance['reference']
    resource = partial['resource_checkpoint']
    completed = {
        'experiment': 'd1-v2-query-conditioned-rerank-pair-scores',
        'recovery_status': 'RECOVERED_COMPLETE_SCORES_FINAL_METADATA_INCOMPLETE',
        'baseline_commit': partial.get('baseline_commit', '766027c79d4c6f17ba7b88b1df3bc079dfbbd141'),
        'experiment_commit': run_commit,
        'runner_script_sha256_at_inference': runner_sha,
        'raw_partial_artifact': {
            'path': str(partial_path),
            'bytes': len(partial_bytes),
            'sha256': partial_sha,
            'raw_status_preserved': partial.get('status'),
        },
        'recovered_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'run_started_at_utc': None,
        'timestamp_note': 'The runner kept its UTC start timestamp only in memory. Its final metadata assembly failed after model cleanup, so no exact timestamp was persisted.',
        'finalization_failure': {
            'error': "NameError: name 'sys' is not defined",
            'phase': 'final JSON metadata assembly after model cleanup',
            'all_320_calls_complete_before_failure': True,
            'clean_runner_finalization': False,
        },
        'command_arguments_reconstructed_from_granted_invocation': [
            '/Users/ddddd-433/.codex/worktrees/phase2-d1/d1-reference-env/bin/python',
            'SightVault/qa/d1-v2/run_d1_rerank.py',
            '--model-path', '/Users/ddddd-433/.codex/worktrees/phase2-d1/d1-models/d1-2B',
            '--embedding-results', 'SightVault/qa/d1-v2/embedding_top20.json',
            '--image-manifest', 'SightVault/qa/d1-v2/image_manifest.json',
            '--output', str(HERE / 'd1_rerank_scores_per_query_every5.json'),
            '--empty-cache-every', '5',
        ],
        'model': {
            'repo': model['repo'],
            'revision': model['revision'],
            'format': model['format'],
            'model_file_bytes': model['model_file']['bytes'],
            'model_file_sha256': model['model_file']['sha256'],
        },
        'runtime': {
            'name': runtime['name'],
            'transformers_version': runtime['transformers_version'],
            'torch_version': runtime['torch_version'],
            'device': runtime['device'],
            'dtype': runtime['dtype'],
            'python': platform['python'],
            'platform': platform['os'],
            'ram_bytes': platform['ram_bytes'],
            'offline_flags': runtime['offline_flags'],
            'network_guard': 'OS sandbox denied outbound network; runner installed DNS/non-loopback socket guard; blocked-attempt count was not persisted in the recovered checkpoint.',
            'blocked_network_attempts': None,
        },
        'input_isolation': {
            'embedding_top20_file': 'SightVault/qa/d1-v2/embedding_top20.json',
            'image_manifest': 'SightVault/qa/d1-v2/image_manifest.json',
            'classification_labels_read': False,
            'retrieval_relevance_read': False,
            'ocr_or_filename_text_sent_to_d1': False,
            'd1_input': 'Each of 320 independent calls used raw candidate pixels and only that query text in one typed noul question.',
            'candidate_selection': 'Frozen EmbeddingGemma semantic top-20 list per query; d1 only reordered those candidates.',
            'query_candidate_pair_count': EXPECTED_PAIRS,
            'unique_candidate_images_scored': partial['unique_candidate_images'],
            'cross_query_batching': False,
            'candidate_image_sha256_checks_repeated_after_run': False,
            'candidate_hash_note': 'The runner verifies candidate hashes before loading the model. The recovered rows retain the manifest hashes and were matched to the frozen manifest, but images were not rehashed in this post-run finalizer.',
        },
        'performance': {
            'cold_model_load_s': None,
            'processor_and_tokenizer_init_s': None,
            'query_candidate_pair_evaluation_wall_s': None,
            'timing_note': 'All per-pair inference and cleanup timings are preserved. The runner did not persist model/processor load durations, total evaluation wall, or Q16 loop wall before finalization failed; these values remain unknown.',
            'query_timings': per_query,
            'serial_candidate_call_sum_including_memory_policy_s': sum(float(row['elapsed_with_memory_policy_s']) for row in actual_rows),
            'candidate_inference_only_sum_s': sum(float(row['elapsed_s']) for row in actual_rows),
            'answer_release_and_cache_policy_sum_s': sum(float(row['answer_release_and_cache_policy_s']) for row in actual_rows),
            'process_rss_peak_sampled_bytes': resource['process_rss_peak_bytes'],
            'max_mps_memory_sample_after_request_bytes': resource['max_mps_sample_after_request_bytes'],
            'max_mps_memory_sample_after_answer_release_bytes': resource['max_mps_sample_after_answer_release_bytes'],
            'max_mps_memory_sample_after_cache_release_bytes': resource['max_mps_sample_after_cache_release_bytes'],
            'python_gc_after_each_successful_pair': True,
            'empty_cache_every_successful_pairs': 5,
            'memory_pressure_before_model_load': resource['memory_pressure_before_model_load'],
            'memory_pressure_checkpoints': resource['memory_pressure_checkpoints'],
            'mps_memory_after_model_unload_in_process_bytes': None,
            'post_unload_note': 'In-process after-unload MPS/RSS/object-ref counters were not persisted because final JSON assembly failed. Process exit was separately confirmed and a post-exit system snapshot is linked.',
            'post_process_exit_system_snapshot': args.post_exit_snapshot.name,
            'forward_passes': partial['forward_passes'],
            'model_loaded_once': True,
            'concurrency': 1,
        },
        'completed': True,
        'all_320_calls_complete': True,
        'failures': [],
        'candidate_union_size': partial['unique_candidate_images'],
        'query_candidate_pair_count': EXPECTED_PAIRS,
        'query_timings': per_query,
        'candidate_results': actual_rows,
    }
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partial', type=Path, default=HERE / 'd1_rerank_scores_per_query_every5.partial.json')
    parser.add_argument('--embedding', type=Path, default=HERE / 'embedding_top20.json')
    parser.add_argument('--image-manifest', type=Path, default=HERE / 'image_manifest.json')
    parser.add_argument('--runtime-smoke', type=Path, default=HERE / 'runtime_smoke.json')
    parser.add_argument('--model-provenance', type=Path, default=HERE / 'model_provenance.json')
    parser.add_argument('--post-exit-snapshot', type=Path, default=HERE / 'd1_rerank_post_exit_system_snapshot.json')
    parser.add_argument('--run-commit', default=DEFAULT_RUN_COMMIT)
    parser.add_argument('--output', type=Path, default=HERE / 'd1_rerank_scores_per_query_every5.recovered.json')
    args = parser.parse_args()
    try:
        report = recover(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'recovery_status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}, indent=2), file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'output': str(args.output.resolve()),
        'recovery_status': report['recovery_status'],
        'raw_partial_sha256': report['raw_partial_artifact']['sha256'],
        'all_320_calls_complete': report['all_320_calls_complete'],
        'candidate_pairs': report['query_candidate_pair_count'],
        'call_sum_including_memory_policy_s': round(report['performance']['serial_candidate_call_sum_including_memory_policy_s'], 3),
        'q16_loop_wall_seconds': None,
        'clean_runner_finalization': report['finalization_failure']['clean_runner_finalization'],
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
