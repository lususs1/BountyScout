import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scout_bounties as scout


class Scan51Tests(unittest.TestCase):
    def setUp(self):
        self.items = json.loads((Path(__file__).parent / 'tests/scan51.json').read_text())

    def test_original_issue_classifications(self):
        expected = ['NOT_AN_OFFER', 'UNKNOWN', 'CLAIMED', 'NOT_AN_OFFER', 'UNKNOWN', 'VERIFY']
        self.assertEqual([scout.classify_candidate(i)['economic_status'] for i in self.items], expected)

    def test_scan_only_alerts_paid_issue_and_reconsiders_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'seen.json'
            with (
                patch.object(scout, 'STATE_FILE', str(state)),
                patch.object(scout, 'SEARCH_QUERIES', ['test']),
                patch.object(scout, 'search_github', return_value={'items': self.items}),
                patch.object(scout, 'fetch_issue_comments', return_value=[]),
                patch.object(scout, 'create_github_issue', return_value=True) as notify,
                patch.dict(os.environ, {'GITHUB_TOKEN': 'test', 'GITHUB_REPOSITORY': 'uknwplayer/BountyScout'}, clear=True),
            ):
                with patch.object(scout, "refresh_finalists", side_effect=lambda items, token: items):
                    scout.main()
            body = notify.call_args.args[3]
            self.assertEqual(notify.call_count, 1)
            self.assertIn(self.items[-1]['html_url'], body)
            for item in self.items[:-1]:
                self.assertNotIn(item['html_url'], body)
            seen = json.loads(state.read_text())
            for index in (1, 4):
                self.assertNotIn(self.items[index]['html_url'], seen)

    def test_claimed_label_normalization_and_precedence(self):
        for label in ('status:claimed', ' Status: Claimed ', {'name': 'STATUS: CLAIMED'}):
            with self.subTest(label=label):
                result = scout.classify_candidate({'title': 'Funded bounty $80', 'body': 'Funded bounty', 'labels': [label]})
                self.assertEqual(result['economic_status'], 'CLAIMED')

    def test_opire_help_is_not_an_offer_but_actual_reward_survives(self):
        help_block = self.items[1]['body'].split('<details>', 1)[1]
        help_block = '<details>' + help_block
        for body, labels, expected in (
            ('Documentation test work\n' + help_block, [], 'UNKNOWN'),
            ('Machine-managed. Funded bounty $80\n' + help_block, [], 'FUNDED'),
            ('Documentation test work\n' + help_block, ['bounty: $80'], 'VERIFY'),
            ('Reward offered for accepted PR\n' + help_block, [], 'VERIFY'),
        ):
            with self.subTest(expected=expected, body=body[:40]):
                self.assertEqual(scout.classify_candidate({'title': 'Task', 'body': body, 'labels': labels})['economic_status'], expected)

    def test_no_bounty_phrases_and_related_paid_work(self):
        for phrase in ('This project has no bounties.', 'No bounty is offered.', 'We do not offer bounties.'):
            self.assertEqual(scout.classify_candidate({'title': 'Tests', 'body': phrase})['economic_status'], 'NOT_AN_OFFER')
        self.assertEqual(scout.classify_candidate({'title': 'Bounty $80: add payment proof tests', 'body': 'Reward offered for proof of contributor payments.'})['economic_status'], 'VERIFY')

    def test_finalist_review_excludes_unknown_without_marking_seen(self):
        seen = set()
        candidate = {'url': 'https://github.com/example/repo/issues/1', 'comments': 0, **scout.classify_candidate({'title': 'Documentation tests'})}
        self.assertEqual(scout.review_finalist_comments([candidate], None, seen), [])
        self.assertEqual(seen, set())


if __name__ == '__main__':
    unittest.main()
