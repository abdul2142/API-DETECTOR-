"""Integration tests that drive the real Streamlit app headlessly.

These run the actual script through ``AppTest``, so they cover the wiring that
unit tests cannot: session state, the verification queue, the worker threads and
the rendered markup. The HTTP layer is stubbed, so no network access occurs.

Two regressions are pinned here deliberately:

* keys must be resolved on the main thread before dispatch -- ``st.session_state``
  is not readable from the pool threads that run the probes;
* the success burst must fire only when every conclusive check passed, and only
  once.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import providers as P

APP = str(Path(__file__).resolve().parent.parent / "app.py")

VALID_GROQ = "gsk_" + "a" * 44
BURST = '<div class="ks-burst"'


class StubResponse:
    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.text = ""
        self.headers = headers or {}

    def json(self):
        return self._body


@pytest.fixture
def stub_http(monkeypatch):
    """Replace the HTTP layer; returns a setter for the canned response."""

    def set_response(status_code, body, headers=None):
        monkeypatch.setattr(
            P, "_request",
            lambda method, url, **kwargs: StubResponse(status_code, body, headers))

    return set_response


def launch():
    return AppTest.from_file(APP, default_timeout=120).run()


def markup(app) -> str:
    return " ".join(block.value for block in app.markdown)


def verify_groq(app, key=VALID_GROQ):
    app.text_input(key="k_Groq").set_value(key).run()
    app.button(key="go_Groq").click().run()
    return app


# --- Smoke ------------------------------------------------------------------

def test_app_renders_without_exceptions():
    app = launch()
    assert not app.exception
    assert len(app.tabs) == 4


def test_default_selection_is_rendered_in_the_sidebar():
    app = launch()
    labels = {widget.label for widget in app.text_input}
    assert set(P.FEATURED_PROVIDERS).issubset(labels)


# --- Verification queue -----------------------------------------------------

def test_bad_format_short_circuits_before_any_network_call(monkeypatch):
    """A format mismatch must be decided locally, with no request issued."""
    calls = []
    monkeypatch.setattr(P, "_request",
                        lambda *a, **k: calls.append(a) or pytest.fail("network used"))

    app = verify_groq(launch(), "definitely-not-a-groq-key")
    assert not app.exception
    assert [row["Status"] for row in app.session_state["results"]] == ["Invalid format"]
    assert calls == []


def test_keys_reach_the_worker_threads(stub_http):
    """Regression: session state is unreadable from the probe pool threads, so
    keys must be resolved on the main thread before dispatch."""
    stub_http(200, {"data": [1, 2]})
    app = verify_groq(launch())

    assert not app.exception
    row = app.session_state["results"][0]
    assert row["Status"] == "Live", "an empty key would have yielded 'Not checked'"
    assert row["HTTP Code"] == 200
    assert "2 models" in row["Account / Detail"]
    assert isinstance(row["Response Time (ms)"], float)


def test_rejection_is_reported_with_the_provider_message(stub_http):
    stub_http(401, {"message": "Invalid API Key"})
    app = verify_groq(launch())

    row = app.session_state["results"][0]
    assert row["Status"] == "Rejected"
    assert row["Account / Detail"] == "Invalid API Key"
    assert "ks-pill-bad" in markup(app)


def test_throttling_is_not_reported_as_a_failure(stub_http):
    stub_http(429, {"message": "slow down"})
    app = verify_groq(launch())
    assert app.session_state["results"][0]["Status"] == "Rate limited"


def test_results_can_be_cleared(stub_http):
    stub_http(200, {"data": [1]})
    app = verify_groq(launch())
    assert app.session_state["results"]

    next(button for button in app.sidebar.button
         if "Clear" in str(button.label)).click().run()
    assert app.session_state["results"] == []
    assert not app.exception


# --- Summary rendering ------------------------------------------------------

def test_live_run_renders_the_full_summary(stub_http):
    stub_http(200, {"data": [1, 2]})
    app = verify_groq(launch())
    rendered = markup(app)

    for fragment in ("ks-gauge", "ks-metric", "ks-table", "ks-bar-fill"):
        assert fragment in rendered, f"{fragment} missing from the dashboard"


def test_export_frame_is_arrow_serialisable(stub_http):
    """Mixed ''/float columns break Arrow, so numerics must be coerced."""
    stub_http(200, {"data": [1]})
    app = verify_groq(launch())

    frame = app.dataframe[0].value
    assert pd.api.types.is_numeric_dtype(frame["Response Time (ms)"])
    assert pd.api.types.is_integer_dtype(frame["HTTP Code"])


# --- Success burst ----------------------------------------------------------

def test_burst_fires_when_every_conclusive_check_passed(stub_http):
    stub_http(200, {"data": [1]})
    assert BURST in markup(verify_groq(launch()))


def test_burst_is_suppressed_when_a_check_failed(stub_http):
    stub_http(401, {"message": "nope"})
    assert BURST not in markup(verify_groq(launch()))


def test_burst_is_one_shot(stub_http):
    """It must celebrate the run, not every later interaction."""
    stub_http(200, {"data": [1]})
    app = verify_groq(launch())
    assert BURST in markup(app)

    app.multiselect(key="selected_providers").set_value(["Groq"]).run()
    assert BURST not in markup(app)
    assert len(app.session_state["results"]) == 1


# --- Detector ---------------------------------------------------------------

def _analyse(app, value):
    next(widget for widget in app.text_input
         if widget.label == "API key or token").set_value(value).run()
    next(button for button in app.button
         if "Analyse" in str(button.label)).click().run()
    return app.session_state["detector_report"]


def test_detector_reports_unknown_values_honestly(monkeypatch):
    monkeypatch.setattr(P, "_request",
                        lambda *a, **k: pytest.fail("no probe should be issued"))
    report = _analyse(launch(), "this is not a key at all")

    assert report["detected"] == "Unknown or unsupported"
    assert report["confidence"] == "Low"
    assert report["probes"] == []


def test_detector_verifies_a_recognised_key(stub_http):
    stub_http(200, {"data": [1, 2, 3]})
    report = _analyse(launch(), VALID_GROQ)

    assert report["detected"] == "Groq"
    assert report["status"] == "Verified live"
    assert report["confidence"] == "High"
    assert [row[1] for row in report["probes"]] == ["Live"]


def test_detector_never_claims_a_rejected_broad_key(stub_http):
    """An opaque shape rejected by its probe must not be pinned to a provider."""
    stub_http(401, {"message": "nope"})
    report = _analyse(launch(), "A" * 24)

    assert report["detected"] == "Unknown or unsupported"
    assert report["confidence"] == "Low"


def test_detector_reports_format_only_credentials(monkeypatch):
    monkeypatch.setattr(P, "_request",
                        lambda *a, **k: pytest.fail("no probe should be issued"))
    report = _analyse(launch(), "AKIAIOSFODNN7EXAMPLE")

    assert report["detected"] == "AWS Access Key ID"
    assert report["status"] == "Format only"
    assert "secret access key" in report["detail"]
