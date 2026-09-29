import json
import os
import tempfile
import unittest
from unittest.mock import patch

import scout_bounties as scout


class StandaloneClaimDetectionTests(unittest.TestCase):
    def test_standalone_claim_marks_candidate_claimed(self):
        status = scout.classify_comment_status([
            {"body": " /claim "}
        ])
        self.assertEqual(status, "CLAIMED")

    def test_standalone_claim_is_suppressed_and_marked_seen(self):
        candidate_url = "https://github.com/codeswithroh/tastemaker/issues/85"
        candidate = {
            "title": "check_contrast.py: add --json output and a CI action",
            "body": "Paid Python CI bounty in USDC for testing and GitHub work.",
            "html_url": candidate_url,
            "comments_url": "https://api.github.com/repos/codeswithroh/tastemaker/issues/85/comments",
            "comments": 1,
            "updated_at": "2026-09-29T08:19:19Z",
            "assignees": [],
            "labels": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "seen.json")
            with open(state_file, "w", encoding="utf-8") as fh:
                json.dump([], fh)

            create_issue = unittest.mock.Mock(return_value=True)
            env = {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "uknwplayer/BountyScout",
            }
            with (
                patch.object(scout, "STATE_FILE", state_file),
                patch.object(scout, "SEARCH_QUERIES", ["test-query"]),
                patch.object(scout, "search_github", return_value={"items": [candidate]}),
                patch.object(scout, "fetch_issue_comments", return_value=[{"body": " /claim "}]),
                patch.object(scout, "create_github_issue", create_issue),
                patch.dict(os.environ, env, clear=True),
            ):
                scout.main()

            with open(state_file, "r", encoding="utf-8") as fh:
                saved = json.load(fh)

        create_issue.assert_not_called()
        self.assertIn(candidate_url, saved)


if __name__ == "__main__":
    unittest.main()
