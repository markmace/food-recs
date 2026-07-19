# 🍜 food-recs

Turn a food creator's YouTube videos into a clean, structured CSV of restaurant
recommendations — place names, neighborhoods, sentiment, and source links, deduped
across videos.

Built for a first run against [5 AM Ramen](https://www.youtube.com/@5amramen)'s Tokyo
videos, but works for any channel — just point it at a new case file.

## How it works

```
YouTube API  →  filter  →  Claude (Haiku)  →  dedupe  →  CSV
```

1. **Fetch** — pulls every video (title, description, publish date) from a channel via
   the official YouTube Data API. No downloads, no transcript scraping.
2. **Filter** — keeps videos matching your keywords; anything ambiguous gets a cheap
   yes/no relevance check from Claude instead of being silently dropped.
3. **Extract** — Claude reads each video's title/description and pulls out structured
   shop details (name, neighborhood, category, sentiment, confidence, etc).
4. **Dedupe & export** — merges the same shop mentioned across multiple videos and
   writes one row per unique place to a CSV.

Results are cached along the way (`data/raw/`, `data/extracted/`), so re-running a case
is fast and doesn't re-spend API calls.

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

```bash
uv run python run.py cases/tokyo_ramen.json
```

This writes `data/tokyo_ramen.csv` and prints a summary:

```
Fetched 142 videos total
Keyword-matched: 38, LLM-matched: 6, Dropped: 98

== Summary ==
Total videos fetched: 142
Filtered in: 44 (keyword: 38, LLM: 6, dropped: 98)
Unique shops: 31
Low-confidence shops: 4
CSV written to: data/tokyo_ramen.csv
```

## Adding a new case

A "case" is just a JSON file describing what channel to pull and what to filter for:

```json
{
  "name": "osaka_ramen",
  "source": "youtube",
  "channel": "Some Other Channel",
  "filter_keywords": ["osaka", "namba", "umeda"],
  "output_csv": "data/osaka_ramen.csv"
}
```

Drop it in `cases/` and run it the same way:

```bash
uv run python run.py cases/osaka_ramen.json
```

## Notes

- Caching is **all-or-nothing per case** — once `data/raw/<case>.jsonl` exists, reruns
  won't pick up new videos the channel posts later. Delete that file to force a refetch.
- Extraction uses `claude-haiku-4-5` — fast and cheap, good fit for this kind of
  structured pulling.
