import concurrent.futures
import hashlib
import json
import threading
from pathlib import Path

from cost import MODEL

MAX_WORKERS = 8  # concurrent Claude calls -- these are independent per-video requests;
                  # the SDK's built-in retry handles any transient 429s from going this wide

EXTRACTION_PROMPT = """You are extracting structured data from a YouTube video for a personal \
database.

Topic: {topic}
Channel: {channel}
Video title: {title}
Video URL: {url}
Published: {published_at}
Video description:
{description}

Extract {item_description}. A video may contain multiple such items, or none (e.g. an intro, \
off-topic video, or one with nothing specific to extract).

For each item, output a JSON object with these fields:
{field_list}

Respond with ONLY a JSON array of these objects (use [] if nothing fits). Do not include any \
other text, explanation, or markdown formatting."""


def build_field_list(fields: list[dict]) -> str:
    return "\n".join(f"- {f['name']}: {f['description']} (use null if not mentioned)" for f in fields)


def keyword_match(video: dict, keywords: list[str]) -> bool:
    haystack = f"{video['title']} {video['description']}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def classify_relevance(video: dict, case: dict, client, tracker) -> bool:
    prompt = f"""A YouTube channel called "{case['channel']}" posted a video. We're collecting \
videos about: {case['topic']}

This video didn't match our keyword list ({', '.join(case['filter_keywords'])}):

Title: {video['title']}
Description: {video['description'][:1500]}

Does this video likely mention one or more specific subjects that fit that topic? Answer with \
exactly one word: yes or no."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    tracker.record("filter", response.usage)
    answer = response.content[0].text.strip().lower()
    return answer.startswith("y")


def filter_videos(videos: list[dict], case: dict, client, tracker) -> tuple[list[dict], dict]:
    stats = {"keyword_matched": 0, "llm_matched": 0, "dropped": 0}
    stats_lock = threading.Lock()

    keep: dict[str, bool] = {}
    to_check = []
    for video in videos:
        if keyword_match(video, case["filter_keywords"]):
            keep[video["video_id"]] = True
            stats["keyword_matched"] += 1
        else:
            to_check.append(video)

    def check(video: dict) -> tuple[str, bool]:
        relevant = classify_relevance(video, case, client, tracker)
        with stats_lock:
            stats["llm_matched" if relevant else "dropped"] += 1
        return video["video_id"], relevant

    if to_check:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for video_id, relevant in pool.map(check, to_check):
                keep[video_id] = relevant

    return [v for v in videos if keep.get(v["video_id"])], stats


def try_parse_json_array(text: str) -> list | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def validate_items(items: list, video: dict, case: dict, url: str) -> list[dict]:
    field_names = [f["name"] for f in case["fields"]]
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in field_names:
            value = item.get(key)
            if isinstance(value, bool):
                item[key] = "true" if value else "false"
            elif isinstance(value, (int, float)):
                item[key] = str(value)
            elif not isinstance(value, str):
                item[key] = None
        if not any(item.get(key) for key in field_names):
            continue
        item["channel"] = case["channel"]
        item["source_url"] = url
        item["source_title"] = video["title"]
        item["published_at"] = video["published_at"]
        result.append(item)
    return result


def extract_items_from_video(video: dict, case: dict, client, tracker) -> list[dict]:
    url = f"https://www.youtube.com/watch?v={video['video_id']}"
    item_description = case.get("item_description", f"distinct items related to {case['topic']}")
    prompt = EXTRACTION_PROMPT.format(
        topic=case["topic"], channel=case["channel"], title=video["title"], url=url,
        published_at=video["published_at"], description=video["description"],
        item_description=item_description, field_list=build_field_list(case["fields"]),
    )
    messages = [{"role": "user", "content": prompt}]
    response = client.messages.create(model=MODEL, max_tokens=2048, messages=messages)
    tracker.record("extract", response.usage)
    text = response.content[0].text
    items = try_parse_json_array(text)

    if items is None:
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content":
            "That was not valid JSON. Respond with ONLY a valid JSON array "
            "(or [] if none), no other text."})
        response = client.messages.create(model=MODEL, max_tokens=2048, messages=messages)
        tracker.record("extract", response.usage)
        items = try_parse_json_array(response.content[0].text)
        if items is None:
            print(f"WARNING: giving up on malformed JSON for video {video['video_id']} after retry")
            return []

    return validate_items(items, video, case, url)


def _fields_signature(case: dict) -> str:
    payload = json.dumps(
        {"fields": case["fields"], "item_description": case.get("item_description")},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_or_extract(videos: list[dict], case: dict, client, tracker) -> list[dict]:
    cache_dir = Path("data/extracted")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{case['name']}.jsonl"
    schema_path = cache_dir / f"{case['name']}.schema"

    # The cache stores extracted VALUES, not the field instructions used to produce them,
    # so a wording-only change (same field names, tightened description) is invisible
    # unless we fingerprint the full fields config separately.
    signature = _fields_signature(case)
    if cache_path.exists() and (not schema_path.exists() or schema_path.read_text().strip() != signature):
        print(f"Extraction cache for '{case['name']}' doesn't match the current fields "
              f"config -- discarding it and re-extracting all videos")
        cache_path.unlink()
    schema_path.write_text(signature)

    cached = {}
    if cache_path.exists():
        with open(cache_path) as f:
            for line in f:
                record = json.loads(line)
                cached[record["video_id"]] = record["items"]

    results = dict(cached)
    to_extract = [v for v in videos if v["video_id"] not in cached]

    if to_extract:
        write_lock = threading.Lock()
        done = 0
        with open(cache_path, "a") as f, \
                concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(extract_items_from_video, v, case, client, tracker): v
                       for v in to_extract}
            for future in concurrent.futures.as_completed(futures):
                video = futures[future]
                items = future.result()
                results[video["video_id"]] = items
                with write_lock:
                    f.write(json.dumps({"video_id": video["video_id"], "items": items}) + "\n")
                    f.flush()
                    done += 1
                    if done % 25 == 0 or done == len(to_extract):
                        print(f"  extracted {done}/{len(to_extract)}")

    all_items = []
    for video in videos:
        all_items.extend(results.get(video["video_id"], []))
    return all_items
