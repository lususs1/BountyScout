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


class EconomicContextRegressionTests(unittest.TestCase):
    def _classify(self, title, body):
        return scout.classify_candidate({
            "title": title,
            "body": body,
            "comments": 0,
            "labels": [],
        })["economic_status"]

    def test_design_question_that_only_mentions_bug_bounty_is_not_an_offer(self):
        status = self._classify(
            "Design question: do spending policies cover the sign path?",
            (
                "This is a design/documentation question, not a vulnerability report. "
                "If enforcement is missing, I'll take it to the bug bounty per SECURITY.md."
            ),
        )
        self.assertEqual(status, "NOT_AN_OFFER")

    def test_observatory_note_about_bounty_discovery_is_not_an_offer(self):
        status = self._classify(
            "Field Note 001 — GitHub bot food chain",
            (
                "This note records public code and outputs from automated GitHub bounty "
                "discovery systems. Observed means visible in source."
            ),
        )
        self.assertEqual(status, "NOT_AN_OFFER")

    def test_companion_submission_built_for_external_bounty_is_not_an_offer(self):
        status = self._classify(
            "[companion] invoice reconciler",
            (
                "Repository includes SKILL.md and tests. Built for the live Superteam bounty "
                "Build and Demo a Mermail Agent Skill (500 USDC); video handled separately."
            ),
        )
        self.assertEqual(status, "NOT_AN_OFFER")

    def test_existing_project_proposal_for_bounty_track_is_not_an_offer(self):
        status = self._classify(
            "Proposal: Lossless Prediction Market",
            (
                "Shipment status: contracts built and tested. Testnet deploy is next after approval. "
                "Bounty track: Lossless Prediction Market."
            ),
        )
        self.assertEqual(status, "NOT_AN_OFFER")

    def test_real_work_offer_with_proposal_word_still_requires_verification(self):
        status = self._classify(
            "Proposal bounty: improve README validation",
            "Reward offered for an accepted pull request. Please implement tests and documentation.",
        )
        self.assertEqual(status, "VERIFY")


class WaveAcceptanceRegressionTests(unittest.TestCase):
    def test_accepted_wave_application_marks_issue_claimed(self):
        status = scout.classify_comment_status([
            {
                "body": (
                    "Congratulations, @worker! Your application was accepted by the repo's "
                    "maintainers, and the issue is due on September 30, 2026."
                )
            }
        ])
        self.assertEqual(status, "CLAIMED")

    def test_unaccepted_wave_application_remains_apply_first(self):
        status = scout.classify_comment_status([
            {
                "body": (
                    "@worker has applied to work on this issue as part of the Stellar Wave Program. "
                    "Repo Maintainers: review their application or assign @worker."
                )
            }
        ])
        self.assertEqual(status, "APPLY_FIRST")


if __name__ == "__main__":
    unittest.main()
