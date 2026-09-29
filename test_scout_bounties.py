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
                scout.save_seen_bounties([
                    "https://github.com/z/repo/issues/2",
                    "https://github.com/a/repo/issues/1",
                ])

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


class EconomicStatusTests(unittest.TestCase):
    def _classify(self, title, body, comments=0):
        return scout.classify_candidate({
            "title": title,
            "body": body,
            "comments": comments,
        })

    def test_unfunded_proposal_is_not_treated_as_payable_bounty(self):
        result = self._classify(
            "Python CLI bounty proposal $10",
            "This is an unfunded proposal, not an approved award, reservation, or payment claim.",
        )
        self.assertEqual(result.get("economic_status"), "UNFUNDED_PROPOSAL")

    def test_existing_implementation_pr_is_already_implemented(self):
        result = self._classify(
            "Bounty proposal: sanitize exception disclosure $50 USDC",
            "**Pull Request:** https://github.com/example/repo/pull/19477\n6/6 tests pass.",
        )
        self.assertEqual(result.get("economic_status"), "ALREADY_IMPLEMENTED")

    def test_wait_for_assignment_is_apply_first(self):
        result = self._classify(
            "Add boundary and recovery test coverage",
            "Before coding, describe the plan and estimate. Wait for assignment before starting implementation.",
        )
        self.assertEqual(result.get("economic_status"), "APPLY_FIRST")

    def test_explicit_funded_bounty_is_funded(self):
        result = self._classify(
            "Funded documentation bounty $100 USDC",
            "Funded bounty. Reward is reserved on Algora and available to the accepted contributor.",
        )
        self.assertEqual(result.get("economic_status"), "FUNDED")

    def test_generic_reward_signal_requires_verification(self):
        result = self._classify(
            "Python documentation reward $25",
            "Reward offered for an accepted pull request.",
        )
        self.assertEqual(result.get("economic_status"), "VERIFY")

    def test_unfunded_proposal_is_suppressed_from_alert_and_marked_seen(self):
        candidate_url = "https://github.com/example/project/issues/777"
        candidate = {
            "title": "Python documentation bounty proposal $50",
            "body": "Unfunded proposal; not an approved award. README review and tests.",
            "html_url": candidate_url,
            "comments": 0,
            "updated_at": "2026-09-29T07:00:00Z",
            "assignees": [],
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
                patch.object(scout, "search_github", return_value={"items": [candidate]}),
                patch.object(scout, "create_github_issue", create_issue),
                patch.dict(os.environ, env, clear=True),
            ):
                scout.main()

            with open(state_file, "r", encoding="utf-8") as fh:
                saved = json.load(fh)

        create_issue.assert_not_called()
        self.assertIn(candidate_url, saved)

    def test_funded_candidate_sorts_ahead_of_verify_candidate(self):
        funded = {
            "title": "Funded bounty $100 USDC",
            "body": "Funded bounty; reward reserved on Algora.",
            "html_url": "https://github.com/example/repo/issues/1",
            "comments": 0,
            "updated_at": "2026-09-29T07:00:00Z",
            "assignees": [],
        }
        verify = {
            "title": "Python documentation quality review bounty $25",
            "body": "Paid reward for README markdown review and tests.",
            "html_url": "https://github.com/example/repo/issues/2",
            "comments": 0,
            "updated_at": "2026-09-29T07:01:00Z",
            "assignees": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "seen.json")
            with open(state_file, "w", encoding="utf-8") as fh:
                json.dump([], fh)

            captured = {}

            def fake_issue(repo, token, title, body):
                captured["body"] = body
                return True

            env = {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "uknwplayer/BountyScout",
            }
            with (
                patch.object(scout, "STATE_FILE", state_file),
                patch.object(scout, "SEARCH_QUERIES", ["test-query"]),
                patch.object(scout, "search_github", return_value={"items": [verify, funded]}),
                patch.object(scout, "create_github_issue", side_effect=fake_issue),
                patch.dict(os.environ, env, clear=True),
            ):
                scout.main()

        self.assertLess(
            captured["body"].index("issues/1"),
            captured["body"].index("issues/2"),
        )


if __name__ == "__main__":
    unittest.main()
