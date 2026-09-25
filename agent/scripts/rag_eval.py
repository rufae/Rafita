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


def l2_sq(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n == 0:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0.0 or var_y == 0.0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


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


def evaluate_thresholds(
    positive_relevances: list[float],
    negative_relevances: list[float],
    thresholds: list[float],
) -> list[dict]:
    """Trade-off table: a positive below the threshold is a false negative
    (correct answer discarded) and a negative at/above it is a false positive
    (irrelevant result offered as an answer)."""
    rows = []
    total_positives = len(positive_relevances)
    total_negatives = len(negative_relevances)
    for threshold in thresholds:
        false_negatives = sum(1 for rel in positive_relevances if rel < threshold)
        false_positives = sum(1 for rel in negative_relevances if rel >= threshold)
        rows.append(
            {
                "threshold": threshold,
                "false_negatives": false_negatives,
                "false_negative_rate": false_negatives / total_positives
                if total_positives
                else 0.0,
                "false_positives": false_positives,
                "false_positive_rate": false_positives / total_negatives
                if total_negatives
                else 0.0,
            }
        )
    return rows


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


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    import httpx

    from src.config import settings

    async with httpx.AsyncClient(timeout=600.0) as client:
        response = await client.post(
            "%s/api/embed" % settings.ollama_host.rstrip("/"),
            json={"model": settings.embedding_model, "input": texts},
        )
        response.raise_for_status()
        return [list(map(float, emb)) for emb in response.json().get("embeddings", [])]


async def _build_indexed_manager():
    """Create a VectorManager over a temp copy of the eval vault and index it."""
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
    return manager, indexed


async def run_metric_check(sample_size: int = 12) -> dict:
    """Verify query/document vector norms and L2-vs-cosine ordering (task 1.4)."""
    from src.config import settings

    manager, indexed = await _build_indexed_manager()
    print("Indexed %d notes (%d chunks)" % (indexed["notes_indexed"], indexed["total_chunks"]))
    collection = manager._collection
    stored = collection.get(include=["embeddings"])
    doc_ids = list(stored["ids"])
    doc_embeddings = [list(map(float, emb)) for emb in stored["embeddings"]]
    id_to_emb = dict(zip(doc_ids, doc_embeddings))

    queries = [item["query"] for item in load_dataset() if item["relevant"]][:sample_size]
    query_vectors = await _embed_texts(queries)

    query_norms = [_norm(vector) for vector in query_vectors]
    doc_norms = [_norm(vector) for vector in doc_embeddings]

    order_matches = 0
    max_l2_delta = 0.0
    correlations: list[float] = []
    for query, query_vector in zip(queries, query_vectors):
        result = collection.query(query_texts=[query], n_results=len(doc_ids))
        chroma_ids = result["ids"][0]
        chroma_distances = result["distances"][0]
        manual_l2 = {cid: l2_sq(query_vector, id_to_emb[cid]) for cid in doc_ids}
        for cid, distance in zip(chroma_ids, chroma_distances):
            max_l2_delta = max(max_l2_delta, abs(float(distance) - manual_l2[cid]))
        order_l2 = sorted(doc_ids, key=lambda cid: manual_l2[cid])
        order_cos = sorted(doc_ids, key=lambda cid: 1.0 - cosine(query_vector, id_to_emb[cid]))
        if order_l2[:5] == order_cos[:5]:
            order_matches += 1
        xs = [manual_l2[cid] for cid in doc_ids]
        ys = [1.0 - cosine(query_vector, id_to_emb[cid]) for cid in doc_ids]
        correlations.append(pearson(xs, ys))

    report = {
        "embedding_model": settings.embedding_model,
        "chunks_indexed": len(doc_ids),
        "collection_metadata": collection.metadata,
        "queries_checked": len(queries),
        "query_norm_min": min(query_norms),
        "query_norm_mean": sum(query_norms) / len(query_norms),
        "query_norm_max": max(query_norms),
        "zero_query_vectors": sum(1 for n in query_norms if n == 0.0),
        "doc_norm_min": min(doc_norms),
        "doc_norm_mean": sum(doc_norms) / len(doc_norms),
        "doc_norm_max": max(doc_norms),
        "top5_order_matches_l2_vs_cosine": order_matches,
        "max_chroma_vs_manual_l2_delta": max_l2_delta,
        "pearson_l2_vs_cosine_min": min(correlations),
        "pearson_l2_vs_cosine_mean": sum(correlations) / len(correlations),
    }
    await manager.close()
    return report


async def run_threshold_sweep(thresholds: list[float]) -> dict:
    """Collect raw relevances once and compute the threshold trade-off (task 1.5)."""
    from src.config import settings

    manager, indexed = await _build_indexed_manager()
    print("Indexed %d notes (%d chunks)" % (indexed["notes_indexed"], indexed["total_chunks"]))
    positives: list[float] = []
    negatives: list[float] = []
    for item in load_dataset():
        queried = await manager.query(item["query"], top_k=5, apply_threshold=False)
        results = queried.get("results", [])
        relevances = [float(r["relevance"]) for r in results]
        if item["relevant"]:
            expected = max(
                (float(r["relevance"]) for r in results if r["note_path"] == item["expected_note"]),
                default=0.0,
            )
            positives.append(expected)
        else:
            negatives.append(max(relevances) if relevances else 0.0)
    await manager.close()
    return {
        "embedding_model": settings.embedding_model,
        "positives": len(positives),
        "negatives": len(negatives),
        "positive_relevances": sorted(positives),
        "negative_relevances": sorted(negatives),
        "sweep": evaluate_thresholds(positives, negatives, thresholds),
    }


async def run_evaluation(top_k: int) -> dict:
    from src.config import settings

    manager, indexed = await _build_indexed_manager()
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
        queried = await manager.query(item["query"], top_k=top_k, apply_threshold=False)
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
    parser.add_argument(
        "--check-metric",
        action="store_true",
        help="check embedding norms and L2-vs-cosine ordering instead of the dataset",
    )
    parser.add_argument("--sample-size", type=int, default=12)
    parser.add_argument(
        "--sweep-thresholds",
        type=str,
        default=None,
        help="comma-separated candidate thresholds, e.g. 0.40,0.45,0.49,0.55",
    )
    args = parser.parse_args()

    if args.sweep_thresholds:
        thresholds = [float(value) for value in args.sweep_thresholds.split(",")]
        sweep_report = asyncio.run(run_threshold_sweep(thresholds))
        print("\n=== THRESHOLD SWEEP ===")
        print("positives=%d negatives=%d" % (sweep_report["positives"], sweep_report["negatives"]))
        print("threshold | FN | FN rate | FP | FP rate")
        for row in sweep_report["sweep"]:
            print(
                "  %.2f    | %2d | %6.1f%% | %2d | %6.1f%%"
                % (
                    row["threshold"],
                    row["false_negatives"],
                    row["false_negative_rate"] * 100,
                    row["false_positives"],
                    row["false_positive_rate"] * 100,
                )
            )
        print(json.dumps(sweep_report, indent=2, ensure_ascii=False))
        return 0

    if args.check_metric:
        metric_report = asyncio.run(run_metric_check(args.sample_size))
        print("\n=== METRIC CHECK ===")
        print(json.dumps(metric_report, indent=2, ensure_ascii=False))
        return 0

    report = asyncio.run(run_evaluation(args.top_k))
    print("\n=== SUMMARY ===")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Saved to %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
