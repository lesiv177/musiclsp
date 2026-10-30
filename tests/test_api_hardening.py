import json
import unittest
from types import SimpleNamespace

from services.bot import api


class APIHardeningTests(unittest.IsolatedAsyncioTestCase):
    def test_bounded_int_accepts_and_clamps_values(self):
        self.assertEqual(api.bounded_int("12", 30, 1, 20), 12)
        self.assertEqual(api.bounded_int("999", 30, 1, 20), 20)
        self.assertEqual(api.bounded_int("-5", 30, 1, 20), 1)

    def test_bounded_int_rejects_invalid_values(self):
        self.assertIsNone(api.bounded_int("not-a-number", 30, 1, 20))
        self.assertIsNone(api.bounded_int("2.5", 30, 1, 20))

    async def test_error_middleware_hides_internal_exception_details(self):
        request = SimpleNamespace(path="/private/test")

        async def broken_handler(_request):
            raise RuntimeError("secret-database-password")

        response = await api.error_middleware(request, broken_handler)
        self.assertEqual(response.status, 500)
        payload = json.loads(response.text)
        self.assertEqual(payload["error"], "Внутрішня помилка сервера. Спробуйте ще раз.")
        self.assertNotIn("secret-database-password", response.text)


if __name__ == "__main__":
    unittest.main()
