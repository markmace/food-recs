import json
from pathlib import Path

from cost import MODEL

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
    kept = []
    stats = {"keyword_matched": 0, "llm_matched": 0, "dropped": 0}
    for video in videos:
        if keyword_match(video, case["filter_keywords"]):
            kept.append(video)
            stats["keyword_matched"] += 1
        elif classify_relevance(video, case, client, tracker):
            kept.append(video)
            stats["llm_matched"] += 1
        else:
            stats["dropped"] += 1
    return kept, stats


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
            if not isinstance(item.get(key), str):
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


def _cache_matches_fields(cached: dict, field_names: list[str]) -> bool:
    expected = set(field_names)
    for items in cached.values():
        for item in items:
            if not expected.issubset(item.keys()):
                return False
    return True


def load_or_extract(videos: list[dict], case: dict, client, tracker) -> list[dict]:
    cache_path = Path("data/extracted") / f"{case['name']}.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    cached = {}
    if cache_path.exists():
        with open(cache_path) as f:
            for line in f:
                record = json.loads(line)
                cached[record["video_id"]] = record["items"]

        field_names = [f["name"] for f in case["fields"]]
        if not _cache_matches_fields(cached, field_names):
            print(f"Extraction cache for '{case['name']}' doesn't match the current fields "
                  f"config -- discarding it and re-extracting all videos")
            cached = {}
            cache_path.unlink()

    all_items = []
    with open(cache_path, "a") as f:
        for video in videos:
            if video["video_id"] in cached:
                items = cached[video["video_id"]]
            else:
                items = extract_items_from_video(video, case, client, tracker)
                f.write(json.dumps({"video_id": video["video_id"], "items": items}) + "\n")
                f.flush()
            all_items.extend(items)

    return all_items
