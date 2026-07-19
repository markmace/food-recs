import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from export import export_case
from extract import filter_videos, load_or_extract
from youtube import load_or_fetch_videos


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python run.py cases/<case>.json")

    load_dotenv()
    yt_api_key = os.environ.get("YT_API_KEY")
    if not yt_api_key:
        sys.exit("Missing YT_API_KEY -- set it in .env")

    case_path = Path(sys.argv[1])
    try:
        case = json.loads(case_path.read_text())
    except FileNotFoundError:
        sys.exit(f"Case file not found: {case_path}")
    except json.JSONDecodeError as e:
        sys.exit(f"Case file {case_path} is not valid JSON: {e}")

    client = anthropic.Anthropic()  # raises its own clear error if ANTHROPIC_API_KEY unset

    videos = load_or_fetch_videos(case, yt_api_key)
    print(f"Fetched {len(videos)} videos total")

    filtered, filter_stats = filter_videos(videos, case, client)
    print(f"Keyword-matched: {filter_stats['keyword_matched']}, "
          f"LLM-matched: {filter_stats['llm_matched']}, "
          f"Dropped: {filter_stats['dropped']}")

    places = load_or_extract(filtered, case, client)
    export_case(places, case)


if __name__ == "__main__":
    main()
