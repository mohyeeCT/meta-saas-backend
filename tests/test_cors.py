import asyncio
import json
import unittest

from fastapi.testclient import TestClient

from main import app, _global_exception_handler


class _Request:
    def __init__(self, origin):
        self.headers = {"origin": origin}


class PlatformPreviewCorsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _preflight(self, origin):
        return self.client.options(
            "/api/jobs",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    def test_allows_copypilot_platform_vercel_preview(self):
        origin = (
            "https://copypilot-platform-git-chore-platform-47fef0-"
            "mohyeects-projects.vercel.app"
        )

        response = self._preflight(origin)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)

    def test_blocks_unrelated_vercel_preview(self):
        response = self._preflight(
            "https://unrelated-project-mohyeects-projects.vercel.app"
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_exception_handler_allows_known_origin_without_leaking_exception(self):
        origin = "https://copypilot.app"

        response = asyncio.run(
            _global_exception_handler(_Request(origin), RuntimeError("private database detail"))
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        self.assertEqual(json.loads(response.body), {"detail": "Internal server error"})

    def test_exception_handler_blocks_unknown_origin(self):
        response = asyncio.run(
            _global_exception_handler(
                _Request("https://unrelated-project-mohyeects-projects.vercel.app"),
                RuntimeError("private database detail"),
            )
        )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertNotIn("access-control-allow-credentials", response.headers)
        self.assertEqual(json.loads(response.body), {"detail": "Internal server error"})


if __name__ == "__main__":
    unittest.main()
