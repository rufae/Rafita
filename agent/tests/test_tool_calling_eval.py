"""Unit tests for the pure scoring helpers in scripts/tool_calling_eval.py (task 1.7)."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tool_calling_eval.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("tool_calling_eval_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool_eval = _load_module()


def test_score_attempt_correct_tool():
    assert tool_eval.score_attempt("save_expense", ["save_expense"], True) == {
        "correct": True,
        "mode": "ok",
    }


def test_score_attempt_invalid_args():
    assert tool_eval.score_attempt("save_expense", ["save_expense"], False) == {
        "correct": False,
        "mode": "invalid_args",
    }


def test_score_attempt_wrong_tool():
    assert tool_eval.score_attempt("save_expense", ["create_event"], True)["mode"] == "wrong_tool"


def test_score_attempt_accepts_equivalent_tool():
    assert tool_eval.score_attempt("a", ["b"], True, accept=["b"]) == {
        "correct": True,
        "mode": "ok",
    }
    invalid = tool_eval.score_attempt("a", ["b"], False, accept=["b"])
    assert invalid["mode"] == "invalid_args"
    still_wrong = tool_eval.score_attempt("a", ["c"], True, accept=["b"])
    assert still_wrong["mode"] == "wrong_tool"


def test_score_attempt_no_tool():
    assert tool_eval.score_attempt("save_expense", [], True)["mode"] == "no_tool"


def test_negative_case_scores():
    assert tool_eval.score_attempt(None, [], True)["correct"] is True
    assert tool_eval.score_attempt(None, ["search_web"], True)["mode"] == "unexpected_tool"


def test_required_args_by_tool():
    tools = [
        {"function": {"name": "a", "parameters": {"required": ["x"]}}},
        {"function": {"name": "b", "parameters": {}}},
    ]
    assert tool_eval.required_args_by_tool(tools) == {"a": ["x"], "b": []}


def test_args_ok():
    required = {"a": ["x"]}
    assert tool_eval._args_ok("a", '{"x": 1}', required) is True
    assert tool_eval._args_ok("a", '{"y": 2}', required) is False
    assert tool_eval._args_ok("a", "not-json", required) is False


def test_build_report_aggregates():
    cases = [
        {
            "id": "a",
            "expected": "a",
            "prompt": "p",
            "attempts": [
                {"correct": True, "mode": "ok"},
                {"correct": False, "mode": "no_tool"},
            ],
        },
        {
            "id": "b",
            "expected": None,
            "prompt": "p",
            "attempts": [{"correct": True, "mode": "ok"}],
        },
    ]
    report = tool_eval.build_report(cases)
    assert report["total_correct"] == 2
    assert report["total_attempts"] == 3
    assert report["failure_modes"] == {"no_tool": 1}
    assert report["per_tool"][0]["rate"] == 0.5
