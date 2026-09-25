"""RAG evaluation runner (task 1.3).

Indexes the evaluation vault with the configured embedding model (via Ollama)
and computes Recall@k, MRR and negative-case false positives against
``agent/tests/rag_eval/dataset.jsonl``.

Requires a live Ollama with the embedding model pulled.

Usage:
    OLLAMA_HOST=http://localhost:11434 EMBEDDING_MODEL=bge-m3 \
        python agent/scripts/rag_eval.py --top-k 5 [--json results.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

EVAL_DIR = AGENT_DIR / "tests" / "rag_eval"
DATASET_PATH = EVAL_DIR / "dataset.jsonl"
EVAL_VAULT_PATH = EVAL_DIR / "vault"
PROVISIONAL_THRESHOLDS = (0.5, 0.6, 0.7)


def normalize(text: str) -> str:
    """Lowercase and strip accents so keywords match regardless of encoding."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def first_rank(returned_notes: list[str], expected_note: str) -> int | None:
    """1-based rank of the first result from the expected note, or None."""
    for rank, note in enumerate(returned_notes, 1):
        if note == expected_note:
            return rank
    return None


def summarize_positives(rows: list[dict], k: int) -> dict:
    """rows: [{'rank', 'keyword_any', 'keyword_all', 'expected_relevance'}]."""
    total = len(rows)
    if total == 0:
        return {"total": 0}
    return {
        "total": total,
        "recall@1": sum(1 for r in rows if r["rank"] and r["rank"] <= 1) / total,
        "recall@3": sum(1 for r in rows if r["rank"] and r["rank"] <= 3) / total,
        f"recall@{k}": sum(1 for r in rows if r["rank"] and r["rank"] <= k) / total,
        f"mrr@{k}": sum((1.0 / r["rank"]) for r in rows if r["rank"] and r["rank"] <= k) / total,
        f"keyword_any@{k}": sum(1 for r in rows if r["keyword_any"]) / total,
        f"keyword_all@{k}": sum(1 for r in rows if r["keyword_all"]) / total,
        "mean_expected_relevance": sum(r.get("expected_relevance", 0.0) for r in rows) / total,
        "min_expected_relevance": min(r.get("expected_relevance", 0.0) for r in rows),
    }


def summarize_negatives(rows: list[dict]) -> dict:
    """rows: [{'top_relevance': float}] measured on queries with no answer."""
    total = len(rows)
    if total == 0:
        return {"total": 0}
    relevances = [r["top_relevance"] for r in rows]
    summary = {
        "total": total,
        "mean_top_relevance": sum(relevances) / total,
        "max_top_relevance": max(relevances),
    }
    for threshold in PROVISIONAL_THRESHOLDS:
        above = sum(1 for value in relevances if value >= threshold)
        summary[f"false_found@>={threshold}"] = above / total
    return summary


async def run_evaluation(top_k: int) -> dict:
    from src.config import settings
    from src.utils import vault_indexer as vi
    from src.utils import vector_manager as vml

    workdir = Path(tempfile.mkdtemp(prefix="rag_eval_"))
    # Work on a copy: the indexer auto-links related notes by rewriting
    # frontmatter, and the versioned eval vault must stay untouched.
    vault_copy = workdir / "vault"
    shutil.copytree(EVAL_VAULT_PATH, vault_copy)
    settings.vector_db_dir = str(workdir / "vector_db")

    manager = vml.VectorManager()
    await manager.initialize()
    vml.vector_db = manager
    vi.VAULT_PATH = vault_copy
    vi.VAULT_NAME = "eval_vault"
    indexer = vi.VaultIndexer()

    indexed = await indexer.index_all()
    print(
        "Indexed %d notes (%d chunks, %d failures)"
        % (
            indexed["notes_indexed"],
            indexed["total_chunks"],
            indexed["failures"],
        )
    )

    dataset = load_dataset()
    positive_rows: list[dict] = []
    negative_rows: list[dict] = []

    for item in dataset:
        queried = await manager.query(item["query"], top_k=top_k)
        results = queried.get("results", [])
        notes = [r["note_path"] for r in results]
        relevances = [float(r["relevance"]) for r in results]
        contents = normalize(" ".join(r["content"] for r in results))

        if item["relevant"]:
            rank = first_rank(notes, item["expected_note"])
            keywords = [normalize(kw) for kw in item["expected_keywords"]]
            keyword_any = any(kw in contents for kw in keywords)
            keyword_all = all(kw in contents for kw in keywords)
            expected_relevance = max(
                (float(r["relevance"]) for r in results if r["note_path"] == item["expected_note"]),
                default=0.0,
            )
            positive_rows.append(
                {
                    "id": item["id"],
                    "rank": rank,
                    "keyword_any": keyword_any,
                    "keyword_all": keyword_all,
                    "expected_relevance": expected_relevance,
                }
            )
            status = "OK " if rank else "MISS"
            print(
                "[%s] %s rank=%s rel=%.3f keywords(any=%s all=%s) top=%s"
                % (
                    status,
                    item["id"],
                    rank,
                    expected_relevance,
                    keyword_any,
                    keyword_all,
                    notes[:2],
                )
            )
        else:
            top_relevance = max(relevances) if relevances else 0.0
            negative_rows.append({"id": item["id"], "top_relevance": top_relevance})
            print("[NEG] %s top_relevance=%.3f top=%s" % (item["id"], top_relevance, notes[:1]))

    report = {
        "top_k": top_k,
        "embedding_model": settings.embedding_model,
        "chunks_indexed": indexed["total_chunks"],
        "positives": summarize_positives(positive_rows, top_k),
        "negatives": summarize_negatives(negative_rows),
    }
    await manager.close()
    if math.isnan(report["negatives"].get("mean_top_relevance", 0.0)):
        raise RuntimeError("unexpected NaN in metrics")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG evaluation over the eval vault")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    report = asyncio.run(run_evaluation(args.top_k))
    print("\n=== SUMMARY ===")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Saved to %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
