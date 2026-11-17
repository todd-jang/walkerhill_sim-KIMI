"""Local, stateful web dashboard for assignment-centered pilot analysis."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from wise_sim.experiment_pipeline import (
    build_experiment_unit_facts,
    estimate_fact_itt,
    summarize_operations_per_stay,
    summarize_survey_responders,
)
from wise_sim.web_views import (
    PanelNotFound,
    ViewDataError,
    compute_verdict,
    load_hotel_pricing,
    load_price_panel,
)


STATIC_DIR = Path(__file__).resolve().parent / "static" / "pilot_dashboard"
MAX_REQUEST_BYTES = 20 * 1024 * 1024
MAX_ASSIGNMENTS = 50_000
SESSION_TTL_SECONDS = 4 * 60 * 60
SESSION_RESULTS: dict[str, dict[str, Any]] = {}
SESSION_LOCK = threading.Lock()
STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
LEDGER_KEYS = (
    "assignments",
    "exposures",
    "bookings",
    "payments",
    "cancellations",
    "refunds",
    "costs",
    "stays",
    "operations",
    "surveys",
)
METRICS = (
    "paid_booking",
    "net_booking",
    "fully_cancelled",
    "booking_count",
    "gross_payment",
    "refund_amount",
    "net_revenue",
    "variable_cost",
    "contribution_margin",
    "complaint_count",
    "safety_incident_count",
    "survey_responded",
)


def analyze_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a ledger bundle, build assignment facts, and calculate summaries."""
    experiment = bundle.get("experiment")
    if not isinstance(experiment, Mapping):
        raise ValueError("experiment must be an object with analysis settings")
    experiment_id = experiment.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise ValueError("experiment.experiment_id is required")
    missing = [key for key in LEDGER_KEYS if not isinstance(bundle.get(key), list)]
    if missing:
        raise ValueError(f"all ledger arrays are required: {', '.join(missing)}")
    if len(bundle["assignments"]) > MAX_ASSIGNMENTS:
        raise ValueError(f"assignments cannot exceed {MAX_ASSIGNMENTS}")

    joined = build_experiment_unit_facts(
        bundle["assignments"],
        exposures=bundle["exposures"],
        bookings=bundle["bookings"],
        payments=bundle["payments"],
        cancellations=bundle["cancellations"],
        refunds=bundle["refunds"],
        costs=bundle["costs"],
        stays=bundle["stays"],
        operations=bundle["operations"],
        surveys=bundle["surveys"],
        analysis_cutoff_at=experiment.get("analysis_cutoff_at"),
        source_complete_through=experiment.get("source_complete_through"),
        attribution_window_days=experiment.get("attribution_window_days"),
        data_version=experiment.get("data_version"),
    )
    facts = joined["facts"]
    if {fact["experiment_id"] for fact in facts} != {experiment_id}:
        raise ValueError("experiment.experiment_id must match every assignment")
    segments = sorted({fact["segment"] for fact in facts})
    segment_results: dict[str, Any] = {}
    for segment in segments:
        segment_facts = [fact for fact in facts if fact["segment"] == segment]
        metrics: dict[str, Any] = {}
        for metric in METRICS:
            try:
                metrics[metric] = {
                    "available": True,
                    **estimate_fact_itt(facts, metric, segment=segment),
                }
            except ValueError as error:
                metrics[metric] = {"available": False, "reason": str(error)}
        segment_results[segment] = {
            "assignment_count": len(segment_facts),
            "assigned_per_arm": {
                arm: sum(
                    fact["assigned_variant"] == arm for fact in segment_facts
                )
                for arm in ("control", "treatment")
            },
            "exposed_per_arm": {
                arm: sum(
                    fact["exposed_flag"]
                    and fact["assigned_variant"] == arm
                    for fact in segment_facts
                )
                for arm in ("control", "treatment")
            },
            "exposure_mismatch_count": sum(
                fact["exposure_variant_mismatch_count"] for fact in segment_facts
            ),
            "metrics": metrics,
            "survey": summarize_survey_responders(facts, segment=segment),
            "operations_per_stay": summarize_operations_per_stay(
                facts, segment=segment
            ),
            "safety_incidents_by_severity": {
                severity: sum(
                    fact["safety_incidents_by_severity"].get(severity, 0)
                    for fact in segment_facts
                )
                for severity in sorted({
                    severity
                    for fact in segment_facts
                    for severity in fact["safety_incidents_by_severity"]
                })
            },
        }

    return {
        "experiment": {
            "experiment_id": experiment.get("experiment_id"),
            "protocol_id": sorted({fact["protocol_id"] for fact in facts}),
            "eligibility_versions": sorted({
                fact["eligibility_version"] for fact in facts
            }),
            "assignment_logic_versions": sorted({
                fact["assignment_logic_version"] for fact in facts
            }),
            "analysis_cutoff_at": experiment.get("analysis_cutoff_at"),
            "source_complete_through": experiment.get("source_complete_through"),
            "attribution_window_days": experiment.get("attribution_window_days"),
            "data_version": experiment.get("data_version"),
            "synthetic_only": all(fact["synthetic_only"] for fact in facts),
            "mixed_data_origin": len({fact["data_origin"] for fact in facts}) > 1,
        },
        "segments": segment_results,
        "quality": joined["quality"],
        "facts": facts,
    }


class PilotDashboardHandler(BaseHTTPRequestHandler):
    server_version = "WISEPilotDashboard/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("WISE dashboard: " + fmt % args + "\n")

    def _session_id(self) -> tuple[str, bool]:
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookies.get("wise_pilot_session")
        now = time.monotonic()
        with SESSION_LOCK:
            for key, value in list(SESSION_RESULTS.items()):
                if now - value.get("last_access", now) > SESSION_TTL_SECONDS:
                    SESSION_RESULTS.pop(key, None)
            if morsel and morsel.value in SESSION_RESULTS:
                SESSION_RESULTS[morsel.value]["last_access"] = now
                return morsel.value, False
        return secrets.token_urlsafe(32), True

    def _send(
        self,
        status: int,
        body: bytes | str | Mapping[str, Any],
        content_type: str = "application/json; charset=utf-8",
        *,
        session_id: str | None = None,
        set_cookie: bool = False,
    ) -> None:
        if isinstance(body, Mapping):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if set_cookie and session_id:
            self.send_header(
                "Set-Cookie",
                f"wise_pilot_session={session_id}; HttpOnly; SameSite=Strict; Path=/",
            )
        self.end_headers()
        self.wfile.write(body)

    def _request_session(self) -> str:
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookies.get("wise_pilot_session")
        with SESSION_LOCK:
            if not morsel or morsel.value not in SESSION_RESULTS:
                raise ValueError("session missing or expired; reload the dashboard")
            session = SESSION_RESULTS[morsel.value]
            if time.monotonic() - session.get("last_access", 0) > SESSION_TTL_SECONDS:
                SESSION_RESULTS.pop(morsel.value, None)
                raise ValueError("session missing or expired; reload the dashboard")
            session["last_access"] = time.monotonic()
            return morsel.value

    def _check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def _read_json(self) -> Any:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body must be between 1 and {MAX_REQUEST_BYTES} bytes")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("request body is not valid UTF-8 JSON") from error

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            session_id, is_new = self._session_id()
            with SESSION_LOCK:
                SESSION_RESULTS.setdefault(
                    session_id, {"last_access": time.monotonic()}
                )
            try:
                page = (STATIC_DIR / "index.html").read_bytes()
            except OSError:
                self._send(500, {"error": "dashboard static files are unavailable"})
                return
            self._send(
                200,
                page,
                STATIC_CONTENT_TYPES[".html"],
                session_id=session_id,
                set_cookie=is_new,
            )
            return
        if path.startswith("/static/"):
            relative = path.removeprefix("/static/")
            if relative not in {"app.js", "style.css"}:
                self._send(404, {"error": "not found"})
                return
            static_path = STATIC_DIR / relative
            try:
                self._send(
                    200,
                    static_path.read_bytes(),
                    STATIC_CONTENT_TYPES[static_path.suffix],
                )
            except OSError:
                self._send(404, {"error": "not found"})
            return
        if path == "/api/state":
            try:
                session_id = self._request_session()
            except ValueError as error:
                self._send(401, {"error": str(error)})
                return
            with SESSION_LOCK:
                result = SESSION_RESULTS.get(session_id, {}).get("result")
            self._send(200, {"result": result})
            return
        if path == "/api/price-panel":
            try:
                self._request_session()
            except ValueError as error:
                self._send(401, {"error": str(error)})
                return
            try:
                payload = load_price_panel()
            except PanelNotFound as error:
                self._send(404, {"error": str(error)})
                return
            except ViewDataError as error:
                self._send(500, {"error": str(error)})
                return
            self._send(200, payload)
            return
        if path == "/api/hotel-pricing":
            try:
                self._request_session()
            except ValueError as error:
                self._send(401, {"error": str(error)})
                return
            try:
                payload = load_hotel_pricing()
            except PanelNotFound as error:
                self._send(404, {"error": str(error)})
                return
            except ViewDataError as error:
                self._send(500, {"error": str(error)})
                return
            self._send(200, payload)
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._check_origin():
            self._send(403, {"error": "cross-origin requests are not accepted"})
            return
        try:
            session_id = self._request_session()
        except ValueError as error:
            self._send(401, {"error": str(error)})
            return
        if path == "/api/analyze":
            try:
                bundle = self._read_json()
                if not isinstance(bundle, Mapping):
                    raise ValueError("request must be a JSON object")
                result = analyze_bundle(bundle)
            except (ValueError, TypeError, KeyError) as error:
                self._send(400, {"error": str(error)})
                return
            except Exception:
                self._send(500, {"error": "analysis failed; inspect the local server output"})
                raise
            with SESSION_LOCK:
                session = SESSION_RESULTS.setdefault(
                    session_id, {"last_access": time.monotonic()}
                )
                session["last_access"] = time.monotonic()
                session["result"] = result
            self._send(200, {"result": result})
            return
        if path == "/api/clear":
            with SESSION_LOCK:
                session = SESSION_RESULTS.setdefault(
                    session_id, {"last_access": time.monotonic()}
                )
                session["last_access"] = time.monotonic()
                session.pop("result", None)
            self._send(200, {"result": None})
            return
        if path == "/api/verdict":
            try:
                payload = self._read_json()
                result = compute_verdict(payload)
            except ViewDataError as error:
                self._send(400, {"error": str(error)})
                return
            except (ValueError, TypeError, KeyError) as error:
                self._send(400, {"error": str(error)})
                return
            self._send(200, {"result": result})
            return
        self._send(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self._send(405, {"error": "method not allowed"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local WISE pilot dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PilotDashboardHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"WISE pilot dashboard: {url} (stop with Ctrl+C)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
        with SESSION_LOCK:
            SESSION_RESULTS.clear()


if __name__ == "__main__":
    main()
