import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gmail_storage_core as core


class StorageTests(unittest.TestCase):
    def test_multi_label_messages_count_once_in_every_view(self):
        messages = core.demo_snapshot()['messages']
        expected = sum(m['size'] for m in messages)
        for mode in ['Sender', 'Domain', 'Year', 'Location', 'Label set']:
            grouped = core.groups(messages, mode)
            self.assertEqual(sum(g['size'] for g in grouped), expected)
            self.assertEqual(sum(len(g['messages']) for g in grouped), len(messages))

    def test_treemap_area_is_proportional_and_rectangles_do_not_overlap(self):
        rng = random.Random(8)
        for n in [1, 2, 7, 161]:
            items = [{'name': str(i), 'size': rng.randint(1, 999999)} for i in range(n)]
            rects = core.treemap(items, 0, 0, 1000, 600)
            total = sum(x['size'] for x in items)
            self.assertEqual(len(rects), n)
            for item, x, y, w, h in rects:
                self.assertAlmostEqual(w * h / 600000, item['size'] / total)
                self.assertGreaterEqual(x, -1e-8)
                self.assertGreaterEqual(y, -1e-8)
                self.assertLessEqual(x + w, 1000 + 1e-8)
                self.assertLessEqual(y + h, 600 + 1e-8)
            for i, (_, x, y, w, h) in enumerate(rects):
                for _, xx, yy, ww, hh in rects[i + 1:]:
                    overlap = max(0, min(x + w, xx + ww) - max(x, xx)) * max(0, min(y + h, yy + hh) - max(y, yy))
                    self.assertLess(overlap, 1e-7)

    def test_zero_size_and_empty_map(self):
        self.assertEqual(core.treemap([], 0, 0, 100, 100), [])
        self.assertEqual(core.treemap([{'size': 0}], 0, 0, 100, 100), [])

    def test_snapshot_rejects_duplicate_ids_and_invalid_bytes(self):
        data = core.demo_snapshot()
        core.validate_snapshot(json.loads(json.dumps(data)))
        data['messages'].append(data['messages'][0])
        with self.assertRaises(ValueError):
            core.validate_snapshot(data)
        data['messages'].pop()
        data['messages'][0]['size'] = -2
        with self.assertRaises(ValueError):
            core.validate_snapshot(data)

    def test_streamed_mbox_counts_attachment_bytes_without_loading_bodies(self):
        attachment = b'A' * 2000000
        first = b'From: Person <person@example.com>\r\nSubject: =?utf-8?q?Vacation_=E2=9C=93?=\r\nDate: Wed, 7 Oct 2026 12:00:00 -0500\r\nX-Gmail-Labels: Inbox,Work\r\nMessage-ID: <one@example.com>\r\n\r\n' + attachment + b'\r\n'
        second = b'From: news@example.com\nSubject: Short\n\nHello\n'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / 'sample.mbox'
            path.write_bytes(b'From person@example.com Wed Oct 7 12:00:00 2026\n' + first + b'From news@example.com Wed Oct 7 12:01:00 2026\n' + second)
            data = core.import_mbox(path, threading.Event(), lambda _: None)
        self.assertTrue(data['complete'])
        self.assertEqual(len(data['messages']), 2)
        self.assertEqual(data['messages'][0]['size'], len(first))
        self.assertEqual(data['messages'][1]['size'], len(second))
        self.assertEqual(data['messages'][0]['subject'], 'Vacation ✓')
        self.assertEqual(data['messages'][0]['labels'], ['INBOX', 'Work'])
        self.assertNotIn('body', data['messages'][0])

    def test_pagination_dedup_and_metadata_request(self):
        class Client:
            def __init__(self):
                self.calls = []
            def get(self, endpoint, params=None):
                self.calls.append((endpoint, params))
                if endpoint == 'profile':
                    return {'emailAddress': 'person@example.com'}
                if endpoint == 'labels':
                    return {'labels': [{'id': 'Label_1', 'name': 'Work'}]}
                if endpoint == 'messages':
                    if params.get('pageToken'):
                        return {'messages': [{'id': 'two'}, {'id': 'three'}]}
                    return {'messages': [{'id': 'one'}, {'id': 'two'}], 'nextPageToken': 'next'}
                return {'id': endpoint.split('/')[-1], 'sizeEstimate': 12000000,
                        'labelIds': ['Label_1', 'INBOX'], 'internalDate': '1791410400000',
                        'payload': {'headers': [{'name': 'From', 'value': 'Person <person@example.com>'}]}}
        client = Client()
        data = core.scan_gmail(client, threading.Event(), lambda _: None, lambda _: None)
        self.assertEqual(len(data['messages']), 3)
        self.assertTrue(data['complete'])
        self.assertEqual(sum(m['size'] for m in data['messages']), 36000000)
        for endpoint, params in client.calls:
            if endpoint == 'messages':
                self.assertEqual(params['includeSpamTrash'], 'true')
            elif endpoint.startswith('messages/'):
                self.assertEqual(params['format'], 'metadata')
                self.assertNotIn('body', params['fields'])

    def test_failed_and_cancelled_scans_never_claim_complete(self):
        stop = threading.Event()
        class Client:
            def get(self, endpoint, params=None):
                if endpoint == 'profile': return {'emailAddress': 'p@example.com'}
                if endpoint == 'labels': return {}
                if endpoint == 'messages': return {'messages': [{'id': str(i)} for i in range(100)]}
                if endpoint == 'messages/1': raise core.ApiError(404, 'gone')
                return {'id': endpoint.split('/')[-1], 'sizeEstimate': 123}
        failed = core.scan_gmail(Client(), stop, lambda _: None, lambda _: None)
        self.assertFalse(failed['complete'])
        self.assertEqual(failed['failed'], 1)
        self.assertEqual(len(failed['messages']), 99)
        stopped = core.scan_gmail(Client(), stop, lambda _: None, lambda _: stop.set())
        self.assertFalse(stopped['complete'])
        self.assertLess(len(stopped['messages']), 100)


if __name__ == '__main__':
    unittest.main(verbosity=2)
