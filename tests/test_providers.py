"""Offline tests for the provider registry and verification logic.

Nothing here touches the network: ``providers._request`` is replaced with a
stub so every HTTP outcome -- including the awkward ones -- is reproducible.
"""

from __future__ import annotations

import re

import pytest

import providers as P


# --- Test doubles -----------------------------------------------------------

_MISSING = object()


class FakeResponse:
    """The slice of ``requests.Response`` the verifiers actually use."""

    def __init__(self, status_code=200, json_body=_MISSING, text="", headers=None):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is _MISSING:
            raise ValueError("no JSON body")
        return self._json


class Recorder:
    """Captures the arguments a verifier passes to the HTTP layer."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def patch_request(monkeypatch):
    """Install a Recorder in place of the real HTTP call."""

    def install(response):
        recorder = Recorder(response)
        monkeypatch.setattr(P, "_request", recorder)
        return recorder

    return install


# --- Key-format detection ---------------------------------------------------

# (key, provider that must be detected) for unambiguous, specific signatures.
SPECIFIC_KEYS = [
    ("gsk_" + "a" * 40, "Groq"),
    ("hf_" + "b" * 40, "Hugging Face"),
    ("ghp_" + "c" * 36, "GitHub"),
    ("github_pat_" + "d" * 40, "GitHub"),
    ("glpat-" + "e" * 24, "GitLab"),
    ("sk-ant-api03-" + "f" * 40, "Anthropic"),
    ("sk-or-v1-" + "0" * 64, "OpenRouter"),
    ("AIza" + "g" * 35, "Google Gemini"),
    ("sk_live_" + "h" * 24, "Stripe"),
    ("xoxb-" + "1" * 12 + "-abcdef", "Slack"),
    ("r8_" + "i" * 37, "Replicate"),
    ("re_" + "j" * 24, "Resend"),
    ("SG." + "k" * 22 + "." + "l" * 43, "SendGrid"),
    ("ntn_" + "m" * 46, "Notion"),
    ("lin_api_" + "n" * 40, "Linear"),
    ("figd_" + "o" * 40, "Figma"),
    ("npm_" + "p" * 36, "npm"),
    ("fw_" + "q" * 24, "Fireworks AI"),
    ("pplx-" + "r" * 40, "Perplexity"),
    ("AKIAIOSFODNN7EXAMPLE", "AWS Access Key ID"),
    ("123456789:" + "s" * 35, "Telegram Bot Token"),
    ("pat" + "t" * 14 + "." + "a" * 64, "Airtable"),
]


@pytest.mark.parametrize("key,expected", SPECIFIC_KEYS)
def test_specific_signatures_are_detected(key, expected):
    assert expected in P.detect_candidates(key)


@pytest.mark.parametrize("key,expected", SPECIFIC_KEYS)
def test_specific_signatures_beat_broad_ones(key, expected):
    """A specific match must sort ahead of any broad/shape-only candidate."""
    candidates = P.detect_candidates(key)
    assert not P.REGISTRY[candidates[0]].broad


def test_openai_pattern_excludes_sibling_prefixes():
    """``sk-ant-`` and ``sk-or-v1-`` keys must not be claimed by OpenAI."""
    assert "OpenAI" not in P.detect_candidates("sk-ant-api03-" + "x" * 40)
    assert "OpenAI" not in P.detect_candidates("sk-or-v1-" + "0" * 64)
    assert "OpenAI" in P.detect_candidates("sk-proj-" + "x" * 40)


def test_jwt_is_not_mistaken_for_a_discord_token():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0." + "z" * 43
    assert "Discord Bot Token" not in P.detect_candidates(jwt)


def test_unknown_value_matches_nothing():
    assert P.detect_candidates("hello world") == []
    assert P.detect_candidates("") == []


def test_broad_providers_are_flagged_as_broad():
    """Opaque shapes must never be a positive identification on their own."""
    for name in ("Roboflow", "Mistral", "Cohere", "Together AI", "Vercel", "Cloudflare"):
        assert P.REGISTRY[name].broad, f"{name} should be marked broad"


def test_validate_key_format_round_trip():
    ok, message = P.validate_key_format("Groq", "gsk_" + "a" * 40)
    assert ok and message == ""

    ok, message = P.validate_key_format("Groq", "not-a-groq-key")
    assert not ok and "Groq" in message


def test_validate_key_format_passes_unknown_providers_through():
    ok, message = P.validate_key_format("Nonexistent", "anything")
    assert ok and message == ""


def test_every_pattern_compiles():
    for name, entry in P.REGISTRY.items():
        assert entry.patterns, f"{name} has no patterns"
        for pattern in entry.patterns:
            re.compile(pattern)


# --- Verifier state mapping -------------------------------------------------

def test_success_maps_to_verified(patch_request):
    patch_request(FakeResponse(200, {"login": "octocat"}))
    verify = P.build_verifier("GitHub", "https://api.github.com/user",
                              P._detail("GitHub", "login"))
    result = verify("ghp_token")
    assert result.state == P.VERIFIED
    assert result.confidence == "High"
    assert "octocat" in result.detail
    assert result.http_code == 200
    assert result.elapsed_ms is not None


@pytest.mark.parametrize("code", [401, 403])
def test_rejection_maps_to_invalid(patch_request, code):
    patch_request(FakeResponse(code, {"message": "Bad credentials"}))
    verify = P.build_verifier("GitHub", "https://x", P._detail("GitHub", "login"))
    result = verify("bad")
    assert result.state == P.INVALID
    assert result.detail == "Bad credentials"


def test_throttling_maps_to_limited_not_invalid(patch_request):
    """429 means the key was good enough to be rate-limited."""
    patch_request(FakeResponse(429, {"message": "slow down"}))
    verify = P.build_verifier("Groq", "https://x", P._count("Groq"))
    result = verify("gsk_x")
    assert result.state == P.LIMITED


@pytest.mark.parametrize("code", [400, 404, 500, 503])
def test_unexpected_status_is_inconclusive_not_invalid(patch_request, code):
    patch_request(FakeResponse(code, text="boom"))
    verify = P.build_verifier("Groq", "https://x", P._count("Groq"))
    result = verify("gsk_x")
    assert result.state == P.UNAVAILABLE
    assert result.confidence == "Medium"


def test_configurable_invalid_codes(patch_request):
    """Gemini reports a bad key as 400, so 400 must count as a rejection there."""
    patch_request(FakeResponse(400, {"error": {"message": "API key not valid"}}))
    verify = P.build_verifier("Google Gemini", "https://x", P._count("Gemini"),
                              invalid_codes=(400, 401, 403))
    assert verify("AIza").state == P.INVALID


def test_detail_falls_back_when_body_is_not_json(patch_request):
    patch_request(FakeResponse(200))
    verify = P.build_verifier("Groq", "https://x", P._detail("Groq", "name"))
    result = verify("gsk_x")
    assert result.state == P.VERIFIED
    assert result.detail == "Authentication accepted by Groq"


def test_detail_survives_a_raising_detail_function(patch_request):
    patch_request(FakeResponse(200, {"a": 1}))

    def explode(_body):
        raise RuntimeError("bad detail function")

    verify = P.build_verifier("Groq", "https://x", explode)
    assert verify("gsk_x").state == P.VERIFIED


# --- body_ok: providers that report auth failure inside a 200 ---------------

def test_body_ok_false_is_a_rejection(patch_request):
    patch_request(FakeResponse(200, {"ok": False, "error": "invalid_auth"}))
    verify = P.build_verifier("Slack", "https://x", P._detail("Slack", "team"),
                              body_ok=lambda d: d.get("ok") is True)
    result = verify("xoxb-bad")
    assert result.state == P.INVALID
    assert "invalid_auth" in result.detail


def test_body_ok_true_is_accepted(patch_request):
    patch_request(FakeResponse(200, {"ok": True, "team": "Acme"}))
    verify = P.build_verifier("Slack", "https://x", P._detail("Slack", "team",
                                                              prefix="Workspace"),
                              body_ok=lambda d: d.get("ok") is True)
    result = verify("xoxb-good")
    assert result.state == P.VERIFIED
    assert "Acme" in result.detail


def test_body_ok_fails_open_when_it_raises(patch_request):
    """An unjudgeable body must never be reported as a rejection."""
    patch_request(FakeResponse(200, {"unexpected": "shape"}))

    def explode(_body):
        raise KeyError("missing")

    verify = P.build_verifier("Slack", "https://x", P._detail("Slack", "team"),
                              body_ok=explode)
    assert verify("xoxb-x").state == P.VERIFIED


# --- Auth transport modes ---------------------------------------------------

def test_bearer_mode_sets_authorization_header(patch_request):
    recorder = patch_request(FakeResponse(200, {}))
    P.build_verifier("X", "https://x", P._count("X"))("secret")
    assert recorder.last["headers"]["Authorization"] == "Bearer secret"


def test_custom_header_mode(patch_request):
    recorder = patch_request(FakeResponse(200, {}))
    P.build_verifier("Anthropic", "https://x", P._count("Anthropic"),
                     mode="header", header_name="x-api-key", header_fmt="{key}",
                     extra_headers={"anthropic-version": "2023-06-01"})("secret")
    headers = recorder.last["headers"]
    assert headers["x-api-key"] == "secret"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers


def test_query_mode_sends_the_key_as_a_parameter(patch_request):
    recorder = patch_request(FakeResponse(200, {}))
    P.build_verifier("Gemini", "https://x", P._count("Gemini"),
                     mode="query", query_param="key")("secret")
    assert recorder.last["params"] == {"key": "secret"}


def test_basic_mode_uses_the_key_as_the_username(patch_request):
    recorder = patch_request(FakeResponse(200, {}))
    P.build_verifier("Stripe", "https://x", P._count("Stripe"), mode="basic")("sk_live")
    assert recorder.last["auth"] == ("sk_live", "")


def test_path_mode_interpolates_the_key_into_the_url(patch_request):
    recorder = patch_request(FakeResponse(200, {"ok": True}))
    P.build_verifier("Telegram", "https://api.telegram.org/bot{key}/getMe",
                     P._count("Telegram"), mode="path")("123:abc")
    assert recorder.last["url"] == "https://api.telegram.org/bot123:abc/getMe"
    assert recorder.last["headers"] is None


def test_post_mode_sends_the_json_body(patch_request):
    recorder = patch_request(FakeResponse(200, {"data": {"viewer": {"name": "Ada"}}}))
    verify = P.build_verifier("Linear", "https://x",
                              P._detail("Linear", "data.viewer.name"),
                              method="POST", mode="header", header_fmt="{key}",
                              json_body={"query": "{ viewer { name } }"},
                              body_ok=lambda d: bool((d.get("data") or {}).get("viewer")))
    result = verify("lin_api_x")
    assert recorder.last["method"] == "POST"
    assert recorder.last["json"] == {"query": "{ viewer { name } }"}
    assert result.state == P.VERIFIED
    assert "Ada" in result.detail


def test_unknown_mode_is_rejected_loudly(patch_request):
    patch_request(FakeResponse(200, {}))
    verify = P.build_verifier("X", "https://x", P._count("X"), mode="telepathy")
    with pytest.raises(ValueError):
        verify("key")


# --- Rate-limit headers -----------------------------------------------------

def test_rate_limit_header_is_surfaced(patch_request):
    patch_request(FakeResponse(200, {}, headers={"x-ratelimit-remaining": "4999"}))
    result = P.build_verifier("X", "https://x", P._count("X"))("key")
    assert result.rate_remaining == "4999"


def test_missing_rate_limit_header_is_none(patch_request):
    patch_request(FakeResponse(200, {}))
    assert P.build_verifier("X", "https://x", P._count("X"))("key").rate_remaining is None


# --- Error extraction -------------------------------------------------------

@pytest.mark.parametrize("body,expected", [
    ({"error": "flat string"}, "flat string"),
    ({"message": "a message"}, "a message"),
    ({"detail": "a detail"}, "a detail"),
    ({"description": "Unauthorized"}, "Unauthorized"),
    ({"error": {"message": "nested message"}}, "nested message"),
    ({"errors": [{"message": "Invalid API Token"}]}, "Invalid API Token"),
])
def test_safe_error_reads_common_shapes(body, expected):
    assert P.safe_error(response=FakeResponse(401, body)) == expected


def test_safe_error_falls_back_to_text_then_status():
    assert P.safe_error(response=FakeResponse(500, text="upstream down")) == "upstream down"
    assert P.safe_error(response=FakeResponse(503)) == "HTTP 503"


def test_safe_error_describes_exceptions():
    message = P.safe_error(exc=ValueError("no route to host"))
    assert "ValueError" in message and "no route to host" in message


def test_safe_error_truncates_long_bodies():
    assert len(P.safe_error(response=FakeResponse(400, {"error": "x" * 5000}))) <= 400


# --- Detail helpers ---------------------------------------------------------

def test_detail_reads_the_first_populated_path():
    detail = P._detail("X", "a.b", "c")
    assert detail({"a": {"b": "deep"}}) == "Authenticated as: deep"
    assert detail({"c": "shallow"}) == "Authenticated as: shallow"
    assert detail({"unrelated": 1}) == "Authentication accepted by X"
    assert detail("not a dict") == "Authentication accepted by X"


def test_count_handles_wrapped_and_bare_lists():
    assert "3 models" in P._count("X")({"data": [1, 2, 3]})
    assert "2 domains" in P._count("X", noun="domains")({"data": [1, 2]})
    assert P._count("X")({"nope": 1}) == "Authentication accepted by X"


# --- Format-only providers --------------------------------------------------

def test_format_only_providers_never_claim_to_be_live():
    for name in ("AWS Access Key ID", "Perplexity", "Roboflow Publishable Key"):
        entry = P.REGISTRY[name]
        assert not entry.verifiable, f"{name} must not be live-verifiable"
        assert entry.note, f"{name} must explain why it cannot be verified"
        assert entry.verifier("anything").state == P.FORMAT_ONLY


# --- Registry integrity -----------------------------------------------------

def test_registry_entries_are_well_formed():
    for name, entry in P.REGISTRY.items():
        assert entry.name == name
        assert entry.category in P.CATEGORY_ORDER, f"{name} has an unknown category"
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", entry.accent), f"{name} accent invalid"
        assert 1 <= len(entry.mark) <= 3, f"{name} mark should be 1-3 characters"


def test_verifiable_providers_document_their_probe():
    for name in P.VERIFIABLE_PROVIDERS:
        assert P.REGISTRY[name].endpoint, f"{name} must document its probe endpoint"


def test_featured_providers_are_all_verifiable():
    assert P.FEATURED_PROVIDERS
    for name in P.FEATURED_PROVIDERS:
        assert name in P.VERIFIABLE_PROVIDERS


def test_by_category_covers_every_provider():
    grouped = P.by_category()
    assert sum(len(names) for names in grouped.values()) == P.PROVIDER_COUNT
    assert list(grouped) == [c for c in P.CATEGORY_ORDER if c in grouped]
