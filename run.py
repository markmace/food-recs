import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from cost import CostTracker
from export import export_case
from extract import filter_videos, load_or_extract
from youtube import load_or_fetch_videos


def main():
    parser = argparse.ArgumentParser(description="Extract structured data from a YouTube channel into a CSV.")
    parser.add_argument("case_file", help="Path to a case JSON file, e.g. cases/example.json")
    parser.add_argument("-l", "--limit", type=int, default=None,
                         help="Only process the first N fetched videos (for a quick test)")
    args = parser.parse_args()

    load_dotenv()
    yt_api_key = os.environ.get("YT_API_KEY")
    if not yt_api_key:
        sys.exit("Missing YT_API_KEY -- set it in .env")

    case_path = Path(args.case_file)
    try:
        case = json.loads(case_path.read_text())
    except FileNotFoundError:
        sys.exit(f"Case file not found: {case_path}")
    except json.JSONDecodeError as e:
        sys.exit(f"Case file {case_path} is not valid JSON: {e}")

    client = anthropic.Anthropic()  # raises its own clear error if ANTHROPIC_API_KEY unset
    tracker = CostTracker()

    videos = load_or_fetch_videos(case, yt_api_key)
    print(f"Fetched {len(videos)} videos total")

    # CLI --limit wins if both are set; case-file "limit" is the default; neither means no limit.
    limit = args.limit if args.limit is not None else case.get("limit")
    if limit:
        videos = videos[:limit]
        source = "--limit" if args.limit is not None else "case 'limit'"
        print(f"Limiting to first {len(videos)} videos ({source})")

    filtered, filter_stats = filter_videos(videos, case, client, tracker)
    print(f"Keyword-matched: {filter_stats['keyword_matched']}, "
          f"LLM-matched: {filter_stats['llm_matched']}, "
          f"Dropped: {filter_stats['dropped']}")

    items = load_or_extract(filtered, case, client, tracker)

    # Each run gets its own timestamped CSV/cost report so repeated test runs
    # don't clobber each other. The extraction cache (data/extracted/<case>.jsonl)
    # is intentionally NOT timestamped -- that's what makes reruns cheap.
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_base = Path(case["output_csv"])
    output_base.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_base.with_name(f"{output_base.stem}_{run_id}{output_base.suffix}")
    cost_path = output_base.with_name(f"{output_base.stem}_{run_id}_cost.json")

    export_case(items, case, str(csv_path))
    tracker.write(case, str(cost_path))
    print(f"Cost report written to: {cost_path}")


if __name__ == "__main__":
    main()
