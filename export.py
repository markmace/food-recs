import csv
import re
from collections import defaultdict

from rapidfuzz import fuzz

NAME_MATCH_THRESHOLD = 90


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


def _same_item(a: dict, b: dict, dedupe_fields: list[str]) -> bool:
    matched_any = False
    for field in dedupe_fields:
        va, vb = normalize_name(a.get(field)), normalize_name(b.get(field))
        if not va and not vb:
            continue
        if not va or not vb:
            return False
        if fuzz.ratio(va, vb) < NAME_MATCH_THRESHOLD:
            return False
        matched_any = True
    return matched_any


def _merge_cluster(records: list[dict], field_names: list[str]) -> dict:
    records = sorted(records, key=lambda r: r["published_at"])

    def first_non_null(field: str):
        for r in records:
            if r.get(field):
                return r[field]
        return None

    seen_urls, seen_titles = [], []
    for r in records:
        if r["source_url"] not in seen_urls:
            seen_urls.append(r["source_url"])
            seen_titles.append(r["source_title"])

    row = {name: first_non_null(name) for name in field_names}
    row["channel"] = records[0]["channel"]
    row["source_urls"] = "|".join(seen_urls)
    row["source_titles"] = "|".join(seen_titles)
    row["first_seen"] = records[0]["published_at"]
    return row


def dedupe_items(items: list[dict], dedupe_fields: list[str], field_names: list[str]) -> list[dict]:
    uf = _UnionFind(len(items))
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _same_item(items[i], items[j], dedupe_fields):
                uf.union(i, j)

    clusters: dict[int, list[dict]] = defaultdict(list)
    for idx, item in enumerate(items):
        clusters[uf.find(idx)].append(item)

    return [_merge_cluster(cluster, field_names) for cluster in clusters.values()]


def _as_row(item: dict, field_names: list[str]) -> dict:
    row = {
        "channel": item["channel"],
        "video": item["source_title"],
        "video_url": item["source_url"],
        "published_at": item["published_at"],
    }
    for name in field_names:
        row[name] = item.get(name)
    return row


_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def write_csv(rows: list[dict], columns: list[str], output_path: str) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _csv_safe(row.get(col)) for col in columns})


def export_case(items: list[dict], case: dict, output_path: str) -> None:
    field_names = [f["name"] for f in case["fields"]]
    dedupe_fields = case.get("dedupe_fields")

    if dedupe_fields:
        rows = dedupe_items(items, dedupe_fields, field_names)
        columns = ["channel", *field_names, "source_urls", "source_titles", "first_seen"]
        rows.sort(key=lambda r: (r.get(dedupe_fields[0]) or "", r["first_seen"]))
    else:
        rows = [_as_row(item, field_names) for item in items]
        columns = ["channel", "video", "video_url", "published_at", *field_names]
        rows.sort(key=lambda r: (r["published_at"], r["video"]))

    write_csv(rows, columns, output_path)

    print()
    print("== Summary ==")
    print(f"Rows written: {len(rows)}")
    if dedupe_fields:
        print(f"(deduped from {len(items)} extracted items on: {', '.join(dedupe_fields)})")
    print(f"CSV written to: {output_path}")
