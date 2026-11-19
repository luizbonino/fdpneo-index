# Semantic search: evaluation and decision

- Task: 10.1 (search design §3.7)
- Date: 2026-11-19
- Decision: **go, as an optional feature, off by default.** Before any
  deployment turns it on, run the evaluation again on its own metadata
  (see "What this does not show").

## What was built

- **Model:** a multilingual sentence model, run locally with ONNX Runtime.
  - `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, Apache 2.0.
  - 384 dimensions, more than 50 languages.
  - int8, pinned to a Hugging Face revision; 118 MB on disk.
  - `fdpindex embeddings download <dir>` fetches the variant for the CPU and
    records its SHA-256.
  - No text leaves the deployment. ONNX Runtime's telemetry events are
    switched off when the model loads.
- **Storage:** `search_embedding` in PostgreSQL with pgvector, with an HNSW
  cosine index.
  - The table is created when the feature is switched on, not by a
    migration. Servers without pgvector are unaffected.
  - Embeddings are derived data: a background loop embeds new and changed
    resources (fdp, catalog, dataset, series, service).
- **What is embedded:** the resource's title, keywords, the names of its
  themes and subjects in every language of the label pack, and the start of
  its description.
- **Ranking:** a search with words, sorted by relevance, takes the top 200
  full-text hits and the 200 nearest vectors under the same filters, and
  fuses them by reciprocal rank (k = 60).
  - pgvector 0.8's iterative HNSW scan keeps filtered searches complete.
  - A hit found by meaning alone must be within a cosine distance of 0.6
    (`FDPINDEX_EMBEDDINGS_MAX_DISTANCE`).
  - `semantic=off|hybrid|only` on a request compares the modes.
  - The response says how many hits meaning added (`semantic.added`).
- **Settings:** `FDPINDEX_SEARCH_EMBEDDINGS=local`,
  `FDPINDEX_EMBEDDINGS_MODEL_DIR`, `FDPINDEX_EMBEDDINGS_THREADS`,
  `FDPINDEX_EMBEDDINGS_MAX_DISTANCE`.
- **Server image:** it needs the `semantic` extra. ONNX Runtime has no
  Python 3.15 wheels yet, so build with
  `--build-arg PYTHON_VERSION=3.14 --build-arg EXTRAS=semantic`.
- **Database:** PostgreSQL needs pgvector, for example the
  `pgvector/pgvector:0.8.6-pg18` image.

## How it was measured

- **Corpus:** the 150 synthetic FDPs of `server/bench/seed_demo.py`. They
  hold 12,275 datasets and 12,833 embedded resources.
- **Setup:** a separate pgvector database, a local server, and an Apple M5
  with 10 cores.
- **Queries:** 27, judged exactly. A dataset is relevant when its English
  title names the query's topic: the corpus titles every dataset
  "Topic kind, region, years".
  - 3 use the corpus's own words;
  - 18 are paraphrases ("bowel cancer" for colorectal cancer, "family
    earnings" for household income);
  - 6 are in Dutch, German or French.
- **Script:** `server/bench/semantic_eval.py --url …` reproduces the run.

## Results

| Queries | Mode | P@10 | nDCG@10 | MRR | Recall@100 |
|---|---|---|---|---|---|
| same words (3) | full text | 1.00 | 1.00 | 1.00 | 1.00 |
| same words (3) | hybrid | 1.00 | 1.00 | 1.00 | 1.00 |
| paraphrase (18) | full text | 0.22 | 0.22 | 0.22 | 0.22 |
| paraphrase (18) | hybrid | 1.00 | 1.00 | 1.00 | 0.97 |
| other language (6) | full text | 1.00 | 1.00 | 1.00 | 0.45 |
| other language (6) | hybrid | 1.00 | 1.00 | 1.00 | 1.00 |
| **all (27)** | **full text** | **0.48** | **0.48** | **0.48** | **0.36** |
| **all (27)** | **hybrid** | **1.00** | **1.00** | **1.00** | **0.98** |

Meaning alone (`only`) scores the same as hybrid here.

- **Paraphrases:** full text finds 4 of the 18.
- **Other languages:** full text finds only the datasets that also carry a
  title in that language. Their first hits are right, but they are half
  the relevant ones.
- **Queries with the corpus's words:** fusing changes nothing for them.

**The distance limit.** At 0.5 instead of 0.6, four paraphrases fail
("coronavirus pandemic", "hot cities in summer", "family earnings",
"harvest of wheat"), and hybrid P@10 is 0.86. In every case the right
dataset was the nearest one, at 0.49 to 0.54; the limit cut it off.
Unrelated queries ("quantum chromodynamics", "jazz music", "stock market
crash" and five more) came no closer than 0.73, and at 0.6 they still
return nothing.

**Cost**

| | |
|---|---|
| Query latency, p50 / p95 | full text 21 / 70 ms; hybrid 63 / 95 ms |
| Embedding one query | 1.3 ms |
| Embedding resources | about 87 per second on all cores (37 on one core), for texts of 530 characters on average |
| First embedding of this corpus | about 2.5 minutes |
| Storage | 50 MB for 12,833 resources (`search_embedding` with its index), next to 143 MB for `search_resource` |
| Image | the `semantic` extra adds ONNX Runtime, tokenizers and NumPy; the model is a separate 118 MB download |

**At the scale of the 2-million-resource benchmark,** if about half are
searchable resources:

- the first embedding takes **about 3 hours** of CPU on a machine like
  this one;
- storage grows by roughly **4 GB**.

After that, only changed resources are embedded again. A server with
fewer cores, or x86 without VNNI, is slower; `FDPINDEX_EMBEDDINGS_THREADS`
limits how many cores it uses.

## What this does not show

- **Real metadata.** The corpus is synthetic: short, templated titles and
  topic keywords. Real FDP records are longer, noisier and more varied.
  Domain terms (rare diseases, gene names, ontology codes) may be beyond a
  general-purpose model; MeSH and ORDO names in the label pack help full
  text there.
- **A limit chosen on its own queries.** The 0.6 limit was picked from these
  same queries. A real corpus may need a different one.
- **Facets.** They count full-text matches only, so hits added by meaning
  are not in the facet counts. The response's `semantic.added` says how
  many there are.
- **Deep paging.** Hybrid ranking covers the first 200 hits. Pages beyond
  that, and `cursor` paging, fall back to full text.

## Decision and next steps

**Go, as an optional feature, off by default.** Paraphrases and
cross-language queries are exactly what full text misses. The cost is
modest for a few thousand FDPs: a dependency extra, pgvector, a 118 MB
model, and hours of CPU once at the largest scale.

Before a deployment turns it on:

1. Run `bench/semantic_eval.py` on its own harvested metadata, with
   queries written by people who search it (a few dozen with judged
   results), and check the distance limit.
2. Decide whether the client should show that a hit was found by meaning
   (a small "related" marker), since it may lack the words typed.
