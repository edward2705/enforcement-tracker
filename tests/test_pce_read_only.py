"""The PCE client must never change the PCE: only GET, plus the Explorer traffic query it creates,
asks for draft decisions on, and deletes itself. Run with: python -m unittest discover tests

The network adapter is mocked, so every request goes through the real ReadOnlySession check and
`self.sent` records only what would have left the machine."""
import json
import os
import re
import sys
import unittest
from unittest import mock

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
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
        patcher = mock.patch.object(requests.adapters.HTTPAdapter, "send", return_value=response())
        self.sent = patcher.start()
        self.addCleanup(patcher.stop)

    def methods(self):
        return [(call.args[0].method, call.args[0].path_url.split("/api/v2")[1]) for call in self.sent.call_args_list]

    def test_get_is_sent(self):
        self.client.get("/labels")
        self.assertEqual(self.sent.call_count, 1)

    def test_writes_are_refused_before_sending(self):
        for method, path in (("POST", "/labels"), ("PUT", "/labels/1"), ("PATCH", "/workloads/1"), ("DELETE", "/workloads/1"),
                             ("POST", "/sec_policy"), ("PUT", "/sec_policy/draft/rule_sets/1"), ("DELETE", "/sec_policy/draft/rule_sets/1"),
                             ("POST", "/workloads/bulk_delete"), ("POST", "/traffic_flows/async_queries/x/anything"),
                             ("DELETE", "/traffic_flows/async_queries/someone-elses-query"),
                             ("PUT", "/traffic_flows/async_queries/someone-elses-query/update_rules"),
                             ("POST", "/traffic_flows/async_queries/someone-elses-query/update_rules"),
                             ("HEAD", "/labels"), ("OPTIONS", "/labels")):
            with self.subTest(method=method, path=path), self.assertRaises(pce.PCEError):
                self.client.session.request(method, BASE + path)
        self.assertEqual(self.sent.call_count, 0)

    def test_prepared_requests_are_checked_too(self):
        prepared = self.client.session.prepare_request(requests.Request("DELETE", BASE + "/workloads/1"))
        with self.assertRaises(pce.PCEError):
            self.client.session.send(prepared)
        self.assertEqual(self.sent.call_count, 0)

    def test_bodies_are_checked(self):
        cases = (("GET", "/labels", {"json": {"name": "x"}}),                                   # GET never has a body
                 ("POST", "/traffic_flows/async_queries", {"json": {"query_name": "q", "href": "/orgs/1/labels/1"}}),  # unknown field
                 ("POST", "/traffic_flows/async_queries", {"data": "not json"}),
                 ("POST", "/traffic_flows/async_queries", {}))                                  # empty query
        for method, path, kwargs in cases:
            with self.subTest(method=method, kwargs=kwargs), self.assertRaises(pce.PCEError):
                self.client.session.request(method, BASE + path, **kwargs)
        self.assertEqual(self.sent.call_count, 0)

    def test_other_hosts_and_plain_http_are_refused(self):
        for url in ("https://elsewhere.example.com:8443/api/v2/orgs/1/labels", "http://pce.example.com:8443/api/v2/orgs/1/labels",
                    "https://pce.example.com:9999/api/v2/orgs/1/labels"):
            with self.subTest(url=url), self.assertRaises(pce.PCEError):
                self.client.session.get(url)
        self.assertEqual(self.sent.call_count, 0)

    def test_redirects_are_not_followed(self):
        def redirect(prepared, **_kwargs):
            r = response(302, b"")
            r.headers["Location"] = BASE + "/workloads/1"
            r.request, r.url = prepared, prepared.url
            return r
        self.sent.side_effect = redirect
        self.assertEqual(self.client.session.get(BASE + "/labels").status_code, 302)
        self.assertEqual(self.sent.call_count, 1)

    def test_traffic_query_is_created_and_only_that_query_deleted(self):
        self.sent.side_effect = [response(202, b'{"href": "%s", "status": "queued"}' % QUERY.encode()),
                                 response(200, b'{"status": "completed"}'), response(200, b"[]"), response(204, b"")]
        self.assertEqual(self.client.traffic_flows(30, ("blocked",)), ([], ""))
        self.assertEqual([m for m, _ in self.methods()], ["POST", "GET", "GET", "DELETE"])
        self.assertEqual(self.methods()[-1], ("DELETE", QUERY))
        body = json.loads(self.sent.call_args_list[0].args[0].body)
        self.assertLessEqual(set(body), pce.ReadOnlySession.QUERY_FIELDS)

    def test_draft_decisions_only_for_own_query(self):
        flow = b'[{"policy_decision": "blocked", "draft_policy_decision": "allowed"}]'
        self.sent.side_effect = [response(202, b'{"href": "%s", "status": "queued"}' % QUERY.encode()),
                                 response(200, b'{"status": "completed"}'), response(202, b""),
                                 response(200, b'{"status": "completed", "rules": {"status": "completed"}}'),
                                 response(200, flow), response(204, b"")]
        with mock.patch("time.sleep"):
            flows, error = self.client.traffic_flows(30, ("blocked",), draft=True)
        self.assertEqual((flows[0]["draft_policy_decision"], error), ("allowed", ""))
        self.assertEqual(self.methods()[2], ("PUT", QUERY + "/update_rules"))
        self.assertEqual([m for m, _ in self.methods()], ["POST", "GET", "PUT", "GET", "GET", "DELETE"])


class OnlyPceModuleTalksToThePceTest(unittest.TestCase):
    """No other module may send HTTP requests itself, around ReadOnlySession."""

    SENDS = re.compile(r"\b(requests\.(get|post|put|patch|delete|head|options|request|Session|session)\b|urllib\.request|http\.client|urlopen)")

    def test_no_direct_http_outside_pce_module(self):
        offenders = []
        for name in sorted(os.listdir(ROOT)):
            if name.endswith(".py") and name != "pce.py":
                with open(os.path.join(ROOT, name)) as fh:
                    offenders += [f"{name}:{n}: {line.strip()}" for n, line in enumerate(fh, 1) if self.SENDS.search(line)]
        self.assertEqual(offenders, [], "send PCE requests through pce.PCE only")


if __name__ == "__main__":
    unittest.main()
