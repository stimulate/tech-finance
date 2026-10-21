#!/usr/bin/env python3
"""Collect RSS + HN, cluster multilingual news on CPU, build static JSON."""
from __future__ import annotations

import calendar
import hashlib
import html
import json
import logging
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import numpy as np
import requests
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parent.parent
FEEDS = ROOT / "config" / "feeds.json"
ARCHIVE = ROOT / "data" / "articles.json"
OUTPUT = ROOT / "site" / "data" / "events.json"
MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.91"))
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "700"))
NOW = datetime.now(timezone.utc)
USER_AGENT = "TechFinanceRadarPersonal/0.1 (RSS reader; public feed metadata only)"
TIMEOUT = 16
logger = logging.getLogger("radar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def clean(s: str, max_len=280) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    return re.sub(r"\s+", " ", s).strip()[:max_len]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(entry) -> str:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                return iso(datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc))
            except (TypeError, ValueError, OverflowError):
                pass
    return iso(NOW)


def utc(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return NOW


def canonical(url: str) -> str:
    try:
        p = urlsplit(url.strip())
        if p.scheme not in ("http", "https") or not p.netloc:
            return ""
        params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                  if not (k.lower().startswith("utm_") or k.lower() in ("fbclid", "gclid", "ref"))]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", urlencode(params), ""))
    except ValueError:
        return ""


def article(source, title, url, published, summary="", rank=None, external_id=None):
    title = clean(title, 240)
    url = canonical(url)
    if not title or not url:
        return None
    key = f"{source['name']}|{external_id or url}"
    return {
        "id": hashlib.sha256(key.encode()).hexdigest()[:18],
        "title": title,
        "url": url,
        "summary": clean(summary, 240),
        "published": published,
        "source": source["name"],
        "source_type": source["type"],
        "publisher": source["publisher"],
        "language": source["lang"],
        "category": source["category"],
        "rank": rank,
    }


def collect_feed(source):
    response = requests.get(source["url"], timeout=TIMEOUT,
                            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, */*"})
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if not feed.entries:
        raise ValueError("No feed entries found")
    found = []
    for i, item in enumerate(feed.entries[:45], 1):
        result = article(source, item.get("title", ""), item.get("link", ""),
                         parse_time(item), item.get("summary", ""),
                         rank=i if source.get("ranked") else None)
        if result:
            found.append(result)
    return found


def collect_hn(limit=25):
    base = "https://hacker-news.firebaseio.com/v0"
    session = requests.Session()
    resp = session.get(f"{base}/topstories.json", timeout=TIMEOUT)
    resp.raise_for_status()
    ids = resp.json()[:limit]
    src = {"name": "Hacker News · Top", "lang":"en", "category":"tech", "type":"aggregator", "publisher":"Hacker News"}
    def one(args):
        rank, story_id = args
        r = requests.get(f"{base}/item/{story_id}.json", timeout=TIMEOUT)
        r.raise_for_status()
        item = r.json()
        if not item or item.get("type") != "story" or item.get("deleted") or item.get("dead"):
            return None
        url = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        return article(src, item.get("title", ""), url,
                       iso(datetime.fromtimestamp(item.get("time", int(NOW.timestamp())), tz=timezone.utc)),
                       summary=f"HN points: {item.get('score', 0)} | comments: {item.get('descendants', 0)}",
                       rank=rank, external_id=story_id)
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(one, (i, story_id)) for i, story_id in enumerate(ids, 1)]
        for task in as_completed(futures):
            try:
                found = task.result()
                if found:
                    results.append(found)
            except Exception as exc:
                logger.warning("HN item skipped: %s", exc)
    return results


def meaningful_numbers(title: str) -> set[str]:
    # Conflicting product versions, counts or amounts usually indicate different events.
    return {v for v in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])", title)
            if v not in {"2025", "2026", "2027"}}


def compatible(a, b) -> bool:
    na, nb = meaningful_numbers(a["title"]), meaningful_numbers(b["title"])
    if na and nb and na.isdisjoint(nb):
        return False
    # Stories more than 48 hours apart are conservatively kept separate.
    return abs((utc(a["published"]) - utc(b["published"])).total_seconds()) <= 48 * 3600


def relevant(title: str) -> float:
    keywords = ("aws", "cloud", "azure", "entra", "security", "cyber", "ransomware",
                "人工智能", "ai", "半導体", "半导体", "finance", "inflation", "market",
                "金融", "投资", "投資", "円安", "金利", "株価", "devops", "kubernetes")
    t = title.lower()
    return 1.0 if any(word in t for word in keywords) else 0.35


def cluster(articles: list[dict]):
    if not articles:
        return []
    model = SentenceTransformer(MODEL, device="cpu")
    titles = ["query: " + a["title"] for a in articles]
    emb = model.encode(titles, batch_size=32, normalize_embeddings=True,
                       convert_to_numpy=True, show_progress_bar=False)
    emb = np.asarray(emb, dtype=np.float32)
    events: list[list[int]] = []
    for i, a in enumerate(articles):
        matched = None
        highest = THRESHOLD
        for event_id, members in enumerate(events):
            # Compare against each member; avoid chaining loosely related events.
            comparable = [j for j in members if compatible(a, articles[j])]
            if not comparable:
                continue
            sims = emb[comparable] @ emb[i]
            best = float(np.max(sims))
            # Must agree with representative too, or use very strong similarity.
            representative = float(emb[members[0]] @ emb[i])
            if best >= highest and (representative >= THRESHOLD - 0.035 or best >= 0.965):
                matched, highest = event_id, best
        if matched is None:
            events.append([i])
        else:
            events[matched].append(i)

    output = []
    for members in events:
        group = [articles[i] for i in members]
        # Prefer existing Chinese headline when available; otherwise latest headline.
        headline = next((a for a in group if a["language"] == "zh" and a["source_type"] == "publisher"), group[0])
        publishers = sorted({a["publisher"] for a in group if a["source_type"] in ("publisher", "community")})
        independent = len(publishers)
        rank_values = [1.0 / math.log2(a["rank"] + 1) for a in group if isinstance(a.get("rank"), int) and a["rank"] >= 1]
        rank_score = max(rank_values, default=0.0)
        coverage_score = math.log1p(min(independent, 7)) / math.log1p(7)
        newest = max(utc(a["published"]) for a in group)
        age_hours = max(0, (NOW - newest).total_seconds() / 3600)
        freshness_score = 2 ** (-age_hours / 18)
        personal_score = max(relevant(a["title"]) for a in group)
        score = round(35 * rank_score + 30 * coverage_score + 25 * freshness_score + 10 * personal_score, 1)
        output.append({
            "id": hashlib.sha256("|".join(sorted(a["id"] for a in group)).encode()).hexdigest()[:15],
            "title": headline["title"],
            "category": "finance" if sum(a["category"] == "finance" for a in group) > len(group) / 2 else "tech",
            "score": score,
            "published": iso(newest),
            "media_count": independent,
            "article_count": len(group),
            "languages": sorted({a["language"] for a in group}),
            "publishers": publishers,
            "articles": sorted(group, key=lambda a: a["published"], reverse=True),
            "components": {"rank": round(rank_score, 3), "coverage": round(coverage_score, 3),
                           "freshness": round(freshness_score, 3), "relevance": round(personal_score, 3)},
        })
    return sorted(output, key=lambda e: e["score"], reverse=True)


def main():
    sources = read_json(FEEDS, [])
    archive = read_json(ARCHIVE, [])
    new = []
    failed = []
    success = 0
    # One source failure must not prevent publishing the rest.
    for src in sources:
        try:
            got = collect_feed(src)
            new.extend(got)
            success += 1
            logger.info("%s: %d articles", src["name"], len(got))
        except Exception as exc:
            failed.append(src["name"])
            logger.warning("Source failed %s: %s", src["name"], exc)
    try:
        hn = collect_hn()
        success += 1
        new.extend(hn)
        logger.info("Hacker News: %d articles", len(hn))
    except Exception as exc:
        failed.append("Hacker News")
        logger.warning("HN failed: %s", exc)

    # Keep older articles when feeds temporarily fail; refresh existing rank data.
    merged = {a["id"]: a for a in archive if isinstance(a, dict) and a.get("id")}
    for a in new:
        merged[a["id"]] = a
    cutoff = NOW - timedelta(days=7)
    items = [a for a in merged.values() if utc(a.get("published", "")) >= cutoff]
    items.sort(key=lambda a: a["published"], reverse=True)
    items = items[:MAX_ARTICLES]
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    visible = [a for a in items if utc(a["published"]) >= NOW - timedelta(hours=72)]
    if not visible and not items:
        raise RuntimeError("No articles available; inspect feed/API connectivity")
    events = cluster(visible)
    payload = {"updated_at": iso(NOW), "article_count": len(visible),
               "feeds_ok": success, "feeds_failed": failed, "similarity_threshold": THRESHOLD,
               "events": events}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    logger.info("Done: %d articles, %d events, %d successful sources, %d failures", len(visible), len(events), success, len(failed))


if __name__ == "__main__":
    main()
