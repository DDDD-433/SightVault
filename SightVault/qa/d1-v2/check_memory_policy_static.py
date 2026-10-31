#!/usr/bin/env python3
"""Static regression checks for cache-clear bookkeeping; imports no model runtime."""
from __future__ import annotations

import ast
from pathlib import Path


HERE = Path(__file__).resolve().parent


def names(tree: ast.AST, identifier: str, context: type[ast.expr_context] | None = None) -> list[ast.Name]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == identifier
        and (context is None or isinstance(node.ctx, context))
    ]


def assert_guarded_duplicate_handling(tree: ast.AST, container: str) -> None:
    comparison = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not node.comparators:
            continue
        left = node.left
        if not (
            isinstance(left, ast.Call)
            and isinstance(left.func, ast.Attribute)
            and left.func.attr == "get"
            and isinstance(left.func.value, ast.Subscript)
            and isinstance(left.func.value.value, ast.Name)
            and left.func.value.value.id == container
        ):
            continue
        right = node.comparators[0]
        if isinstance(right, ast.Name) and right.id == "request_index":
            comparison = True
        elif isinstance(right, ast.Subscript) and isinstance(right.value, ast.Name) and right.value.id in {"candidate", "pair"}:
            comparison = True
        if comparison:
            break
    assert comparison, f"{container} cleanup failure path must check for an already appended result row"


def main() -> int:
    for filename, container in (
        ("run_d1_classification_routing.py", "execution"),
        ("run_d1_rerank.py", "candidate_results"),
    ):
        path = HERE / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not names(tree, "cache_release_s", ast.Load), f"{filename} still loads the removed cache_release_s name"
        assert names(tree, "cache_clear_elapsed"), f"{filename} does not measure cache-clear duration"
        assert any(
            isinstance(node, ast.Constant) and node.value == "mps_cache_clear_elapsed_s"
            for node in ast.walk(tree)
        ), f"{filename} does not persist cache-clear duration"
        assert_guarded_duplicate_handling(tree, container)
        print(f"OK: {filename} cache-clear bookkeeping and post-inference error path")
    classification = ast.parse((HERE / "run_d1_classification_routing.py").read_text(encoding="utf-8"))
    coverage = next(
        node.value for node in ast.walk(classification)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "full_fixture_coverage" for target in node.targets)
    )
    coverage_source = ast.get_source_segment((HERE / "run_d1_classification_routing.py").read_text(encoding="utf-8"), coverage)
    assert "not args.repeat_to_100" not in coverage_source, "Declared repeats must not invalidate completed 68-image coverage"
    assert "len(predictions[\"baseline\"]) == 62" in coverage_source
    assert "len(predictions[\"supplement\"]) == 6" in coverage_source
    print("OK: 68 unique fixture coverage remains true when a completed run appends declared repeat calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
