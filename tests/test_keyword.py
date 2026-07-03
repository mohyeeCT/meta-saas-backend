import unittest

from utils.keyword import select_keyword


class KeywordSelectionTests(unittest.TestCase):

    # ── CTR cap ───────────────────────────────────────────────────────────────

    def test_ctr_cap_prevents_outlier_from_winning(self):
        """A keyword with 80% CTR must not beat a stronger keyword due to uncapped CTR."""
        queries = [
            {"query": "buy widgets online", "impressions": 500, "clicks": 50,  "ctr": 0.10, "position": 5.0},
            {"query": "widget store",       "impressions": 100, "clicks": 80,  "ctr": 0.80, "position": 5.0},
        ]
        dfs_data = {
            "buy widgets online": {"volume": 1000, "difficulty": 20},
            "widget store":       {"volume": 200,  "difficulty": 20},
        }

        result = select_keyword(queries, dfs_data, branded_terms=[])

        self.assertEqual(result["selected_keyword"], "buy widgets online",
                         "High-volume keyword should win; outlier CTR must not override it")

    def test_normal_ctr_unaffected_by_cap(self):
        """CTR values at or below 0.15 must pass through the cap unchanged."""
        queries = [
            {"query": "red widgets", "impressions": 200, "clicks": 20, "ctr": 0.10, "position": 3.0},
        ]
        dfs_data = {"red widgets": {"volume": 300, "difficulty": 30}}

        result = select_keyword(queries, dfs_data, branded_terms=[])

        self.assertEqual(result["selected_keyword"], "red widgets")
        self.assertFalse(result["fallback_triggered"])

    # ── Zero-volume handling (standard mode) ─────────────────────────────────

    def test_standard_mode_penalises_zero_volume_over_volume_bearing(self):
        """In standard mode a zero-volume keyword must lose to a volume-bearing one."""
        queries = [
            {"query": "popular widget",  "impressions": 300, "clicks": 30, "ctr": 0.10, "position": 5.0},
            {"query": "obscure widget",  "impressions": 300, "clicks": 30, "ctr": 0.10, "position": 5.0},
        ]
        dfs_data = {
            "popular widget": {"volume": 500, "difficulty": 25},
            "obscure widget": {"volume": 0,   "difficulty": 25},
        }

        result = select_keyword(queries, dfs_data, branded_terms=[])

        self.assertEqual(result["selected_keyword"], "popular widget",
                         "Volume-bearing keyword must outscore zero-volume keyword")

    def test_standard_mode_can_select_zero_volume_as_last_resort(self):
        """In standard mode a zero-volume keyword with impressions can still be selected
        when it is the only candidate (all others are branded or at position 1)."""
        queries = [
            {"query": "cbd tinctures", "impressions": 400, "clicks": 40, "ctr": 0.10, "position": 4.0},
        ]
        dfs_data = {}  # no DFS data → volume defaults to 0

        result = select_keyword(queries, dfs_data, branded_terms=[])

        self.assertEqual(result["selected_keyword"], "cbd tinctures",
                         "Zero-volume keyword must still be selectable when it is the only candidate")
        self.assertFalse(result["fallback_triggered"])

    def test_standard_zero_volume_scoring_uses_position_and_relevance(self):
        queries = [
            {
                "query": "emergency plumbing services",
                "impressions": 40,
                "clicks": 1,
                "ctr": 0.02,
                "position": 2.0,
            },
            {
                "query": "random celebrity quote",
                "impressions": 500,
                "clicks": 10,
                "ctr": 0.02,
                "position": 45.0,
            },
        ]
        dfs_data = {
            "emergency plumbing services": {"volume": 0, "difficulty": 50},
            "random celebrity quote": {"volume": 0, "difficulty": 50},
        }

        result = select_keyword(
            queries,
            dfs_data,
            branded_terms=[],
            h1="Emergency Plumbing Services",
            restricted_industry=False,
        )

        self.assertEqual(result["selected_keyword"], "emergency plumbing services")
        self.assertGreater(
            result["selected_keyword_data"]["score"],
            result["runner_up"]["score"],
        )

    # ── min_volume filter ─────────────────────────────────────────────────────

    def test_standard_mode_applies_min_volume_filter(self):
        """In standard mode keywords below min_volume must be excluded."""
        queries = [
            {"query": "niche widget", "impressions": 200, "clicks": 20, "ctr": 0.10, "position": 5.0},
        ]
        dfs_data = {"niche widget": {"volume": 5, "difficulty": 30}}

        result = select_keyword(queries, dfs_data, branded_terms=[], min_volume=50)

        self.assertIsNone(result["selected_keyword"],
                          "Keyword below min_volume must be excluded in standard mode")
        self.assertTrue(result["fallback_triggered"])

    # ── restricted_industry mode ──────────────────────────────────────────────

    def test_restricted_industry_scores_zero_volume_without_penalty(self):
        """In restricted mode, zero-volume keyword must win over a penalised zero-volume
        competitor — both have no DFS data, restricted scores on engagement signals."""
        queries = [
            {"query": "cbd oil tincture", "impressions": 500, "clicks": 60, "ctr": 0.12, "position": 4.0},
        ]
        dfs_data = {}  # volume suppressed

        result = select_keyword(
            queries, dfs_data, branded_terms=[], restricted_industry=True
        )

        self.assertEqual(result["selected_keyword"], "cbd oil tincture",
                         "Restricted mode must select keyword even with no DFS volume")
        self.assertFalse(result["fallback_triggered"])

    def test_restricted_industry_ignores_min_volume_filter(self):
        """In restricted mode, keywords below min_volume must still compete."""
        queries = [
            {"query": "artisan hemp extract", "impressions": 200, "clicks": 20, "ctr": 0.10, "position": 5.0},
        ]
        dfs_data = {"artisan hemp extract": {"volume": 5, "difficulty": 30}}

        result = select_keyword(
            queries, dfs_data, branded_terms=[], min_volume=100, restricted_industry=True
        )

        self.assertEqual(result["selected_keyword"], "artisan hemp extract",
                         "Restricted mode must not apply min_volume filter")

    # ── Branded filtering ─────────────────────────────────────────────────────

    def test_branded_keywords_are_excluded(self):
        """Queries containing a branded term must be filtered out entirely."""
        queries = [
            {"query": "acme widgets",    "impressions": 1000, "clicks": 200, "ctr": 0.20, "position": 2.0},
            {"query": "buy widgets now", "impressions": 300,  "clicks": 30,  "ctr": 0.10, "position": 5.0},
        ]
        dfs_data = {
            "acme widgets":    {"volume": 2000, "difficulty": 10},
            "buy widgets now": {"volume": 500,  "difficulty": 30},
        }

        result = select_keyword(queries, dfs_data, branded_terms=["acme"])

        self.assertEqual(result["selected_keyword"], "buy widgets now",
                         "Branded keyword must be excluded regardless of volume or impressions")

    # ── Position 1.0 exclusion ────────────────────────────────────────────────

    def test_exact_position_one_is_excluded(self):
        """Keywords ranked at exactly position 1.0 must be skipped."""
        queries = [
            {"query": "top ranking widget", "impressions": 2000, "clicks": 400, "ctr": 0.20, "position": 1.0},
            {"query": "second widget",      "impressions": 300,  "clicks": 30,  "ctr": 0.10, "position": 3.0},
        ]
        dfs_data = {
            "top ranking widget": {"volume": 5000, "difficulty": 10},
            "second widget":      {"volume": 400,  "difficulty": 30},
        }

        result = select_keyword(queries, dfs_data, branded_terms=[])

        self.assertEqual(result["selected_keyword"], "second widget",
                         "Position 1.0 keyword must be excluded from selection")


if __name__ == "__main__":
    unittest.main()
