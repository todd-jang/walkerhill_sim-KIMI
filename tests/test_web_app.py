import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from wise_sim.web_app import (
    LEDGER_KEYS,
    PilotDashboardHandler,
    SESSION_LOCK,
    SESSION_RESULTS,
    analyze_bundle,
)


class PilotDashboardWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), PilotDashboardHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    def setUp(self):
        with SESSION_LOCK:
            SESSION_RESULTS.clear()

    @staticmethod
    def bundle():
        assignments = []
        for index, arm in enumerate(("control", "treatment")):
            assignments.append({
                "experiment_id": "test-pilot-v1",
                "design": "randomized",
                "data_origin": "randomized_experiment",
                "assignment_id": f"a{index}",
                "unit_id": f"u{index}",
                "segment": "family",
                "assigned_variant": arm,
                "assignment_probability": 0.5,
                "assigned_at": "2026-01-01T10:00:00+00:00",
                "outcome_matures_at": "2026-02-01T00:00:00+00:00",
                "randomization_block_id": None,
                "protocol_id": "protocol-v1",
                "eligibility_version": "eligibility-v1",
                "assignment_logic_version": "assignment-v1",
            })
        return {
            "experiment": {
                "experiment_id": "test-pilot-v1",
                "analysis_cutoff_at": "2026-02-02T00:00:00+00:00",
                "source_complete_through": "2026-02-02T00:00:00+00:00",
                "attribution_window_days": 7,
                "data_version": "test-export-v1",
            },
            "assignments": assignments,
            **{name: [] for name in LEDGER_KEYS if name != "assignments"},
        }

    def request(self, path, *, method="GET", body=None, cookie=None, headers=None):
        request_headers = dict(headers or {})
        if cookie:
            request_headers["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            return error.code, error.read(), error.headers
        return response.status, response.read(), response.headers

    def test_analysis_computes_assignment_centered_dashboard_summary(self):
        result = analyze_bundle(self.bundle())
        self.assertEqual(result["quality"]["assignment_count"], 2)
        self.assertEqual(result["segments"]["family"]["assigned_per_arm"], {
            "control": 1,
            "treatment": 1,
        })
        self.assertFalse(result["experiment"]["synthetic_only"])
        self.assertTrue(result["segments"]["family"]["metrics"]["paid_booking"]["available"])
        self.assertEqual(len(result["facts"]), 2)

    def test_local_session_retains_analysis_and_clear_removes_it(self):
        status, _, headers = self.request("/")
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        status, body, _ = self.request(
            "/api/analyze", method="POST", body=self.bundle(), cookie=cookie
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["result"]["quality"]["assignment_count"], 2)

        status, body, _ = self.request("/api/state", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"]["experiment"]["data_version"], "test-export-v1")

        self.request("/api/clear", method="POST", body={}, cookie=cookie)
        status, body, _ = self.request("/api/state", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["result"])

    def test_sessions_are_isolated_and_cross_origin_posts_are_rejected(self):
        _, _, first_headers = self.request("/")
        _, _, second_headers = self.request("/")
        first_cookie = first_headers["Set-Cookie"].split(";", 1)[0]
        second_cookie = second_headers["Set-Cookie"].split(";", 1)[0]
        self.request("/api/analyze", method="POST", body=self.bundle(), cookie=first_cookie)

        status, body, _ = self.request("/api/state", cookie=second_cookie)
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["result"])

        status, body, _ = self.request(
            "/api/clear",
            method="POST",
            body={},
            cookie=first_cookie,
            headers={"Origin": "http://attacker.invalid"},
        )
        self.assertEqual(status, 403)
        self.assertIn(b"cross-origin", body)

    def test_missing_ledger_arrays_are_reported(self):
        with self.assertRaisesRegex(ValueError, "all ledger arrays are required"):
            analyze_bundle({
                "experiment": {"experiment_id": "test-pilot-v1"},
                "assignments": [],
            })

    def test_experiment_metadata_cannot_disagree_with_assignment_records(self):
        bundle = self.bundle()
        bundle["experiment"]["experiment_id"] = "different-pilot"
        with self.assertRaisesRegex(ValueError, "must match every assignment"):
            analyze_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
