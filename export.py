import csv
import re
from collections import defaultdict

from rapidfuzz import fuzz

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, None: 0}
NAME_MATCH_THRESHOLD = 90

CSV_COLUMNS = [
    "channel", "subject", "category", "details", "sentiment", "link",
    "source_urls", "source_titles", "first_seen", "confidence",
]


def normalize_name(name) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = re.sub(r"[.''\-]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


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

    def most_common(field: str):
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
        "channel": records[0]["channel"],
        "subject": most_common("subject"),
        "category": best_by_confidence("category"),
        "details": best_by_confidence("details"),
        "sentiment": best_by_confidence("sentiment"),
        "link": first_non_null("link"),
        "source_urls": "|".join(seen_urls),
        "source_titles": "|".join(seen_titles),
        "first_seen": records[0]["published_at"],
        "confidence": max((r.get("confidence") for r in records), key=lambda c: CONFIDENCE_RANK[c]),
    }


def _same_item(a: dict, b: dict) -> bool:
    name_a = normalize_name(a.get("subject"))
    name_b = normalize_name(b.get("subject"))
    if not name_a or not name_b:
        return False

    link_a, link_b = a.get("link"), b.get("link")
    if link_a and link_b:
        return link_a == link_b

    return fuzz.ratio(name_a, name_b) >= NAME_MATCH_THRESHOLD


def dedupe_items(items: list[dict]) -> list[dict]:
    uf = _UnionFind(len(items))
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _same_item(items[i], items[j]):
                uf.union(i, j)

    clusters: dict[int, list[dict]] = defaultdict(list)
    for idx, item in enumerate(items):
        clusters[uf.find(idx)].append(item)

    merged = [_merge_cluster(cluster) for cluster in clusters.values()]
    merged.sort(key=lambda r: (r["subject"] or ""))
    return merged


_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def write_csv(items: list[dict], output_path: str) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow({col: _csv_safe(item.get(col)) for col in CSV_COLUMNS})


def export_case(items: list[dict], case: dict) -> None:
    deduped = dedupe_items(items)
    write_csv(deduped, case["output_csv"])
    low_confidence = sum(1 for r in deduped if r["confidence"] == "low")

    print()
    print("== Summary ==")
    print(f"Unique items: {len(deduped)}")
    print(f"Low-confidence items: {low_confidence}")
    print(f"CSV written to: {case['output_csv']}")
