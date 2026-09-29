import json
import os
import tempfile
import unittest
from unittest.mock import patch

import scout_bounties as scout


class BountyScoutStateTests(unittest.TestCase):
    def test_save_seen_bounties_is_sorted_for_stable_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "seen.json")
            with patch.object(scout, "STATE_FILE", state_file):
                scout.save_seen_bounties(["https://github.com/z/repo/issues/2", "https://github.com/a/repo/issues/1"])

            with open(state_file, "r", encoding="utf-8") as fh:
                saved = json.load(fh)

        self.assertEqual(
            saved,
            ["https://github.com/a/repo/issues/1", "https://github.com/z/repo/issues/2"],
        )

    def test_failed_delivery_does_not_mark_candidate_seen(self):
        candidate_url = "https://github.com/example/project/issues/123"
        candidate = {
            "title": "Documentation bounty $10",
            "body": "Paid README markdown quality review",
            "html_url": candidate_url,
            "comments": 0,
            "updated_at": "2026-09-29T06:00:00Z",
            "assignees": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "seen.json")
            with open(state_file, "w", encoding="utf-8") as fh:
                json.dump([], fh)

            env = {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "uknwplayer/BountyScout",
            }
            with (
                patch.object(scout, "STATE_FILE", state_file),
                patch.object(scout, "search_github", return_value={"items": [candidate]}),
                patch.object(scout, "create_github_issue", return_value=False),
                patch.dict(os.environ, env, clear=True),
            ):
                scout.main()

            with open(state_file, "r", encoding="utf-8") as fh:
                saved = json.load(fh)

        self.assertNotIn(candidate_url, saved)


if __name__ == "__main__":
    unittest.main()
