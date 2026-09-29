import unittest

import scout_bounties as scout


class StandaloneClaimDetectionTests(unittest.TestCase):
    def test_standalone_claim_marks_candidate_claimed(self):
        status = scout.classify_comment_status([
            {"body": " /claim "}
        ])
        self.assertEqual(status, "CLAIMED")


if __name__ == "__main__":
    unittest.main()
