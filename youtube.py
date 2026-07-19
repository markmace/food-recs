import json
from pathlib import Path

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"


def _get(path: str, params: dict) -> dict:
    # Raise our own error using only `path` (never response.url, which embeds the
    # ?key=... query param) so a failed request can't leak the API key into a
    # traceback someone might paste into a public issue/log.
    response = requests.get(f"{API_BASE}/{path}", params=params)
    if not response.ok:
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError):
            message = response.text[:300]
        raise RuntimeError(f"YouTube API error {response.status_code} on {path}: {message}")
    return response.json()


def resolve_channel_id(channel_name: str, api_key: str) -> str:
    data = _get("search", {"part": "snippet", "type": "channel", "q": channel_name, "maxResults": 1, "key": api_key})
    item = data["items"][0]
    channel_id = item["snippet"]["channelId"]
    print(f"Resolved '{channel_name}' -> {item['snippet']['title']} ({channel_id})")
    return channel_id


def get_uploads_playlist_id(channel_id: str, api_key: str) -> str:
    data = _get("channels", {"part": "contentDetails", "id": channel_id, "key": api_key})
    return data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_all_videos(playlist_id: str, api_key: str) -> list[dict]:
    videos = []
    page_token = None
    while True:
        params = {"part": "snippet", "playlistId": playlist_id, "maxResults": 50, "key": api_key}
        if page_token:
            params["pageToken"] = page_token
        data = _get("playlistItems", params)
        for item in data["items"]:
            snippet = item["snippet"]
            videos.append({
                "video_id": snippet["resourceId"]["videoId"],
                "title": snippet["title"],
                "description": snippet["description"],
                "published_at": snippet["publishedAt"],
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return videos


def load_or_fetch_videos(case: dict, api_key: str) -> list[dict]:
    # All-or-nothing cache: once this file exists, reruns never see videos the
    # channel posts later (this is a one-time backfill, not a sync). Delete the
    # file to force a refetch.
    cache_path = Path("data/raw") / f"{case['name']}.jsonl"
    if cache_path.exists():
        with open(cache_path) as f:
            return [json.loads(line) for line in f]

    channel_id = resolve_channel_id(case["channel"], api_key)
    playlist_id = get_uploads_playlist_id(channel_id, api_key)
    videos = fetch_all_videos(playlist_id, api_key)

    # Write to a temp file and rename into place (atomic on POSIX), so a kill or
    # crash mid-write can never leave a truncated file that a later run would
    # mistake for a complete cache via cache_path.exists().
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w") as f:
        for video in videos:
            f.write(json.dumps(video) + "\n")
    tmp_path.replace(cache_path)

    return videos
