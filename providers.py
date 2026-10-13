"""Provider registry and credential verification for Keyscope.

A single source of truth for every supported provider. Each provider declares
its key-format patterns plus a verifier that performs a **read-only**
authenticated request against the provider's own API. The UI layer never talks
to a provider directly; it goes through this module.

Design principles
-----------------
* Detection (does the key *look* like provider X?) and verification (does the
  provider *accept* the key?) are kept strictly separate.
* A format match alone is never reported as an active key.
* Some credentials cannot be proven valid on their own -- an AWS access key id
  needs its secret, a Roboflow publishable key only authorises inference
  against one model -- so those are reported as "format only" with a reason.
* Every verifier fails *open*: when a response cannot be interpreted we report
  "unavailable", never "invalid". A false negative is worse than no answer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

import requests

# --- Verification states ----------------------------------------------------

VERIFIED = "verified"        # provider accepted the credential
INVALID = "invalid"          # provider rejected the credential
LIMITED = "limited"          # accepted but rate-limited / out of quota
UNAVAILABLE = "unavailable"  # no conclusion could be drawn (network / HTTP)
FORMAT_ONLY = "format_only"  # structure recognised, cannot be live-verified

DEFAULT_TIMEOUT = 12
MAX_RETRIES = 2            # transient-failure retries (total attempts = 1 + this)
RETRY_BACKOFF = 0.6        # seconds, multiplied by attempt number

USER_AGENT = "Keyscope/2.0 (+api-key-status-checker)"

# --- Categories -------------------------------------------------------------

AI = "AI & ML"
DEV = "Developer platforms"
CLOUD = "Cloud & infrastructure"
PAY = "Payments"
COMMS = "Communication"
DATA = "Productivity & data"

CATEGORY_ORDER = [AI, DEV, CLOUD, PAY, COMMS, DATA]


@dataclass
class VerifyResult:
    """Outcome of a single verification attempt."""

    detail: str
    confidence: str           # "High" | "Medium" | "Low"
    state: str                # one of the state constants above
    http_code: Optional[int] = None
    elapsed_ms: Optional[float] = None
    rate_remaining: Optional[str] = None


# --- HTTP plumbing ----------------------------------------------------------

def _request(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP wrapper with a consistent timeout and light retry on transient errors.

    Retries only connection/timeout errors and 5xx responses; auth responses
    (2xx/401/403/429) are returned immediately since a retry would not change
    them and would waste a provider request.
    """
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", USER_AGENT)
    kwargs["headers"] = headers

    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code >= 500 and attempt <= MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
                continue
            return response
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
                continue
            raise
    # Unreachable, but keeps type-checkers happy.
    raise last_exc  # type: ignore[misc]


def _json_or_none(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


_RATE_HEADERS = (
    "X-RateLimit-Remaining",
    "x-ratelimit-remaining",
    "RateLimit-Remaining",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "anthropic-ratelimit-requests-remaining",
)


def _rate_remaining(response: requests.Response) -> Optional[str]:
    """Return a rate-limit-remaining header value if the provider exposes one."""
    for header in _RATE_HEADERS:
        if header in response.headers:
            return response.headers[header]
    return None


_ERROR_KEYS = ("error", "message", "detail", "description",
               "error_description", "error_message", "msg")


def safe_error(response: Optional[requests.Response] = None,
               exc: Optional[Exception] = None) -> str:
    """Extract a human-readable, non-sensitive error message."""
    if exc:
        return f"Connection error: {type(exc).__name__}: {exc}"
    if response is None:
        return "Unknown error"

    body = _json_or_none(response)
    if isinstance(body, dict):
        for key in _ERROR_KEYS:
            msg: Any = body.get(key)
            if isinstance(msg, dict):
                msg = msg.get("message") or msg.get("detail") or msg.get("description")
            if isinstance(msg, list) and msg:
                head = msg[0]
                msg = head.get("message") if isinstance(head, dict) else head
            if msg:
                return str(msg)[:400]
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            head = errors[0]
            msg = head.get("message") if isinstance(head, dict) else head
            if msg:
                return str(msg)[:400]

    text = (response.text or "").strip()
    return text[:400] if text else f"HTTP {response.status_code}"


# --- Detail builders --------------------------------------------------------

def _detail(name: str, *paths: str, prefix: str = "Authenticated as") -> Callable[[Any], str]:
    """Build a success-detail function that reads the first populated dotted path."""

    def detail(body: Any) -> str:
        for path in paths:
            value: Any = body
            for part in path.split("."):
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return f"{prefix}: {value}"
        return f"Authentication accepted by {name}"

    return detail


def _count(name: str, key: str = "data", noun: str = "models") -> Callable[[Any], str]:
    """Build a success-detail function that counts a list in the response body."""

    def detail(body: Any) -> str:
        items = body.get(key) if isinstance(body, dict) else body
        if isinstance(items, list):
            return f"Authentication accepted — {len(items)} {noun} visible"
        return f"Authentication accepted by {name}"

    return detail


# --- Generic verifier factory ----------------------------------------------

def build_verifier(
    name: str,
    url: str,
    detail_fn: Callable[[Any], str],
    *,
    method: str = "GET",
    mode: str = "bearer",
    header_name: str = "Authorization",
    header_fmt: str = "Bearer {key}",
    query_param: str = "api_key",
    extra_headers: Optional[dict] = None,
    json_body: Optional[dict] = None,
    invalid_codes: Iterable[int] = (401, 403),
    body_ok: Optional[Callable[[dict], bool]] = None,
) -> Callable[[str], VerifyResult]:
    """Return a verifier closure for one provider.

    ``mode`` selects how the credential is presented:

    ``bearer``/``header``
        Sent as a request header built from ``header_fmt`` (the default is a
        standard ``Authorization: Bearer`` header).
    ``query``
        Sent as the ``query_param`` query-string parameter.
    ``basic``
        Sent as the HTTP basic-auth username with an empty password.
    ``path``
        Already interpolated into ``url`` via a ``{key}`` placeholder.

    ``body_ok`` handles providers that signal auth failure inside a 200
    response (Slack, Telegram, Cloudflare). It fails open: if the predicate
    raises, the credential is *not* reported invalid.
    """
    invalid = tuple(invalid_codes)

    def verify(key: str) -> VerifyResult:
        headers = dict(extra_headers or {})
        params: Optional[dict] = None
        auth: Optional[tuple] = None
        target = url.replace("{key}", key) if "{key}" in url else url

        if mode in ("bearer", "header"):
            headers[header_name] = header_fmt.format(key=key)
        elif mode == "query":
            params = {query_param: key}
        elif mode == "basic":
            auth = (key, "")
        elif mode != "path":
            raise ValueError(f"unknown verifier mode: {mode!r}")

        started = time.perf_counter()
        response = _request(method, target, headers=headers or None,
                            params=params, auth=auth, json=json_body)
        elapsed = round((time.perf_counter() - started) * 1000, 2)

        code = response.status_code
        remaining = _rate_remaining(response)

        if 200 <= code < 300:
            body = _json_or_none(response)
            if body_ok is not None and isinstance(body, dict):
                try:
                    accepted = bool(body_ok(body))
                except Exception:
                    accepted = True  # cannot judge -> never call it invalid
                if not accepted:
                    return VerifyResult(
                        f"{name} rejected the credential: {safe_error(response=response)}",
                        "High", INVALID, code, elapsed, remaining)
            try:
                detail = detail_fn(body)
            except Exception:
                detail = f"Authentication accepted by {name}"
            return VerifyResult(detail or f"Authentication accepted by {name}",
                                "High", VERIFIED, code, elapsed, remaining)

        if code in invalid:
            return VerifyResult(safe_error(response=response), "High", INVALID,
                                code, elapsed, remaining)
        if code == 429:
            return VerifyResult(f"{name} accepted the credential but rate-limited the request.",
                                "High", LIMITED, code, elapsed, remaining)
        return VerifyResult(f"{name} verification returned HTTP {code}.",
                            "Medium", UNAVAILABLE, code, elapsed, remaining)

    verify.__name__ = "verify_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return verify


def format_only(note: str) -> Callable[[str], VerifyResult]:
    """Build a verifier that reports a recognised structure and explains why
    live verification is impossible."""

    def verify(_key: str) -> VerifyResult:
        return VerifyResult(note, "Medium", FORMAT_ONLY)

    return verify


# --- Provider registry ------------------------------------------------------

@dataclass
class Provider:
    """Everything the app knows about one credential type."""

    name: str
    mark: str                 # 1-2 character glyph for the avatar
    category: str
    accent: str               # brand-ish hex accent used by the UI
    patterns: list[str]
    verifier: Optional[Callable[[str], VerifyResult]] = None
    placeholder: str = "…"
    endpoint: str = ""        # human-readable description of the probe
    docs: str = ""            # where to create/manage the credential
    env: str = ""             # environment variable checked for prefill
    featured: bool = False    # pre-selected on first load
    # Broad/ambiguous patterns are probe candidates only, never a positive
    # identification on format alone.
    broad: bool = False
    note: str = ""            # why it cannot be live-verified, when applicable
    aliases: list[str] = field(default_factory=list)

    @property
    def verifiable(self) -> bool:
        """True when a live request can prove or disprove this credential."""
        return self.verifier is not None and not self.note


REGISTRY: dict[str, Provider] = {
    # --- AI & ML ------------------------------------------------------------
    "OpenAI": Provider(
        "OpenAI", "OA", AI, "#10a37f",
        [r"^sk-(?!ant-|or-v1-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}$"],
        build_verifier("OpenAI", "https://api.openai.com/v1/models",
                       _count("OpenAI")),
        placeholder="sk-…", endpoint="GET api.openai.com/v1/models",
        docs="https://platform.openai.com/api-keys", env="OPENAI_API_KEY"),
    "Anthropic": Provider(
        "Anthropic", "AN", AI, "#d97757",
        [r"^sk-ant-[A-Za-z0-9_-]{20,}$"],
        build_verifier("Anthropic", "https://api.anthropic.com/v1/models",
                       _count("Anthropic"), mode="header", header_name="x-api-key",
                       header_fmt="{key}",
                       extra_headers={"anthropic-version": "2023-06-01"}),
        placeholder="sk-ant-…", endpoint="GET api.anthropic.com/v1/models",
        docs="https://console.anthropic.com/settings/keys", env="ANTHROPIC_API_KEY"),
    "Google Gemini": Provider(
        "Google Gemini", "GM", AI, "#4285f4",
        [r"^AIza[A-Za-z0-9_-]{30,}$"],
        build_verifier("Google Gemini",
                       "https://generativelanguage.googleapis.com/v1beta/models",
                       _count("Google Gemini", key="models"), mode="query",
                       query_param="key", invalid_codes=(400, 401, 403)),
        placeholder="AIza…",
        endpoint="GET generativelanguage.googleapis.com/v1beta/models",
        docs="https://aistudio.google.com/apikey", env="GEMINI_API_KEY"),
    "Groq": Provider(
        "Groq", "GQ", AI, "#f55036",
        [r"^gsk_[A-Za-z0-9_-]{20,}$"],
        build_verifier("Groq", "https://api.groq.com/openai/v1/models",
                       _count("Groq")),
        placeholder="gsk_…", endpoint="GET api.groq.com/openai/v1/models",
        docs="https://console.groq.com/keys", env="GROQ_API_KEY", featured=True),
    "Hugging Face": Provider(
        "Hugging Face", "HF", AI, "#f59e0b",
        [r"^hf_[A-Za-z0-9_-]{20,}$"],
        build_verifier("Hugging Face", "https://huggingface.co/api/whoami-v2",
                       _detail("Hugging Face", "name", "fullname", "email")),
        placeholder="hf_…", endpoint="GET huggingface.co/api/whoami-v2",
        docs="https://huggingface.co/settings/tokens", env="HF_TOKEN", featured=True),
    "Mistral": Provider(
        "Mistral", "MI", AI, "#fa520f",
        [r"^[A-Za-z0-9]{32}$"],
        build_verifier("Mistral", "https://api.mistral.ai/v1/models",
                       _count("Mistral")),
        placeholder="32 characters", endpoint="GET api.mistral.ai/v1/models",
        docs="https://console.mistral.ai/api-keys", env="MISTRAL_API_KEY", broad=True),
    "Cohere": Provider(
        "Cohere", "CO", AI, "#ff7759",
        [r"^[A-Za-z0-9]{40}$"],
        build_verifier("Cohere", "https://api.cohere.com/v1/me",
                       _detail("Cohere", "email", "name")),
        placeholder="40 characters", endpoint="GET api.cohere.com/v1/me",
        docs="https://dashboard.cohere.com/api-keys", env="COHERE_API_KEY", broad=True),
    "Together AI": Provider(
        "Together AI", "TA", AI, "#0f6fff",
        [r"^[a-f0-9]{64}$"],
        build_verifier("Together AI", "https://api.together.xyz/v1/models",
                       _count("Together AI")),
        placeholder="64 hex characters", endpoint="GET api.together.xyz/v1/models",
        docs="https://api.together.ai/settings/api-keys",
        env="TOGETHER_API_KEY", broad=True),
    "OpenRouter": Provider(
        "OpenRouter", "OR", AI, "#6566f1",
        [r"^sk-or-v1-[A-Za-z0-9]{32,}$"],
        build_verifier("OpenRouter", "https://openrouter.ai/api/v1/key",
                       _detail("OpenRouter", "data.label", "data.limit",
                               prefix="Key accepted")),
        placeholder="sk-or-v1-…", endpoint="GET openrouter.ai/api/v1/key",
        docs="https://openrouter.ai/settings/keys", env="OPENROUTER_API_KEY"),
    "DeepSeek": Provider(
        "DeepSeek", "DS", AI, "#4d6bfe",
        [r"^sk-[a-f0-9]{32}$"],
        build_verifier("DeepSeek", "https://api.deepseek.com/user/balance",
                       _detail("DeepSeek", "is_available", prefix="Account reachable")),
        placeholder="sk-…", endpoint="GET api.deepseek.com/user/balance",
        docs="https://platform.deepseek.com/api_keys", env="DEEPSEEK_API_KEY"),
    "Fireworks AI": Provider(
        "Fireworks AI", "FW", AI, "#8b5cf6",
        [r"^fw_[A-Za-z0-9]{20,}$"],
        build_verifier("Fireworks AI",
                       "https://api.fireworks.ai/inference/v1/models",
                       _count("Fireworks AI")),
        placeholder="fw_…",
        endpoint="GET api.fireworks.ai/inference/v1/models",
        docs="https://fireworks.ai/account/api-keys", env="FIREWORKS_API_KEY"),
    "Stability AI": Provider(
        "Stability AI", "SA", AI, "#7c3aed",
        [r"^sk-[A-Za-z0-9]{40,}$"],
        build_verifier("Stability AI", "https://api.stability.ai/v1/user/account",
                       _detail("Stability AI", "email", "id")),
        placeholder="sk-…", endpoint="GET api.stability.ai/v1/user/account",
        docs="https://platform.stability.ai/account/keys",
        env="STABILITY_API_KEY"),
    "ElevenLabs": Provider(
        "ElevenLabs", "11", AI, "#94a3b8",
        [r"^sk_[a-f0-9]{40,}$"],
        build_verifier("ElevenLabs", "https://api.elevenlabs.io/v1/user",
                       _detail("ElevenLabs", "subscription.tier", "xi_api_key",
                               prefix="Account tier"),
                       mode="header", header_name="xi-api-key", header_fmt="{key}"),
        placeholder="sk_…", endpoint="GET api.elevenlabs.io/v1/user",
        docs="https://elevenlabs.io/app/settings/api-keys",
        env="ELEVENLABS_API_KEY"),
    "Replicate": Provider(
        "Replicate", "R8", AI, "#ea2805",
        [r"^r8_[A-Za-z0-9]{20,}$"],
        build_verifier("Replicate", "https://api.replicate.com/v1/account",
                       _detail("Replicate", "username", "name")),
        placeholder="r8_…", endpoint="GET api.replicate.com/v1/account",
        docs="https://replicate.com/account/api-tokens",
        env="REPLICATE_API_TOKEN"),
    "Roboflow": Provider(
        "Roboflow", "RF", AI, "#a351fb",
        [r"^[A-Za-z0-9]{20,}$"],
        build_verifier("Roboflow", "https://api.roboflow.com/",
                       _detail("Roboflow", "workspace", "workspaceId",
                               "workspace_id", "name", prefix="Active workspace"),
                       mode="query"),
        placeholder="opaque key", endpoint="GET api.roboflow.com/?api_key=…",
        docs="https://app.roboflow.com/settings/api",
        env="ROBOFLOW_API_KEY", featured=True, broad=True),
    "Perplexity": Provider(
        "Perplexity", "PX", AI, "#20808d",
        [r"^pplx-[A-Za-z0-9]{32,}$"],
        format_only("Perplexity publishes no read-only authenticated endpoint; "
                    "proving this key would require a billable completion request."),
        placeholder="pplx-…", endpoint="none (no read-only endpoint)",
        docs="https://www.perplexity.ai/settings/api",
        note="No read-only endpoint exists, so this key is identified by format only."),
    "Roboflow Publishable Key": Provider(
        "Roboflow Publishable Key", "RP", AI, "#c084fc",
        [r"^rf_[A-Za-z0-9_-]{3,}$"],
        format_only("Roboflow publishable-key structure recognised. These keys only "
                    "authorise inference against a specific model, so activity cannot "
                    "be proven from the key alone."),
        placeholder="rf_…", endpoint="none (model-scoped credential)",
        docs="https://app.roboflow.com/settings/api",
        note="Publishable keys are model-scoped, so they cannot be verified generically."),

    # --- Developer platforms ----------------------------------------------
    "GitHub": Provider(
        "GitHub", "GH", DEV, "#e5e7eb",
        [r"^(?:ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9]{30,}$",
         r"^github_pat_[A-Za-z0-9_]{20,}$"],
        build_verifier("GitHub", "https://api.github.com/user",
                       _detail("GitHub", "login", "name"),
                       extra_headers={"Accept": "application/vnd.github+json",
                                      "X-GitHub-Api-Version": "2022-11-28"}),
        placeholder="ghp_…", endpoint="GET api.github.com/user",
        docs="https://github.com/settings/tokens", env="GITHUB_TOKEN", featured=True),
    "GitLab": Provider(
        "GitLab", "GL", DEV, "#fc6d26",
        [r"^glpat-[A-Za-z0-9_-]{20,}$"],
        build_verifier("GitLab", "https://gitlab.com/api/v4/user",
                       _detail("GitLab", "username", "name"),
                       mode="header", header_name="PRIVATE-TOKEN", header_fmt="{key}"),
        placeholder="glpat-…", endpoint="GET gitlab.com/api/v4/user",
        docs="https://gitlab.com/-/user_settings/personal_access_tokens",
        env="GITLAB_TOKEN"),
    "npm": Provider(
        "npm", "NP", DEV, "#cb3837",
        [r"^npm_[A-Za-z0-9]{30,}$"],
        build_verifier("npm", "https://registry.npmjs.org/-/whoami",
                       _detail("npm", "username")),
        placeholder="npm_…", endpoint="GET registry.npmjs.org/-/whoami",
        docs="https://www.npmjs.com/settings/~/tokens", env="NPM_TOKEN"),
    "Figma": Provider(
        "Figma", "FG", DEV, "#f24e1e",
        [r"^figd_[A-Za-z0-9_-]{20,}$"],
        build_verifier("Figma", "https://api.figma.com/v1/me",
                       _detail("Figma", "handle", "email"),
                       mode="header", header_name="X-Figma-Token", header_fmt="{key}"),
        placeholder="figd_…", endpoint="GET api.figma.com/v1/me",
        docs="https://www.figma.com/developers/api#access-tokens",
        env="FIGMA_TOKEN"),
    "Linear": Provider(
        "Linear", "LN", DEV, "#5e6ad2",
        [r"^lin_api_[A-Za-z0-9]{20,}$"],
        build_verifier("Linear", "https://api.linear.app/graphql",
                       _detail("Linear", "data.viewer.name", "data.viewer.email"),
                       method="POST", mode="header", header_fmt="{key}",
                       json_body={"query": "{ viewer { name email } }"},
                       invalid_codes=(400, 401, 403),
                       body_ok=lambda d: bool((d.get("data") or {}).get("viewer"))),
        placeholder="lin_api_…", endpoint="POST api.linear.app/graphql (viewer)",
        docs="https://linear.app/settings/api", env="LINEAR_API_KEY"),

    # --- Cloud & infrastructure -------------------------------------------
    "Vercel": Provider(
        "Vercel", "VC", CLOUD, "#cbd5e1",
        [r"^[A-Za-z0-9]{24}$"],
        build_verifier("Vercel", "https://api.vercel.com/v2/user",
                       _detail("Vercel", "user.username", "user.email", "username")),
        placeholder="24 characters", endpoint="GET api.vercel.com/v2/user",
        docs="https://vercel.com/account/tokens", env="VERCEL_TOKEN", broad=True),
    "Cloudflare": Provider(
        "Cloudflare", "CF", CLOUD, "#f6821f",
        [r"^[A-Za-z0-9_-]{40}$"],
        build_verifier("Cloudflare",
                       "https://api.cloudflare.com/client/v4/user/tokens/verify",
                       _detail("Cloudflare", "result.status", prefix="Token status"),
                       invalid_codes=(400, 401, 403),
                       body_ok=lambda d: d.get("success") is True),
        placeholder="40 characters",
        endpoint="GET api.cloudflare.com/client/v4/user/tokens/verify",
        docs="https://dash.cloudflare.com/profile/api-tokens",
        env="CLOUDFLARE_API_TOKEN", broad=True),
    "AWS Access Key ID": Provider(
        "AWS Access Key ID", "AW", CLOUD, "#ff9900",
        [r"^(?:AKIA|ASIA)[A-Z0-9]{16}$"],
        format_only("AWS access-key-id structure recognised. An access key id is "
                    "only half of a credential pair — it cannot be proven valid "
                    "without its secret access key, which this app never requests."),
        placeholder="AKIA…", endpoint="none (requires the secret half)",
        docs="https://console.aws.amazon.com/iam/",
        note="An access key id cannot be verified without its secret access key."),

    # --- Payments ----------------------------------------------------------
    "Stripe": Provider(
        "Stripe", "SP", PAY, "#635bff",
        [r"^(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}$"],
        build_verifier("Stripe", "https://api.stripe.com/v1/account",
                       _detail("Stripe", "business_profile.name", "email", "id",
                               prefix="Account"),
                       mode="basic"),
        placeholder="sk_live_…", endpoint="GET api.stripe.com/v1/account",
        docs="https://dashboard.stripe.com/apikeys", env="STRIPE_SECRET_KEY"),

    # --- Communication -----------------------------------------------------
    "Slack": Provider(
        "Slack", "SL", COMMS, "#e01e5a",
        [r"^xox[baprse]-[A-Za-z0-9-]{10,}$"],
        build_verifier("Slack", "https://slack.com/api/auth.test",
                       _detail("Slack", "team", "user", prefix="Workspace"),
                       method="POST", body_ok=lambda d: d.get("ok") is True),
        placeholder="xoxb-…", endpoint="POST slack.com/api/auth.test",
        docs="https://api.slack.com/apps", env="SLACK_BOT_TOKEN"),
    "Discord Bot Token": Provider(
        "Discord Bot Token", "DC", COMMS, "#5865f2",
        [r"^(?!eyJ)[A-Za-z0-9_-]{24,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}$"],
        build_verifier("Discord", "https://discord.com/api/v10/users/@me",
                       _detail("Discord", "username", "id"),
                       header_fmt="Bot {key}"),
        placeholder="bot token", endpoint="GET discord.com/api/v10/users/@me",
        docs="https://discord.com/developers/applications",
        env="DISCORD_BOT_TOKEN"),
    "Telegram Bot Token": Provider(
        "Telegram Bot Token", "TL", COMMS, "#26a5e4",
        [r"^\d{6,}:[A-Za-z0-9_-]{30,}$"],
        build_verifier("Telegram", "https://api.telegram.org/bot{key}/getMe",
                       _detail("Telegram", "result.username", "result.first_name",
                               prefix="Bot"),
                       mode="path", invalid_codes=(401, 403, 404),
                       body_ok=lambda d: d.get("ok") is True),
        placeholder="123456:ABC…", endpoint="GET api.telegram.org/bot…/getMe",
        docs="https://core.telegram.org/bots#botfather", env="TELEGRAM_BOT_TOKEN"),
    "Resend": Provider(
        "Resend", "RS", COMMS, "#a3a3a3",
        [r"^re_[A-Za-z0-9_]{10,}$"],
        build_verifier("Resend", "https://api.resend.com/domains",
                       _count("Resend", noun="domains")),
        placeholder="re_…", endpoint="GET api.resend.com/domains",
        docs="https://resend.com/api-keys", env="RESEND_API_KEY"),
    "SendGrid": Provider(
        "SendGrid", "SG", COMMS, "#1a82e2",
        [r"^SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}$"],
        build_verifier("SendGrid", "https://api.sendgrid.com/v3/user/account",
                       _detail("SendGrid", "type", "reputation", prefix="Account type")),
        placeholder="SG.…", endpoint="GET api.sendgrid.com/v3/user/account",
        docs="https://app.sendgrid.com/settings/api_keys", env="SENDGRID_API_KEY"),

    # --- Productivity & data ----------------------------------------------
    "Notion": Provider(
        "Notion", "NT", DATA, "#d4d4d8",
        [r"^(?:secret_|ntn_)[A-Za-z0-9]{32,}$"],
        build_verifier("Notion", "https://api.notion.com/v1/users/me",
                       _detail("Notion", "name", "bot.workspace_name", "id"),
                       extra_headers={"Notion-Version": "2022-06-28"}),
        placeholder="ntn_…", endpoint="GET api.notion.com/v1/users/me",
        docs="https://www.notion.so/my-integrations", env="NOTION_API_KEY"),
    "Airtable": Provider(
        "Airtable", "AT", DATA, "#fcb400",
        [r"^pat[A-Za-z0-9]{10,}\.[a-f0-9]{40,}$"],
        build_verifier("Airtable", "https://api.airtable.com/v0/meta/whoami",
                       _detail("Airtable", "email", "id")),
        placeholder="pat…", endpoint="GET api.airtable.com/v0/meta/whoami",
        docs="https://airtable.com/create/tokens", env="AIRTABLE_API_KEY"),
}

FEATURED_PROVIDERS = [name for name, p in REGISTRY.items() if p.featured]

VERIFIABLE_PROVIDERS = [name for name, p in REGISTRY.items() if p.verifiable]

PROVIDER_COUNT = len(REGISTRY)


def by_category() -> dict[str, list[str]]:
    """Provider names grouped by category, in display order."""
    grouped: dict[str, list[str]] = {category: [] for category in CATEGORY_ORDER}
    for name, provider in REGISTRY.items():
        grouped.setdefault(provider.category, []).append(name)
    return {category: names for category, names in grouped.items() if names}


def validate_key_format(provider: str, key: str) -> tuple[bool, str]:
    """Return (ok, message) for whether ``key`` matches ``provider``'s format."""
    key = key.strip()
    p = REGISTRY.get(provider)
    if not p:
        return True, ""
    if any(re.fullmatch(pattern, key) for pattern in p.patterns):
        return True, ""
    if p.broad:
        return False, (f"Value does not have the shape {p.name} keys use "
                       f"({p.placeholder}).")
    return False, f"Key format does not match the expected {p.name} pattern."


def detect_candidates(key: str) -> list[str]:
    """Every registered provider whose format matches ``key``.

    Specific signatures are returned before broad/ambiguous ones so callers can
    prefer a confident match.
    """
    key = key.strip()
    matches = [name for name, p in REGISTRY.items()
               if any(re.fullmatch(pattern, key) for pattern in p.patterns)]
    return sorted(matches, key=lambda name: REGISTRY[name].broad)
