"""The PCE client must never change the PCE: only GET, plus the Explorer traffic query it creates
and deletes itself. Run with: python -m unittest discover tests"""
import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pce  # noqa: E402

BASE = "https://pce.example.com:8443/api/v2/orgs/1"
QUERY = "/orgs/1/traffic_flows/async_queries/11111111-2222-3333-4444-555555555555"


def response(status=200, body=None):
    r = requests.Response()
    r.status_code = status
    r._content = (b"[]" if body is None else body)
    return r


class ReadOnlyTest(unittest.TestCase):
    def setUp(self):
        self.client = pce.PCE("test", {"pce_fqdn": "pce.example.com", "port": 8443, "org": 1, "api_key": "k", "api_secret": "s"})
        patcher = mock.patch.object(requests.Session, "request", return_value=response())
        self.sent = patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_is_sent(self):
        self.client.get("/labels")
        self.assertEqual(self.sent.call_count, 1)

    def test_writes_are_refused_before_sending(self):
        for method, path in (("POST", "/labels"), ("PUT", "/labels/1"), ("PATCH", "/workloads/1"), ("DELETE", "/workloads/1"),
                             ("POST", "/sec_policy"), ("PUT", "/sec_policy/draft/rule_sets/1"), ("DELETE", "/sec_policy/draft/rule_sets/1"),
                             ("POST", "/workloads/bulk_delete"), ("POST", "/traffic_flows/async_queries/x/anything"),
                             ("DELETE", "/traffic_flows/async_queries/someone-elses-query")):
            with self.subTest(method=method, path=path), self.assertRaises(pce.PCEError):
                self.client.session.request(method, BASE + path)
        self.assertEqual(self.sent.call_count, 0)

    def test_other_hosts_and_plain_http_are_refused(self):
        for url in ("https://elsewhere.example.com:8443/api/v2/orgs/1/labels", "http://pce.example.com:8443/api/v2/orgs/1/labels",
                    "https://pce.example.com:9999/api/v2/orgs/1/labels"):
            with self.subTest(url=url), self.assertRaises(pce.PCEError):
                self.client.session.get(url)
        self.assertEqual(self.sent.call_count, 0)

    def test_traffic_query_is_created_and_only_that_query_deleted(self):
        self.sent.side_effect = [response(202, b'{"href": "%s", "status": "queued"}' % QUERY.encode()),
                                 response(200, b'{"status": "completed"}'), response(200, b"[]"), response(204, b"")]
        self.assertEqual(self.client.traffic_flows(30, ("blocked",)), [])
        methods = [call.args[0] for call in self.sent.call_args_list]
        self.assertEqual(methods, ["POST", "GET", "GET", "DELETE"])
        self.assertTrue(self.sent.call_args_list[-1].args[1].endswith(QUERY))


if __name__ == "__main__":
    unittest.main()
