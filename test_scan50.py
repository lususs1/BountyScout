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


class Scan54Tests(unittest.TestCase):
    # Verbatim API snapshots/excerpt retrieved 2026-10-07. StudioOS#1372
    # returned 404; its alert title is tested separately, not as a body snapshot.
    items = [{'title': 'commit-monitor: new leads 2026-10-07_0631Z', 'body': '# commit-monitor digest 2026-10-07_0631\n\n38 configured target(s); 38 attempted; 4 coverage error(s).\n\n1401 security-relevant commit lead(s), ranked:\n\n## [17] [SECURITY FIX] disclosed-fix variant analysis — github:nextcloud/server `cfb3c4f99f`\n- **fix(encryption): clarify how to enable server-side encryption**\n- Nextcloud Vulnerability Disclosure · vdp · web · 2026-09-27T00:39:08Z · Josh\n- https://github.com/nextcloud/server/commit/cfb3c4f99f1fa5aae9cda989657ccec589af0922\n  - explicit security-fix signal: security\n  - patch signal: fix\n  - feature introduction: enable \n  - hot paths: admin, set', 'html_url': 'https://github.com/ahmedfuzayl-gif/commit-monitor/issues/121', 'labels': [], 'state': 'open', 'comments': 0, 'assignees': []}, {'title': 'Bootstrap a valid production deployment safely', 'body': "## Problem or idea\n\nThe current source/Compose and component-wheel workflows require operator setup. The easy-install wishlist needs one guided path that establishes a valid composed deployment and explains actionable failures without accepting placeholder production secrets or hiding missing services. This child is limited to: bootstrap a valid production deployment safely.\n\n## Expected outcome\n\nDeliver idempotent initial setup using exact qualified artifacts and normal auth/data boundaries.\n\n## Environment (if relevant)\n\nAstral system; owning repository: AstralDeep. Baseline: `e9a47d8b826dc506dd509b6110a5199ad1cddc42`. Python changes remain compatible with production Python 3.11. Preserve current Keycloak/RFC 8693, owner/tool/PHI/egress/confirmation and audit boundaries. Shared UI changes ship coherent server-owned vocabulary and all affected client dispositions.\n\n## Acceptance checks (optional)\n\n- [ ] Configure real Keycloak/services, local/runtime-only secrets and Plane guarded startup/readiness without mock auth or placeholders.\n- [ ] Use exact qualified component/artifact identities; user completes normal sign-in and in-product encrypted provider configuration.\n- [ ] Test clean-host installation, rerun, failed dependencies/integrity and exit-78 negative cases; stage a successful production-posture bootstrap.\n- [ ] Include deterministic success, edge, denial, failure and recovery tests, at least 90% changed-code coverage, bounded required CI and necessary real-dispatch/staging/client verification in this same contribution.\n\n## Supporting details (optional)\n\nTracking issue: [AstralDeep #246](https://github.com/AstralDeep/AstralDeep/issues/246). **Bounty: 100 recognition points**; this is a separately scoped contribution from the tracker's original 400-point allocation.\n\nDependencies: [AstralDeep #292](https://github.com/AstralDeep/AstralDeep/issues/292).\n\nExisting linked work: [AstralDeep PR #247](https://github.com/AstralDeep/AstralDeep/pull/247); retain its relationship to the tracker and coordinate scope against these acceptance checks.\n\nOne PR earns one bounty award after a qualifying main merge closes its matching child issue. The parent is a non-bounty tracker; mentioning or closing it does not award points. Avoid a PR closing multiple eligible bounty issues.\n\nDecomposition reference: `wishlist-20261005/install_bootstrap`.\n\nSource evidence:\n- [Current setup entry point](https://github.com/AstralDeep/AstralDeep/blob/e9a47d8b826dc506dd509b6110a5199ad1cddc42/README.md)\n- [Existing composed deployment](https://github.com/AstralDeep/AstralDeep/blob/e9a47d8b826dc506dd509b6110a5199ad1cddc42/docker-compose.yml)\n- [Exact local component-wheel workflow](https://github.com/AstralDeep/AstralDeep/blob/e9a47d8b826dc506dd509b6110a5199ad1cddc42/scripts/install_local_components.py)\n", 'html_url': 'https://github.com/AstralDeep/AstralDeep/issues/293', 'labels': [{'name': 'enhancement', 'color': 'a2eeef', 'description': 'New feature or request'}, {'name': 'bounty', 'color': '6366F1', 'description': 'Approved points-based community task'}, {'name': 'points:100', 'color': '8B5CF6', 'description': '100 recognition points'}, {'name': 'track:security', 'color': '06B6D4', 'description': 'Security, authority, and LETS'}, {'name': 'track:reusability', 'color': '06B6D4', 'description': 'Independent reusable components'}, {'name': 'priority:P2', 'color': 'D4A72C', 'description': 'Normal priority'}], 'state': 'open', 'comments': 1, 'assignees': []}]

    def test_digest_is_meta_alert(self):
        self.assertTrue(scout.is_meta_alert(self.items[0]))

    def test_recognition_points_are_not_cash(self):
        self.assertEqual(scout.classify_candidate(self.items[1])['economic_status'], 'NOT_AN_OFFER')

    def test_separate_cash_reward_on_points_task_survives(self):
        item = dict(self.items[1], body=self.items[1]['body'] + '\nBounty: $80 for this contribution.')
        self.assertIn(scout.classify_candidate(item)['economic_status'], {'VERIFY', 'FUNDED'})

    def test_paid_top_up_alert_title_is_not_offer(self):
        item = {'title': 'AI spend: per-licence caps, and a paid top-up behind "Add tokens" (D629, migration 419)', 'body': '', 'labels': ['state:ready']}
        self.assertEqual(scout.classify_candidate(item)['economic_status'], 'NOT_AN_OFFER')
        item['body'] = 'Bounty: $80 for implementing this feature.'
        self.assertIn(scout.classify_candidate(item)['economic_status'], {'VERIFY', 'FUNDED'})

    def test_main_excludes_digest_and_points_without_network_or_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.object(scout, 'STATE_FILE', str(Path(tmp) / 'seen.json')),
                  patch.object(scout, 'SEARCH_QUERIES', ['test']),
                  patch.object(scout, 'search_github', return_value={'items': self.items}),
                  patch.object(scout, 'fetch_review_resource') as read,
                  patch.object(scout, 'create_github_issue') as notify,
                  patch.dict(os.environ, {}, clear=True)):
                scout.main()
            read.assert_not_called()
            notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
