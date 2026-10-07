import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import scout_bounties as scout


class Scan50Tests(unittest.TestCase):
    def setUp(self):
        self.items = json.loads((Path(__file__).parent / 'tests/scan50.json').read_text())
        self.paid = copy.deepcopy(self.items[4])
        self.paid.update(comments=0, body='Add documentation and security tests.')

    def candidate(self, item=None):
        item = item or self.paid
        return {'url': item['html_url'], 'comments': item['comments'],
                **scout.classify_candidate(item)}

    def review(self, item=None, comments=None, timeline=None, prs=None):
        item = item or self.paid
        def read(url, token, collection=False):
            if url.endswith('/comments'):
                return comments if comments is not None else []
            if url.endswith('/timeline'):
                return timeline if timeline is not None else []
            if '/pulls/' in url:
                return (prs or {}).get(url.rsplit('/', 1)[1])
            return item
        with patch.object(scout, 'fetch_review_resource', side_effect=read):
            return scout.refresh_finalists([self.candidate()], None)

    def test_actual_scan50_product_features_are_not_offers(self):
        for item in self.items[1:3]:
            self.assertEqual(scout.classify_candidate(item)['economic_status'], 'NOT_AN_OFFER')

    def test_same_product_work_with_real_reward_survives(self):
        for original in self.items[1:3]:
            for offer in ('[Bounty: $80] ', 'Funded bounty $80: '):
                item = dict(original, title=offer + original['title'])
                self.assertIn(scout.classify_candidate(item)['economic_status'], {'VERIFY', 'FUNDED'})

    def test_actual_in_progress_and_closed_are_rejected(self):
        for index in (0, 3):
            self.assertFalse(scout.is_clean_candidate(self.items[index]))

    def test_label_normalization(self):
        for label in (' State:In-Progress ', {'name': 'STATUS:IN-PROGRESS'}, 'in progress'):
            self.assertFalse(scout.is_clean_candidate(dict(self.paid, labels=[label])))

    def test_newly_closed_or_occupied_finalist_not_retained(self):
        for item in (dict(self.paid, state='closed'), dict(self.paid, labels=['state:in-progress']),
                     dict(self.paid, assignees=[{'login': 'other'}])):
            self.assertEqual(self.review(item), [])

    def test_latest_body_can_remove_payment_signal(self):
        self.assertEqual(self.review(dict(self.paid, title='Documentation tests')), [])

    def test_live_unoccupied_paid_issue_survives(self):
        self.assertEqual(len(self.review()), 1)

    def test_actual_susu9_resolved_pr_shorthand_is_checked(self):
        comments = [{'body': '/attempt #9\n\nResolved in PR #13\n\n/claim #9'}]
        self.assertEqual(self.review(comments=comments, prs={'13': {'state': 'open', 'body': 'Closes #9'}}), [])

    def test_linked_open_or_merged_implementation_blocks(self):
        event = {'event': 'cross-referenced', 'source': {'issue': {
            'html_url': 'https://github.com/SUSU-LABS/susu-contracts/pull/13', 'pull_request': {}}}}
        # API pull_request is a nonempty object.
        event['source']['issue']['pull_request'] = {'url': 'api'}
        for pr in ({'state': 'open', 'body': 'Fixes #9'},
                   {'state': 'closed', 'merged_at': '2026-10-07', 'body': 'Closes #9'}):
            self.assertEqual(self.review(timeline=[event], prs={'13': pr}), [])

    def test_unrelated_reference_or_abandoned_pr_does_not_block(self):
        comments = [{'body': 'Resolved in PR #13'}]
        for pr in ({'state': 'open', 'body': 'Closes #90'},
                   {'state': 'closed', 'merged_at': None, 'body': 'Closes #9'},
                   {'state': 'open', 'body': 'Reference #9 as an example'}):
            self.assertEqual(len(self.review(comments=comments, prs={'13': pr})), 1)

    def test_different_repository_short_number_does_not_block(self):
        timeline = [{'event': 'cross-referenced', 'source': {'issue': {
            'html_url': 'https://github.com/example/other/pull/13', 'pull_request': {'url': 'api'}}}}]
        self.assertEqual(len(self.review(timeline=timeline, prs={'13': {'state': 'open', 'body': 'Closes #9'}})), 1)

    def test_failed_review_leaves_candidate_retryable(self):
        with patch.object(scout, 'fetch_review_resource', return_value=None):
            candidate = self.candidate()
            self.assertEqual(scout.refresh_finalists([candidate], None), [])
            self.assertEqual(candidate, self.candidate())

    def test_failed_linked_pr_review_defers_candidate(self):
        self.assertEqual(self.review(comments=[{'body': 'Resolved in PR #13'}]), [])

    def test_zero_stale_comment_count_still_reads_new_comments(self):
        result = self.review(comments=[{'body': '/claim'}])
        # Latest comment count can race the comments endpoint; review it regardless.
        self.assertEqual(scout.review_finalist_comments(result, None, set()), [])

    def test_review_bound_defers_excess_without_seen_mutation(self):
        candidates = [self.candidate() for _ in range(12)]
        with patch.object(scout, 'fetch_review_resource', side_effect=lambda url, token, collection=False: [] if collection else self.paid):
            self.assertEqual(len(scout.refresh_finalists(candidates, None)), scout.COMMENT_REVIEW_LIMIT)

    def test_paginated_review_reads_all_pages(self):
        responses = []
        for data in ([{'body': 'reference'}] * 100, [{'body': '/claim'}]):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(data).encode()
            responses.append(response)
        with patch.object(scout.urllib.request, 'urlopen', side_effect=responses) as read:
            result = scout.fetch_review_resource('https://api.github.com/repos/example/repo/issues/1/comments', None, True)
        self.assertEqual(len(result), 101)
        self.assertIn('page=2', read.call_args.args[0].full_url)

    def test_real_main_refresh_preserves_paid_and_retries_failed_reads(self):
        # Run the production path rather than bypassing final refresh.
        items = copy.deepcopy(self.items) + [dict(self.paid, html_url='https://github.com/example/repo/issues/80')]
        by_url = {i['html_url']: i for i in items}
        def read(url, token, collection=False):
            if collection:
                if url.endswith('/comments') and '/issues/9/' in url:
                    return [{'body': 'Resolved in PR #13'}]
                return []
            if '/pulls/13' in url:
                return {'state': 'open', 'body': 'Closes #9'}
            return by_url[url.replace('https://api.github.com/repos/', 'https://github.com/')]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'seen.json'
            with (patch.object(scout, 'STATE_FILE', str(state)),
                  patch.object(scout, 'SEARCH_QUERIES', ['test']),
                  patch.object(scout, 'search_github', return_value={'items': items}),
                  patch.object(scout, 'fetch_review_resource', side_effect=read),
                  patch.object(scout, 'create_github_issue', return_value=True) as notify,
                  patch.dict(os.environ, {'GITHUB_TOKEN': 'test', 'GITHUB_REPOSITORY': 'uknwplayer/BountyScout'}, clear=True)):
                scout.main()
            self.assertEqual(notify.call_count, 1)
            body = notify.call_args.args[3]
            self.assertIn(items[-1]['html_url'], body)
            for item in items[:-1]:
                self.assertNotIn(item['html_url'], body)
            self.assertNotIn(self.paid['html_url'], json.loads(state.read_text()))
            with (patch.object(scout, 'STATE_FILE', str(state)),
                  patch.object(scout, 'SEARCH_QUERIES', ['test']),
                  patch.object(scout, 'search_github', return_value={'items': [self.paid]}),
                  patch.object(scout, 'fetch_review_resource', return_value=None),
                  patch.object(scout, 'create_github_issue') as notify,
                  patch.dict(os.environ, {}, clear=True)):
                scout.main()
            notify.assert_not_called()
            self.assertNotIn(self.paid['html_url'], json.loads(state.read_text()))


if __name__ == '__main__':
    unittest.main()
