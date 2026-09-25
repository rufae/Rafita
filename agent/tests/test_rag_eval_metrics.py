"""Unit tests for the pure metric helpers in scripts/rag_eval.py (task 1.3)."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rag_eval.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rag_eval_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rag_eval = _load_module()


def test_first_rank():
    notes = ["a.md", "b.md", "c.md"]
    assert rag_eval.first_rank(notes, "b.md") == 2
    assert rag_eval.first_rank(notes, "z.md") is None


def test_summarize_positives():
    rows = [
        {"rank": 1, "keyword_any": True, "keyword_all": True, "expected_relevance": 0.8},
        {"rank": 2, "keyword_any": True, "keyword_all": False, "expected_relevance": 0.5},
        {"rank": None, "keyword_any": False, "keyword_all": False, "expected_relevance": 0.0},
    ]
    summary = rag_eval.summarize_positives(rows, 5)
    assert summary["total"] == 3
    assert summary["recall@1"] == 1 / 3
    assert summary["recall@3"] == 2 / 3
    assert summary["recall@5"] == 2 / 3
    assert abs(summary["mrr@5"] - (1 + 0.5) / 3) < 1e-9
    assert summary["keyword_any@5"] == 2 / 3
    assert summary["keyword_all@5"] == 1 / 3
    assert abs(summary["mean_expected_relevance"] - (0.8 + 0.5 + 0.0) / 3) < 1e-9
    assert summary["min_expected_relevance"] == 0.0


def test_summarize_negatives():
    rows = [{"top_relevance": 0.4}, {"top_relevance": 0.65}, {"top_relevance": 0.8}]
    summary = rag_eval.summarize_negatives(rows)
    assert summary["total"] == 3
    assert abs(summary["mean_top_relevance"] - (0.4 + 0.65 + 0.8) / 3) < 1e-9
    assert summary["max_top_relevance"] == 0.8
    assert abs(summary["false_found@>=0.5"] - 2 / 3) < 1e-9
    assert abs(summary["false_found@>=0.6"] - 2 / 3) < 1e-9
    assert abs(summary["false_found@>=0.7"] - 1 / 3) < 1e-9


def test_normalize_strips_accents_and_case():
    assert rag_eval.normalize("Día 20") == "dia 20"
    assert rag_eval.normalize("BGE-M3") == "bge-m3"


def test_evaluate_thresholds():
    rows = rag_eval.evaluate_thresholds(
        positive_relevances=[0.4, 0.6],
        negative_relevances=[0.5, 0.7],
        thresholds=[0.45, 0.65],
    )
    assert rows[0]["threshold"] == 0.45
    assert rows[0]["false_negatives"] == 1
    assert rows[0]["false_positives"] == 2
    assert rows[1]["false_negatives"] == 2
    assert rows[1]["false_positives"] == 1
    assert abs(rows[1]["false_negative_rate"] - 1.0) < 1e-9


def test_distance_helpers():
    assert rag_eval.l2_sq([1.0, 0.0], [0.0, 2.0]) == 5.0
    assert rag_eval.cosine([1.0, 0.0], [0.0, 2.0]) == 0.0
    assert rag_eval.cosine([1.0, 0.0], [2.0, 0.0]) == 1.0
    assert rag_eval.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert rag_eval.pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == 1.0
    assert rag_eval.pearson([1.0], [2.0]) == 0.0
