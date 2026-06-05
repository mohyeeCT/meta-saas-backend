import unittest

from fastapi.testclient import TestClient

from main import app


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


if __name__ == "__main__":
    unittest.main()
