# food-recs

Extract structured data from a YouTube channel into a CSV — filter videos, pull out
items with Claude using fields *you* define, optionally dedupe across videos, and write
one row per item.

Example uses: every product a reviewer covers, every place mentioned in travel videos,
every bowl of ramen featured in a food channel, tools referenced in a tutorial channel, etc.

## How it works

```
YouTube API  →  filter  →  Claude (Haiku)  →  (optional dedupe)  →  CSV
```

1. **Fetch** — pulls every video (title, description, publish date) from a channel via
   the official YouTube Data API. No downloads, no transcript scraping.
2. **Filter** — keeps videos matching your keywords; anything ambiguous gets a cheap
   yes/no relevance check from Claude instead of being silently dropped.
3. **Extract** — Claude reads each video's title/description and pulls out whatever
   fields you defined in the case file (see below).
4. **Export** — if you configured `dedupe_fields`, merges items that match on those
   fields across videos; otherwise every extracted item becomes its own row. Writes the
   CSV.

Results are cached along the way (`data/raw/`, `data/extracted/`), so re-running a case
is fast and doesn't re-spend API calls.

Every run writes its own timestamped CSV and cost report (e.g.
`data/my_case_20260719_153000.csv` / `..._cost.json`), so running the same case multiple
times — while tuning fields, testing with `--limit`, etc. — never overwrites a previous
run's output. The cost report has a token and dollar breakdown by pipeline stage
(filtering vs. extraction) for the API calls actually made *that run* — a fully-cached
rerun reports close to $0.

## Setup

**1. Get the two API keys you'll need:**

| Key | Where to get it |
|---|---|
| `YT_API_KEY` | [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com) → enable the **YouTube Data API v3** → Credentials → Create API key |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/keys) → Create Key |

**2. Install dependencies** (using [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
```

**3. Add your keys:**

```bash
cp .env.example .env
# then edit .env and paste in your keys
```

## Usage

Copy the example case and configure it for a channel and topic you care about:

```bash
cp cases/example.json cases/my_case.json
# edit my_case.json — set channel, topic, fields, filter_keywords, output_csv
uv run python run.py cases/my_case.json
```

Name a case `cases/*.local.json` (e.g. `cases/tokyo_ramen.local.json`) to keep it out of
git — that pattern is already in `.gitignore`, handy for cases you're iterating on but
don't want in the public repo.

## Case file format

```json
{
  "name": "my_case",
  "source": "youtube",
  "channel": "Channel Name",
  "topic": "product reviews",
  "item_description": "one row per distinct product reviewed in the video",
  "filter_keywords": ["review", "hands-on"],
  "fields": [
    {"name": "product", "description": "Name of the product being reviewed"},
    {"name": "category", "description": "Type or category of product, or null"},
    {"name": "notes", "description": "Relevant details: specs, price, use case, etc."},
    {"name": "verdict", "description": "The reviewer's overall opinion or recommendation"}
  ],
  "dedupe_fields": ["product"],
  "output_csv": "data/my_case.csv",
  "limit": 5
}
```

`topic` drives the LLM prompts — describe what you're trying to collect in plain
language. `filter_keywords` narrow which videos get processed.

`fields` defines your output schema — Claude extracts exactly the fields you list, using
your descriptions as instructions. There's no fixed schema; name and describe whatever
columns make sense for your case. `item_description` tells Claude what one row
represents — e.g. `"one row per distinct bowl of ramen ordered or featured in the
video"` if you want per-dish granularity rather than one row per place/video. Every row
also automatically gets `channel`, `video`, `video_url`, and `published_at` — you don't
need to list those yourself.

`dedupe_fields` (optional) is a list of your field names to merge on across videos —
e.g. `["product"]` merges the same product mentioned in multiple videos into one row
(unioning source videos). Fuzzy-matches on those fields only; all listed fields must
agree (or be null on both sides) for two items to merge, and unrelated fields are kept
from the merged records. Leave it out and every extracted item becomes its own row with
no merging — the right choice whenever repeats across videos are expected and
*shouldn't* collapse (e.g. one row per bowl of ramen — the same shop across two videos
is two different bowls, not a duplicate).

`limit` (optional) caps the pipeline to the first N fetched videos — handy for a quick,
cheap test before running the full channel. You can also pass `--limit`/`-l` on the
command line instead of (or in addition to) setting it in the case file:

```bash
uv run python run.py cases/my_case.json --limit 5
uv run python run.py cases/my_case.json -l 5
```

If neither is set, the full video list is processed. If both are set, `--limit` wins.
Either way, use a separate case file (distinct `name`/`output_csv`) for test runs so
they don't overwrite your real cache/output — see `cases/test_example.json`.

## Notes

- Caching is **all-or-nothing per case** — once `data/raw/<case>.jsonl` exists, reruns
  won't pick up new videos the channel posts later. Delete that file to force a refetch.
- Extraction uses `claude-haiku-4-5` — fast and cheap, good fit for this kind of
  structured pulling.
- Cost reports use hardcoded per-million-token pricing (`cost.py`) — update it there if
  pricing changes or you switch models.
