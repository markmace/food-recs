import json
from pathlib import Path

MODEL = "claude-haiku-4-5"

VALID_SENTIMENTS = {"loved", "liked", "mixed", "negative"}
VALID_CONFIDENCE = {"high", "medium", "low"}

EXTRACTION_PROMPT = """You are extracting structured data about ramen shops mentioned in a \
YouTube video, for a personal database of ramen recommendations.

Channel: {channel}
Video title: {title}
Video URL: {url}
Published: {published_at}
Video description:
{description}

Identify every distinct ramen shop mentioned in the title/description as being featured, \
visited, or recommended in this video. A video may feature multiple shops, or none (e.g. a \
compilation intro, a non-ramen video, or a video where no specific restaurant is named).

For each shop, output a JSON object with these fields:
- place_name_en: the shop's name in English/romaji, or null if unknown
- place_name_local: the shop's name in Japanese script if given, or null
- neighborhood: the neighborhood/area mentioned (e.g. Shibuya, Ebisu), or null if not mentioned
- city: the city the shop is in, inferred from context. Keep this to the city name only \
(e.g. "Tokyo") -- put neighborhood-level detail in the neighborhood field instead. Use null \
only if truly unclear.
- category: the ramen style if mentioned (e.g. shoyu, shio, tonkotsu, tsukemen, miso), or null
- sentiment: one of "loved", "liked", "mixed", "negative", or null if unclear
- price_signal: any price info mentioned (e.g. "¥980", "budget", "pricey"), or null
- maps_url: a Google Maps URL for the shop if one appears in the description, or null
- confidence: "high" if clearly and unambiguously named, "medium" if reasonably confident, \
"low" if you are guessing/inferring

Respond with ONLY a JSON array of these objects (use [] if no shops are featured). Do not \
include any other text, explanation, or markdown formatting."""


def keyword_match(video: dict, keywords: list[str]) -> bool:
    haystack = f"{video['title']} {video['description']}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def classify_relevance(video: dict, case: dict, client) -> bool:
    prompt = f"""A YouTube channel called "{case['channel']}" posted a video. We're looking \
for videos that feature specific ramen shop recommendations, but this one's title/description \
didn't match our keyword list ({', '.join(case['filter_keywords'])}).

Title: {video['title']}
Description: {video['description'][:1500]}

Does this video likely feature one or more specific ramen restaurants? Answer with exactly \
one word: yes or no."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    answer = response.content[0].text.strip().lower()
    return answer.startswith("y")


def filter_videos(videos: list[dict], case: dict, client) -> tuple[list[dict], dict]:
    kept = []
    stats = {"keyword_matched": 0, "llm_matched": 0, "dropped": 0}
    for video in videos:
        if keyword_match(video, case["filter_keywords"]):
            kept.append(video)
            stats["keyword_matched"] += 1
        elif classify_relevance(video, case, client):
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


# String-typed fields that must actually be strings (or None) by the time they
# reach export.py's dedupe/CSV logic -- Claude occasionally nests an object or
# list where a plain string was asked for, and downstream code assumes str.
STRING_FIELDS = ("place_name_en", "place_name_local", "neighborhood", "city",
                  "category", "price_signal", "maps_url")


def validate_shops(shops: list, video: dict, case: dict, url: str) -> list[dict]:
    result = []
    for shop in shops:
        if not isinstance(shop, dict):
            continue
        # Fields we already know -- overwrite rather than trust the model's echo.
        shop["creator"] = case["channel"]
        shop["source_url"] = url
        shop["source_title"] = video["title"]
        shop["published_at"] = video["published_at"]
        if not shop.get("place_name_en") and not shop.get("place_name_local"):
            continue  # no usable name -- drop rather than emit a nameless row
        for key in STRING_FIELDS:
            if not isinstance(shop.get(key), str):
                shop[key] = None
        # isinstance check first: an unhashable value (list/dict) would raise on
        # the `in` membership test otherwise.
        if not isinstance(shop.get("sentiment"), str) or shop["sentiment"] not in VALID_SENTIMENTS:
            shop["sentiment"] = None
        if not isinstance(shop.get("confidence"), str) or shop["confidence"] not in VALID_CONFIDENCE:
            shop["confidence"] = None
        result.append(shop)
    return result


def extract_shops_from_video(video: dict, case: dict, client) -> list[dict]:
    url = f"https://www.youtube.com/watch?v={video['video_id']}"
    prompt = EXTRACTION_PROMPT.format(
        channel=case["channel"], title=video["title"], url=url,
        published_at=video["published_at"], description=video["description"],
    )
    messages = [{"role": "user", "content": prompt}]
    response = client.messages.create(model=MODEL, max_tokens=2048, messages=messages)
    text = response.content[0].text
    shops = try_parse_json_array(text)

    if shops is None:
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content":
            "That was not valid JSON. Respond with ONLY a valid JSON array "
            "(or [] if none), no other text."})
        response = client.messages.create(model=MODEL, max_tokens=2048, messages=messages)
        shops = try_parse_json_array(response.content[0].text)
        if shops is None:
            print(f"WARNING: giving up on malformed JSON for video {video['video_id']} after retry")
            return []

    return validate_shops(shops, video, case, url)


def load_or_extract(videos: list[dict], case: dict, client) -> list[dict]:
    cache_path = Path("data/extracted") / f"{case['name']}.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    cached = {}
    if cache_path.exists():
        with open(cache_path) as f:
            for line in f:
                record = json.loads(line)
                cached[record["video_id"]] = record["places"]

    all_places = []
    with open(cache_path, "a") as f:
        for video in videos:
            if video["video_id"] in cached:
                places = cached[video["video_id"]]
            else:
                places = extract_shops_from_video(video, case, client)
                f.write(json.dumps({"video_id": video["video_id"], "places": places}) + "\n")
                f.flush()
            all_places.extend(places)

    return all_places
