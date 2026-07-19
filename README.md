# food-recs

Extract structured data from a YouTube channel into a CSV — filter videos, pull out
subjects with Claude, dedupe across videos, and write one row per item.

Example uses: every product a reviewer covers, every place mentioned in travel videos,
tools referenced in a tutorial channel, etc.

## How it works

```
YouTube API  →  filter  →  Claude (Haiku)  →  dedupe  →  CSV
```

1. **Fetch** — pulls every video (title, description, publish date) from a channel via
   the official YouTube Data API. No downloads, no transcript scraping.
2. **Filter** — keeps videos matching your keywords; anything ambiguous gets a cheap
   yes/no relevance check from Claude instead of being silently dropped.
3. **Extract** — Claude reads each video's title/description and pulls out structured
   fields (subject, category, details, sentiment, confidence, etc).
4. **Dedupe & export** — merges the same subject mentioned across multiple videos and
   writes one row per unique item to a CSV.

Results are cached along the way (`data/raw/`, `data/extracted/`), so re-running a case
is fast and doesn't re-spend API calls.

Every run also writes a cost report next to the CSV (e.g. `data/my_case_cost.json`) with
a token and dollar breakdown by pipeline stage (filtering vs. extraction) for the API
calls actually made *that run* — a fully-cached rerun reports close to $0.

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
# edit my_case.json — set channel, topic, filter_keywords, output_csv
uv run python run.py cases/my_case.json
```

## Case file format

```json
{
  "name": "my_case",
  "source": "youtube",
  "channel": "Channel Name",
  "topic": "product reviews",
  "filter_keywords": ["review", "hands-on"],
  "output_csv": "data/my_case.csv"
}
```

`topic` drives the LLM prompts — describe what you're trying to collect in plain
language. `filter_keywords` narrow which videos get processed.

## Notes

- Caching is **all-or-nothing per case** — once `data/raw/<case>.jsonl` exists, reruns
  won't pick up new videos the channel posts later. Delete that file to force a refetch.
- Extraction uses `claude-haiku-4-5` — fast and cheap, good fit for this kind of
  structured pulling.
- Cost reports use hardcoded per-million-token pricing (`cost.py`) — update it there if
  pricing changes or you switch models.
