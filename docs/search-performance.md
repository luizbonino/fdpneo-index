# Search performance at scale

The faceted search was measured on a synthetic index of 1 000 FDPs (task
6.15, search design §7). This page gives the numbers, what was changed
because of them, and what operators should plan for.

## Set-up

- **Corpus.** `server/bench/search_bench.py` generated 1 000 FDPs of 2 000
  harvested nodes each. Each FDP has one FDP node, 1–8 catalogs, about 650
  datasets, 1 300 distributions and a few data services, shaped like the test
  fixtures. Words, keywords and themes follow Zipf distributions, so terms
  are as selective as in real text. Every node went through the real
  projection and the indexer's write path.
- **Result.** 2.0 M resources, 15 M facet values and 2.6 M labels.
  - The search tables take about 14 GB with indexes: `search_resource` 5.0 GB,
    `search_value` 8.0 GB (2.8 GB of it heap) and `search_label` 1.0 GB.
  - Loading took 7.5 minutes with 7 worker processes.
- **Machine.** PostgreSQL 18 in Docker on a laptop, in an 8 GB VM shared with
  other services, with `shared_buffers=1GB` and `work_mem=32MB`. The data
  does not fit in memory, so many reads go to disk.
- **Two numbers per search.**
  - *First* is the first run, after other searches have filled the cache. On
    this machine it mostly measures disk reads.
  - *Repeat* is the median of the next two runs. Repeats without words or
    dates also hit the 60-second facet cache.
- Each search returns the first page of 20 results with the default facets.

## Results

| Search | Matches | Before: first / repeat | After: first / repeat |
|---|---:|---:|---:|
| Unfiltered first page | 683 451 | 40.1 s / 30.3 s | 2.1 s / 0.05 s |
| Datasets tab | 657 762 | 29.5 s / 22.5 s | 0.8 s / 0.04 s |
| Unfiltered, no facets | 683 451 | 1.7 s / 1.7 s | 0.03 s / 0.03 s |
| Theme filter (Health) | 338 215 | 12.6 s / 24.0 s | 3.9 s / 0.05 s |
| Theme + format + licence | 189 656 | 24.0 s / 28.7 s | 8.2 s / 0.06 s |
| Not Health | 345 236 | 53.0 s / 26.0 s | 10.7 s / 0.06 s |
| Modified since 2025 | 125 494 | 9.1 s / 7.4 s | 2.4 s / 0.8 s |
| Reachable FDPs only | 298 670 | 14.5 s / 12.0 s | 1.4 s / 0.06 s |
| One FDP | 687 | 0.6 s / 0.1 s | 1.3 s / 0.02 s |
| Word in 74 % of resources ("health") | 506 243 | 80.9 s / 22.1 s | 4.3 s / 2.0 s |
| Word in 1.5 % ("dementia") | 29 293 | 2.0 s / 1.5 s | 5.0 s / 1.6 s |
| Two words | 22 078 | 2.4 s / 1.8 s | 3.7 s / 1.1 s |
| Phrase of two common words | 19 052 | 42.9 s / 29.2 s | 5.5 s / 4.4 s |
| Partial word ("gen") | 169 251 | 11.8 s / 10.1 s | 4.5 s / 3.0 s |
| Word + Health + datasets | 59 288 | 29.9 s / 60.0 s | 4.3 s / 2.9 s |
| Rare word | 452 | 0.4 s / 0.3 s | 0.2 s / 0.06 s |
| Page 500, by title | 683 451 | 1.8 s / 2.1 s | 0.6 s / 0.04 s |
| Facet dialog, keyword filter | – | 3.5 s / 2.1 s | 0.15 s / 0.01 s |
| Resource page | – | 0.05 s / 0.05 s | 0.07 s / 0.03 s |

Before the changes, most searches over the whole index took 10–80 seconds,
so with the default 5-second limit they would have failed. After them, every
repeated search but the phrase takes 3 seconds or less. A first run on a cold
cache still takes several seconds for very common words, phrases and
combined filters.

## What changed

Migration `0004` and `search/query.py` contain these changes.

- **Facets without words or dates read `search_value` alone.**
  - Each value row now holds its resource's kind and FDP.
  - An index on `(field, kind, value)`, which includes the FDP and the
    resource id, answers the type tabs, FDP filters and field filters without
    reading any resource row.
  - Before, each facet looked up the resource of every value, 2 million
    lookups for the format facet.
- **Facets of searches with words or dates count over the matching ids.** The
  ids are found once per request. Every facet then counts over them by
  primary key, instead of re-checking the words for each value row.
  - **Up to 50 000 matches**, the counts are exact.
  - **Above 50 000**, they come from a sample of about 10 000 matches and are
    scaled up. The API marks them `"approximate": true`, and the page shows
    "≈" with an explanation.
  - The sample is every k-th id, or a `TABLESAMPLE SYSTEM` page sample when
    that reads at most 2 % of the table. Both are deterministic, so counts do
    not change from page to page.
- **Ranking is bounded for very broad searches.**
  - Above 50 000 matches, relevance is computed for the 5 000 most recently
    modified matches only. The response carries `ranking.candidates`, the
    page explains it, and paging stops there.
  - The score uses `ts_rank` instead of `ts_rank_cd`. Cover density cost ten
    times as much, and the title, keyword and description weights carry the
    signal.
- **The two sort orders have indexes covering every sort key.**
  `ix_search_resource_recent` serves newest-first and
  `ix_search_resource_title_lower` the title sort. A page stops after its
  rows, even when thousands of resources share one date.
- **The planner sees joint statistics on `(field, value)`.** It used to expect
  8 000 resources with a common theme where there were 338 000, and picked a
  nested loop.
- **Searches set `random_page_cost = 4` for their own transaction.**
  - Search rows are wide: the text vectors are stored in the row, about 3 KB
    each. Fetching them one by one costs more than an SSD setting of 1.1
    suggests.
  - With 1.1, the planner tested a word on each of 285 000 resources with a
    common theme, taking 8.6 s. With 4 it uses the full-text index, taking
    0.4 s.
- **Facet counts and totals of searches without words or dates are cached**
  in the server process for 60 seconds. A change in the index starts afresh.

## For operators

- **Memory.** At 2 M resources the search tables and their indexes take about
  14 GB. Searches stay under a second when the hot part, the indexes and
  `search_value`, fits in memory. Give PostgreSQL enough RAM for the OS page
  cache to hold most of it. With less, the first searches after a restart or
  after unrelated work take several seconds.
- **The 5-second limit.**
  - Anonymous searches are cut off after `FDPINDEX_SEARCH_TIMEOUT_SECONDS`
    (default 5) with a 503; administrators get
    `FDPINDEX_SEARCH_ADMIN_TIMEOUT_SECONDS` (default 30).
  - On a cold cache, the slowest shapes above (a phrase of very common words,
    several combined filters) come close to the limit. Raise it if users meet
    "the search took too long" more than now and then.
- **Migration `0004` rewrites `search_value`.**
  - On 15 M values it took 3 minutes, then `VACUUM ANALYZE search_value` took
    18 seconds. The server applies migrations when it starts, before it
    serves, so plan a few minutes of downtime at this size.
  - The rewrite leaves the table at about twice its size. To reclaim the
    space, run `VACUUM (FULL) search_value` in a quiet hour; it locks the
    table while it runs.
- **`maintenance_work_mem` barely matters.** Rebuilding one full-text index
  over 2 M resources took 12.4 s with 64 MB, 11.1 s with 256 MB and 10.8 s
  with 1 GB. The default is fine.
- **`random_page_cost`** may stay at your SSD setting. Searches set their own.

## Known limits and next steps

- **Cold first runs** of phrases of very common words, and of combined
  filters, take 5–10 seconds on the laptop VM above.
- **Storing the text vectors outside the row** could shrink `search_resource`
  about tenfold for scans. That could be done with a smaller
  `toast_tuple_target` or a separate table, at some cost to ranking. To be
  measured with this benchmark.
- **The facet cache is per process.** With several server replicas each one
  warms its own.

## Running the benchmark

```sh
cd server
# a throwaway PostgreSQL in Docker, the full corpus (about 10 minutes), every shape:
uv run python bench/search_bench.py --entries 1000 --nodes 2000 --gin --out bench/out
# again on an existing, migrated database, only some shapes:
uv run python bench/search_bench.py --database-url postgresql+asyncpg://… --skip-load --only '^text'
```

The report (`report.json`) lists, for every search, each SQL statement's
time. Next to it, each search's slowest statement is saved with its
`EXPLAIN (ANALYZE, BUFFERS)` plan.
