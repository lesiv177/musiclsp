import unittest
from pathlib import Path


class MiniAppCorsContractTests(unittest.TestCase):
    def test_fetch_sends_only_cors_allowed_headers(self):
        html = Path("webapp/index.html").read_text(encoding="utf-8")
        start = html.index("async function req(")
        end = html.index("// Не запускаємо однаковий GET", start)
        block = html[start:end]
        self.assertIn('"Content-Type":"application/json"', block)
        self.assertIn('"X-Telegram-Init-Data": INIT', block)
        self.assertNotIn("ui_no_genres", block)
        self.assertNotIn("ui_genres_error", block)


if __name__ == "__main__":
    unittest.main()
