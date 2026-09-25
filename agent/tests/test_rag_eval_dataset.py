"""Schema and consistency checks for the RAG evaluation dataset (task 1.2)."""

import json
from pathlib import Path

DATASET = Path(__file__).resolve().parent / "rag_eval" / "dataset.jsonl"
VAULT = Path(__file__).resolve().parent / "rag_eval" / "vault"

REQUIRED_FIELDS = {"id", "query", "category", "relevant", "expected_note", "expected_keywords"}


def _load() -> list[tuple[int, dict]]:
    items: list[tuple[int, dict]] = []
    for line_no, line in enumerate(DATASET.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        items.append((line_no, json.loads(line)))
    return items


def test_dataset_exists_and_is_valid_jsonl():
    assert DATASET.exists(), "dataset.jsonl must be versioned in the repo"
    assert len(_load()) >= 20, "dataset must contain at least 20 cases"


def test_schema_and_unique_ids():
    seen: set[str] = set()
    for line_no, item in _load():
        assert set(item) >= REQUIRED_FIELDS, f"line {line_no} is missing fields"
        assert item["id"] not in seen, f"duplicate id {item['id']}"
        seen.add(item["id"])
        assert isinstance(item["query"], str) and item["query"].strip()
        assert isinstance(item["relevant"], bool)
        assert isinstance(item["expected_keywords"], list)
        assert isinstance(item["expected_note"], (str, type(None)))


def test_positive_cases_point_to_existing_notes():
    positives = [item for _, item in _load() if item["relevant"]]
    assert len(positives) >= 20, "need at least 20 positive cases"
    for item in positives:
        assert item["expected_note"], f"{item['id']} needs expected_note"
        assert (VAULT / item["expected_note"]).exists(), (
            f"{item['id']} note not found in eval vault"
        )
        assert item["expected_keywords"], f"{item['id']} needs expected_keywords"


def test_negative_cases_have_no_expected_note():
    negatives = [item for _, item in _load() if not item["relevant"]]
    assert len(negatives) >= 5, "need at least 5 negative cases"
    for item in negatives:
        assert item["expected_note"] is None
        assert item["expected_keywords"] == []


def test_unique_queries():
    queries = [item["query"].strip().lower() for _, item in _load()]
    assert len(queries) == len(set(queries)), "duplicate queries in dataset"


def test_eval_vault_has_notes():
    assert len(list(VAULT.rglob("*.md"))) >= 6
