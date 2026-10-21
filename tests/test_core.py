"""Fast smoke tests that never download the multilingual model."""
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update.py"
spec = importlib.util.spec_from_file_location("radar_update", SCRIPT)
radar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radar)

class FakeModel:
    def __init__(self, *args, **kwargs):
        pass

    def encode(self, texts, **kwargs):
        vectors = []
        for title in texts:
            if any(word in title for word in ("NVIDIA", "英伟达", "エヌビディア")):
                vectors.append([1.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 1.0, 0.0])
        return np.asarray(vectors, dtype=np.float32)


class CoreTests(unittest.TestCase):
    def test_article_url_cleanup(self):
        source = {"name": "Test", "type": "publisher", "publisher": "Test", "lang": "en", "category": "tech"}
        item = radar.article(source, "AI news", "https://example.com/a?utm_source=foo&x=1", "2026-10-13T00:00:00Z")
        self.assertEqual(item["url"], "https://example.com/a?x=1")
        self.assertIsNone(radar.article(source, "No", "javascript:alert(1)", "2026-10-13T00:00:00Z"))

    def test_event_clustering_and_independent_sources(self):
        when = radar.iso(datetime.now(timezone.utc))
        src = [
            {"name": "English Press", "type": "publisher", "publisher": "English Press", "lang": "en", "category": "tech"},
            {"name": "中文媒体", "type": "publisher", "publisher": "中文媒体", "lang": "zh", "category": "tech"},
            {"name": "Hacker News", "type": "aggregator", "publisher": "Hacker News", "lang": "en", "category": "tech"},
            {"name": "Japanese Press", "type": "publisher", "publisher": "Japanese Press", "lang": "ja", "category": "tech"},
        ]
        rows = [
            radar.article(src[0], "NVIDIA announces AI processors", "https://example.com/nvidia", when),
            radar.article(src[1], "英伟达发布AI芯片", "https://example.cn/nvidia", when),
            radar.article(src[2], "NVIDIA announces new processors", "https://news.ycombinator.com/item?id=1", when, rank=1),
            radar.article(src[3], "全く異なる経済ニュース", "https://example.jp/unrelated", when),
        ]
        with patch.object(radar, "SentenceTransformer", FakeModel):
            events = radar.cluster(rows)
        self.assertEqual(len(events), 2)
        big = next(e for e in events if e["article_count"] == 3)
        self.assertEqual(big["media_count"], 2)
        self.assertIn("zh", big["languages"])
        self.assertGreater(big["score"], 0)


if __name__ == "__main__":
    unittest.main()
