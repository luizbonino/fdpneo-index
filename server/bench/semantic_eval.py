"""Evaluate semantic search on the synthetic corpus (TASKS 10.1).

Runs a judged query set against a running index with semantic search on, in
three modes (full text, hybrid, meaning only), and reports precision@10,
nDCG@10, MRR, recall@100 and latency. A dataset is relevant to a query when
its English title names the query's topic (the synthetic corpus titles every
dataset "<Topic> <kind>, <region>, <years>"), so the judgments are exact.

    python semantic_eval.py --url http://127.0.0.1:8095 --out results.md

The corpus is synthetic: its titles are short and templated, and its keywords
already carry some synonyms. Results on real FDP metadata will differ.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# (query, relevant topics, kind of query)
QUERIES: list[tuple[str, tuple[str, ...], str]] = [
    ("breast cancer", ("breast cancer",), "same words"),
    ("air quality", ("air quality",), "same words"),
    ("household income", ("household income",), "same words"),
    ("tumour registry", ("breast cancer", "colorectal cancer"), "paraphrase"),
    ("bowel cancer", ("colorectal cancer",), "paraphrase"),
    ("diabetes mellitus", ("type 2 diabetes",), "paraphrase"),
    ("cardiac insufficiency", ("heart failure",), "paraphrase"),
    ("memory loss in older people", ("dementia",), "paraphrase"),
    ("breathing problems in kids", ("asthma in children",), "paraphrase"),
    ("inherited metabolic diseases", ("rare metabolic disorders",), "paraphrase"),
    ("DNA sequencing", ("whole-genome sequencing",), "paraphrase"),
    ("coronavirus pandemic", ("covid-19",), "paraphrase"),
    ("teenage depression", ("mental health of adolescents",), "paraphrase"),
    ("air pollution", ("air quality",), "paraphrase"),
    ("water pollution in rivers", ("river water quality",), "paraphrase"),
    ("hot cities in summer", ("urban heat islands",), "paraphrase"),
    ("family earnings", ("household income",), "paraphrase"),
    ("unemployment", ("labour market participation",), "paraphrase"),
    ("harvest of wheat", ("crop yields",), "paraphrase"),
    ("electricity use at home", ("household energy consumption",), "paraphrase"),
    ("bicycle commuting", ("cycling and commuting",), "paraphrase"),
    ("luchtkwaliteit", ("air quality",), "other language"),
    ("Luftqualität", ("air quality",), "other language"),
    ("hartfalen", ("heart failure",), "other language"),
    ("Darmkrebs", ("colorectal cancer",), "other language"),
    ("astma bij kinderen", ("asthma in children",), "other language"),
    ("diabète de type 2", ("type 2 diabetes",), "other language"),
]
MODES = ("off", "hybrid", "only")


@dataclass
class Score:
    precision: float
    ndcg: float
    rr: float
    recall: float
    millis: float


def relevant(title: str, topics: tuple[str, ...]) -> bool:
    lowered = title.lower()
    return any(lowered.startswith(t) for t in topics)


def search(base: str, q: str, mode: str, size: int) -> dict:  # type: ignore[type-arg]
    query = urllib.parse.urlencode(
        {"q": q, "type": "dataset", "size": size, "facets": "none", "semantic": mode, "lang": "en"}
    )
    if not base.startswith(("http://", "https://")):
        raise SystemExit("--url must be http(s)")
    with urllib.request.urlopen(f"{base}/index/search?{query}", timeout=60) as response:  # noqa: S310 - checked above
        return json.load(response)


def total_relevant(base: str, topics: tuple[str, ...]) -> int:
    count = 0
    for topic in topics:
        body = search(base, f'title:"{topic}"', "off", 1)
        count += int(body["totalResults"])
    return count


def score(base: str, q: str, topics: tuple[str, ...], mode: str, relevant_total: int) -> Score:
    started = time.perf_counter()
    first = search(base, q, mode, 100)
    millis = (time.perf_counter() - started) * 1000
    marks = [relevant(h["title"], topics) for h in first["hits"]]
    top = marks[:10]
    precision = sum(top) / 10
    dcg = sum(1 / math.log2(i + 2) for i, hit in enumerate(top) if hit)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(10, relevant_total)))
    rr = next((1 / (i + 1) for i, hit in enumerate(marks) if hit), 0.0)
    recall = sum(marks) / min(100, relevant_total) if relevant_total else 0.0
    return Score(precision, dcg / ideal if ideal else 0.0, rr, recall, millis)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8095")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    for mode in MODES:  # warm up
        search(base, "warm up", mode, 10)
    rows: dict[str, dict[str, Score]] = {}
    for q, topics, _kind in QUERIES:
        total = total_relevant(base, topics)
        rows[q] = {mode: score(base, q, topics, mode, total) for mode in MODES}
    lines = [
        "| Query kind | Mode | P@10 | nDCG@10 | MRR | Recall@100 |",
        "|---|---|---|---|---|---|",
    ]
    for kind in ("same words", "paraphrase", "other language", "all"):
        chosen = [q for q, _, k in QUERIES if kind in {k, "all"}]
        for mode in MODES:
            scores = [rows[q][mode] for q in chosen]
            lines.append(
                f"| {kind} ({len(chosen)}) | {mode} | "
                f"{statistics.mean(s.precision for s in scores):.2f} | "
                f"{statistics.mean(s.ndcg for s in scores):.2f} | "
                f"{statistics.mean(s.rr for s in scores):.2f} | "
                f"{statistics.mean(s.recall for s in scores):.2f} |"
            )
    lines += ["", "| Mode | Latency p50 | p95 |", "|---|---|---|"]
    for mode in MODES:
        times = sorted(rows[q][mode].millis for q in rows)
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        lines.append(f"| {mode} | {statistics.median(times):.0f} ms | {p95:.0f} ms |")
    lines += ["", "| Query | full text P@10 | hybrid P@10 | meaning P@10 |", "|---|---|---|---|"]
    for q, _, _ in QUERIES:
        r = rows[q]
        lines.append(
            f"| {q} | {r['off'].precision:.1f} | {r['hybrid'].precision:.1f} | {r['only'].precision:.1f} |"
        )
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
