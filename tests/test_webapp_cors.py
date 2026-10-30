import re
import unittest
from pathlib import Path


class MiniAppCorsContractTests(unittest.TestCase):
    def test_fetch_sends_only_cors_allowed_headers(self):
        html = Path("webapp/index.html").read_text(encoding="utf-8")
        match = re.search(r"async function req\\(path, opts=\\{\\}\\)\\{(.*?)\\n\\}", html, re.S)
        self.assertIsNotNone(match, "Mini App request helper is missing")
        block = match.group(1)
        self.assertIn('"Content-Type":"application/json"', block)
        self.assertIn('"X-Telegram-Init-Data": INIT', block)
        self.assertNotRegex(block, r"ui_[a-z_]+\\s*:")
        self.assertNotIn("Access-Control-Allow-Headers", block)


if __name__ == "__main__":
    unittest.main()
