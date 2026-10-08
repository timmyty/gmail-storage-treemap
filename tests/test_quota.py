import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gmail_storage_core as core

QUOTA_ERROR = "Quota exceeded for quota metric 'Total Query Cost' and limit 'Units per minute per user' of service 'gmail.googleapis.com'."


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.cancelled = False
        self.on_wait = None
    def clock(self): return self.now
    def is_set(self): return self.cancelled
    def set(self): self.cancelled = True
    def wait(self, seconds):
        self.now += seconds
        if self.on_wait:
            callback, self.on_wait = self.on_wait, None
            callback()
        return self.cancelled


class QuotaTests(unittest.TestCase):
    def test_screenshot_error_recognized_without_word_rate(self):
        self.assertNotIn('rate', QUOTA_ERROR.lower())
        self.assertTrue(core.ApiError(403, QUOTA_ERROR).rate_limited)
        for reason in ['userRateLimitExceeded', 'rateLimitExceeded', 'RATE_LIMIT_EXCEEDED', 'QUOTA_EXCEEDED', 'RESOURCE_EXHAUSTED']:
            self.assertTrue(core.ApiError(403, '', [reason]).rate_limited)
        self.assertFalse(core.ApiError(403, 'Daily quota exceeded', ['dailyLimitExceeded']).rate_limited)
        self.assertFalse(core.ApiError(403, 'Access denied', ['domainPolicy']).rate_limited)

    def test_error_json_reasons_and_retry_after_are_preserved(self):
        body = {'error': {'message': QUOTA_ERROR, 'errors': [{'reason': 'quotaExceeded'}],
                          'details': [{'reason': 'RATE_LIMIT_EXCEEDED', 'retryDelay': '65s'}]}}
        error = HTTPError('https://gmail.googleapis.com/', 403, 'Forbidden', {'Retry-After': '90'}, io.BytesIO(json.dumps(body).encode()))
        with patch.object(core.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(core.ApiError) as caught:
                core.request_json('https://gmail.googleapis.com/')
        self.assertTrue(caught.exception.rate_limited)
        self.assertEqual(caught.exception.retry_after, 90)
        self.assertIn('quotaExceeded', caught.exception.reasons)

    def test_quota_units_pace_240_message_reads_per_minute(self):
        clock = FakeTime()
        gate = core.RequestPacer(clock, lambda _: None, clock.clock)
        times = []
        for _ in range(241):
            gate.acquire(core.quota_cost('messages/id'))
            times.append(clock.now)
        self.assertAlmostEqual(times[-1] - times[0], 60)
        self.assertEqual(core.quota_cost('messages'), 5)
        self.assertEqual(core.quota_cost('profile'), 1)

    def test_waiting_worker_rechecks_cooldown_and_cancel_is_immediate(self):
        clock = FakeTime()
        notices = []
        gate = core.RequestPacer(clock, notices.append, clock.clock)
        gate.acquire(20)
        clock.on_wait = lambda: gate.cooldown(65)
        gate.acquire(20)
        self.assertGreaterEqual(clock.now, 165.25)
        self.assertEqual(gate.units_per_second, 40)
        self.assertTrue(any('quota pause' in n for n in notices))
        gate.cooldown(65)
        clock.on_wait = clock.set
        start = clock.now
        with self.assertRaises(core.Cancelled):
            gate.acquire(20)
        self.assertLessEqual(clock.now - start, 1)

    def test_inflight_errors_share_one_slowdown(self):
        clock = FakeTime()
        gate = core.RequestPacer(clock, lambda _: None, clock.clock)
        for _ in range(6): gate.cooldown(65)
        self.assertEqual(gate.units_per_second, 40)
        self.assertEqual(gate.cooldown_until, 165)

    def test_real_client_retries_same_message_and_honors_retry_after(self):
        clock = FakeTime()
        statuses = []
        client = core.GmailClient({}, {'access_token': 'test-only'}, clock, statuses.append)
        client.pacer = core.RequestPacer(clock, statuses.append, clock.clock)
        times = []
        def request(url, **kwargs):
            times.append(clock.now)
            if len(times) == 1:
                raise core.ApiError(403, QUOTA_ERROR, retry_after=90)
            return {'id': 'same-message', 'sizeEstimate': 7}
        with patch.object(core, 'request_json', side_effect=request), patch.object(core.random, 'random', return_value=0):
            self.assertEqual(client.get('messages/same-message')['id'], 'same-message')
        self.assertEqual(times, [100, 190])

    def test_permission_failure_is_not_retried(self):
        client = core.GmailClient({}, {'access_token': 'test-only'}, threading.Event())
        with patch.object(core, 'request_json', side_effect=core.ApiError(403, 'Access denied', ['domainPolicy'])) as request:
            with self.assertRaises(core.ApiError): client.get('profile')
        self.assertEqual(request.call_count, 1)

    def test_persistent_quota_is_bounded(self):
        clock = FakeTime()
        client = core.GmailClient({}, {'access_token': 'test-only'}, clock)
        client.pacer = core.RequestPacer(clock, lambda _: None, clock.clock)
        with patch.object(core, 'request_json', side_effect=core.ApiError(403, QUOTA_ERROR)) as request, patch.object(core.random, 'random', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'Resume scan'):
                client.get('messages/1')
        self.assertEqual(request.call_count, 8)

    def test_resume_only_reads_missing_records_and_checks_account(self):
        class Client:
            def __init__(self): self.reads = []
            def get(self, endpoint, params=None):
                if endpoint == 'profile': return {'emailAddress': 'a@example.com'}
                if endpoint == 'labels': return {}
                if endpoint == 'messages': return {'messages': [{'id': '1'}, {'id': '2'}]}
                self.reads.append(endpoint)
                return {'id': endpoint.split('/')[-1], 'sizeEstimate': 11}
        record = {'id': '1', 'size': 7, 'sender': 'example@example.com', 'subject': 'Example', 'date': 'Unknown'}
        resume = {'version': 1, 'source': 'Gmail size estimates', 'account': 'a@example.com', 'complete': False,
                  'messages': [record, dict(record, id='deleted')]}
        client = Client()
        progress = []
        data = core.scan_gmail(client, threading.Event(), lambda _: None, lambda _: None,
                               resume=resume, scan_progress=progress.append)
        self.assertEqual(client.reads, ['messages/2'])
        self.assertEqual(sum(m['size'] for m in data['messages']), 18)
        self.assertTrue(data['complete'])
        self.assertEqual(progress[-1]['completed'], 2)
        self.assertEqual(progress[-1]['total'], 2)
        self.assertEqual(progress[-1]['reused'], 1)
        resume['account'] = 'other@example.com'
        with self.assertRaisesRegex(ValueError, 'different mailbox'):
            core.scan_gmail(client, threading.Event(), lambda _: None, lambda _: None, resume=resume)

    def test_fatal_error_preserves_successes_from_same_batch(self):
        class Client:
            def get(self, endpoint, params=None):
                if endpoint == 'profile': return {'emailAddress': 'a@example.com'}
                if endpoint == 'labels': return {}
                if endpoint == 'messages': return {'messages': [{'id': str(i)} for i in range(6)]}
                if endpoint == 'messages/1': raise RuntimeError('Quota unavailable')
                return {'id': endpoint.split('/')[-1], 'sizeEstimate': 9}
        partials = []
        with self.assertRaisesRegex(RuntimeError, 'Quota unavailable'):
            core.scan_gmail(Client(), threading.Event(), lambda _: None,
                            lambda data: partials.append(dict(data, messages=list(data['messages']))))
        self.assertEqual(len(partials[-1]['messages']), 5)
        self.assertFalse(partials[-1]['complete'])


if __name__ == '__main__': unittest.main(verbosity=2)
