import csv
import re
from collections import defaultdict

from rapidfuzz import fuzz

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, None: 0}

# fuzz.ratio, not WRatio -- WRatio's partial-ratio component scores nearly any
# "one name is a prefix of the other" pair at ~90 regardless of how much text is
# appended (e.g. "Ramen Jiro" vs "Ramen Jiro Mita Honten", different branches,
# scores 90), which made the old threshold merge unrelated chain branches. Plain
# ratio separates these cleanly; see the merge policy in _same_place below.
NAME_MATCH_THRESHOLD = 90

CSV_COLUMNS = [
    "creator", "place_name_en", "place_name_local", "neighborhood", "city",
    "category", "sentiment", "price_signal", "maps_url",
    "source_urls", "source_titles", "first_seen", "confidence",
]


def normalize_name(name) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = re.sub(r"[.''\-]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _comparison_name(name: str | None) -> str:
    # Only for fuzzy-match scoring: strip the generic word "ramen" so it doesn't
    # drag down the score for genuine duplicates like "Ichiran" / "Ichiran Ramen"
    # -- real branch differentiators are place/area words, not this filler.
    normalized = normalize_name(name)
    return re.sub(r"\bramen\b", "", normalized).strip()


def normalize_city(city) -> str:
    if not isinstance(city, str):
        return ""
    return city.strip().lower()


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[ri] = rj


def _merge_cluster(records: list[dict]) -> dict:
    records = sorted(records, key=lambda r: r["published_at"])

    def best_by_confidence(field: str):
        ranked = sorted(records, key=lambda r: (CONFIDENCE_RANK[r.get("confidence")], r["published_at"]))
        for r in reversed(ranked):
            if r.get(field):
                return r[field]
        return None

    def most_common_name(field: str):
        counts: dict[str, int] = defaultdict(int)
        for r in records:
            if r.get(field):
                counts[r[field]] += 1
        if not counts:
            return None
        max_count = max(counts.values())
        candidates = [v for v, c in counts.items() if c == max_count]
        return max(candidates, key=len)

    def first_non_null(field: str):
        for r in records:
            if r.get(field):
                return r[field]
        return None

    seen_urls = []
    seen_titles = []
    for r in records:
        if r["source_url"] not in seen_urls:
            seen_urls.append(r["source_url"])
            seen_titles.append(r["source_title"])

    return {
        "creator": records[0]["creator"],
        "place_name_en": most_common_name("place_name_en"),
        "place_name_local": most_common_name("place_name_local"),
        "neighborhood": first_non_null("neighborhood"),
        "city": first_non_null("city"),
        "category": best_by_confidence("category"),
        "sentiment": best_by_confidence("sentiment"),
        "price_signal": best_by_confidence("price_signal"),
        "maps_url": first_non_null("maps_url"),
        "source_urls": "|".join(seen_urls),
        "source_titles": "|".join(seen_titles),
        "first_seen": records[0]["published_at"],
        "confidence": max((r.get("confidence") for r in records), key=lambda c: CONFIDENCE_RANK[c]),
    }


def _same_place(a: dict, b: dict) -> bool:
    name_a = _comparison_name(a.get("place_name_en") or a.get("place_name_local"))
    name_b = _comparison_name(b.get("place_name_en") or b.get("place_name_local"))
    if not name_a or not name_b:
        return False

    # maps_url is the strongest signal available -- let it decide outright when
    # both sides have one, regardless of city/neighborhood text (which may be
    # null on one side while a maps_url still confirms the same place).
    maps_url_a, maps_url_b = a.get("maps_url"), b.get("maps_url")
    if maps_url_a and maps_url_b:
        return maps_url_a == maps_url_b

    # Without a maps_url match, require a confirmed shared city AND neighborhood
    # before trusting name similarity -- many ramen shops share generic naming,
    # and a missing city/neighborhood on either side could just as easily mean a
    # different branch as the same one. Undermerge over overmerge.
    city_a, city_b = normalize_city(a.get("city")), normalize_city(b.get("city"))
    if not city_a or not city_b or city_a != city_b:
        return False
    neighborhood_a, neighborhood_b = normalize_name(a.get("neighborhood")), normalize_name(b.get("neighborhood"))
    if not neighborhood_a or not neighborhood_b or neighborhood_a != neighborhood_b:
        return False

    return fuzz.ratio(name_a, name_b) >= NAME_MATCH_THRESHOLD


def dedupe_places(places: list[dict]) -> list[dict]:
    # A single pass over all places (not pre-bucketed by city) so the maps_url
    # shortcut in _same_place can still catch a match even when one mention has
    # a null/differently-cased city and the other doesn't.
    uf = _UnionFind(len(places))
    for i in range(len(places)):
        for j in range(i + 1, len(places)):
            if _same_place(places[i], places[j]):
                uf.union(i, j)

    clusters: dict[int, list[dict]] = defaultdict(list)
    for idx, place in enumerate(places):
        clusters[uf.find(idx)].append(place)

    merged = [_merge_cluster(cluster) for cluster in clusters.values()]
    merged.sort(key=lambda p: (p["neighborhood"] is None, p["neighborhood"] or "", p["place_name_en"] or p["place_name_local"] or ""))
    return merged


# Excel/Sheets treats a cell starting with any of these as a formula. Values
# here ultimately trace back to arbitrary YouTube video text via Claude, so
# guard against CSV formula injection when the CSV is opened in a spreadsheet.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def write_csv(places: list[dict], output_path: str) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for place in places:
            writer.writerow({col: _csv_safe(place.get(col)) for col in CSV_COLUMNS})


def export_case(places: list[dict], case: dict) -> None:
    deduped = dedupe_places(places)
    write_csv(deduped, case["output_csv"])
    low_confidence = sum(1 for p in deduped if p["confidence"] == "low")

    print()
    print("== Summary ==")
    print(f"Unique shops: {len(deduped)}")
    print(f"Low-confidence shops: {low_confidence}")
    print(f"CSV written to: {case['output_csv']}")
