import unittest
from unittest.mock import patch
from core import smart


class StreamValidationTests(unittest.TestCase):
    def test_check_url_uses_provider_thread_local_session(self):
        class Response:
            status_code = 200
            def close(self):
                pass
        with patch.object(smart.P, "_http_session") as session:
            session.return_value.head.return_value = Response()
            self.assertTrue(smart.check_url("https://audio.example.test/unique-stream-check"))
            session.return_value.head.assert_called_once()

    def test_check_url_falls_back_to_range_get_when_head_is_blocked(self):
        class Response:
            def __init__(self, status): self.status_code=status
            def close(self): pass
        with patch.object(smart.P, "_http_session") as session:
            session.return_value.head.return_value = Response(405)
            session.return_value.get.return_value = Response(206)
            self.assertTrue(smart.check_url("https://audio.example.test/unique-range-check"))
            session.return_value.get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
