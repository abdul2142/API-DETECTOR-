# Keyscope

**API credential verification console.** A Streamlit app that checks whether
**your own** API keys are actually accepted by their providers — across 32
credential types, with an animated dashboard, a universal key detector and
deliberately honest reporting.

Keys live only in the browser session. Nothing is written to disk by this app,
and every provider call is **read-only**.

---

## What it does

| | |
| --- | --- |
| **Dashboard** | Pick providers, paste keys, verify one or all at once. Concurrent probes stream a live progress panel, then a health ring, metric tiles, a results table and per-provider latency bars. |
| **Key detector** | Paste *any* key. Keyscope infers the provider from the key's structure, probes every plausible candidate concurrently, and reports what the providers actually said. |
| **Provider catalog** | Browse all 32 credential types: category, key pattern, the exact read-only endpoint used to verify, the env var checked for prefill, and any caveat. |
| **How it works** | The status legend, the verification model, the security posture and the accessibility policy, in plain language. |

## Honest by design

This is the part that matters most in a tool like this.

- **A format match is never reported as an active key.** Detection ("does this
  *look* like provider X?") and verification ("does X *accept* it?") are
  separate code paths. Only the second can report a key as live.
- **Verifiers fail open.** If a response cannot be interpreted, the result is
  *Unreachable* — never *Rejected*. A false accusation is worse than no answer.
- **429 is not a failure.** Rate limiting means the credential got far enough to
  be throttled, so it is reported as *Rate limited*, not invalid.
- **Opaque formats are not guessed.** Broad patterns (a bare 24-, 32- or
  40-character token) are probe *candidates* only. If no provider authenticates
  the value, Keyscope says *Unknown* rather than naming a provider on shape
  alone.
- **Some credentials cannot be proven, and it says so.** An AWS access key id
  needs its secret half; a Roboflow publishable key is model-scoped; Perplexity
  exposes no read-only endpoint. These report *Format only* with the reason.
- **The health score excludes inconclusive checks**, so a network failure never
  inflates or deflates it.

## Install & run

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (default <http://localhost:8501>).

### Optional: preload keys from the environment

Copy `.env.example` to `.env`, fill in only the keys you want, and the sidebar
gains a **Load from environment** button. `.env` is gitignored; never commit it.

```bash
cp .env.example .env
```

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

127 tests, no network access. `tests/test_providers.py` covers pattern
detection and every HTTP outcome via a stubbed transport, `tests/test_ui.py`
covers escaping, gauge geometry and the reduced-motion policy, and
`tests/test_app.py` drives the real app through Streamlit's `AppTest`.

## Supported providers

**AI & ML** — OpenAI, Anthropic, Google Gemini, Groq, Hugging Face, Mistral,
Cohere, Together AI, OpenRouter, DeepSeek, Fireworks AI, Stability AI,
ElevenLabs, Replicate, Roboflow, Perplexity*, Roboflow publishable keys*

**Developer platforms** — GitHub, GitLab, npm, Figma, Linear

**Cloud & infrastructure** — Vercel, Cloudflare, AWS access key id*

**Payments** — Stripe

**Communication** — Slack, Discord, Telegram, Resend, SendGrid

**Productivity & data** — Notion, Airtable

\* format-only: identified by structure, with an explanation of why it cannot be
live-verified.

## How verification works

Each provider has one **read-only** call against its own API — `GET /v1/models`
for OpenAI, Anthropic and Groq, `GET /user` for GitHub, `POST auth.test` for
Slack, `GET /user/tokens/verify` for Cloudflare, and so on. The catalog tab
lists all of them. Keyscope never creates, modifies or deletes anything.

The response is interpreted as:

| Result | Meaning |
| --- | --- |
| **Live** | The provider accepted the credential (HTTP 2xx). |
| **Rejected** | The provider explicitly refused it (usually 401/403). |
| **Rate limited** | Accepted but throttled (429) — *not* invalid. |
| **Unreachable** | No conclusion could be drawn (network error, odd status). |
| **Format only** | Structure recognised, cannot be proven live. |
| **Invalid format** | The value does not match that provider's key pattern. |

Transient connection errors and 5xx responses are retried with backoff.
Authentication outcomes return immediately, since a retry would not change them
and would waste a provider request.

Some providers signal auth failure *inside* a 200 response — Slack's `ok:
false`, Telegram's `ok: false`, Cloudflare's `success: false`. A `body_ok`
predicate handles those, and it fails open: if the predicate raises, the
credential is not reported invalid.

## Project layout

```
app.py             Streamlit UI: state, orchestration, layout
providers.py       Provider registry + verification (all network access)
ui.py              Design tokens, animation system, HTML builders (all markup)
tests/             Offline unit tests + headless app integration tests
.streamlit/        Theme configuration
```

The three modules have strict, one-directional responsibilities: `app.py`
imports both, `providers.py` and `ui.py` import neither each other nor
Streamlit's UI surface. `providers.py` holds no markup; `ui.py` holds no
provider knowledge and makes no network calls.

### Adding a provider

Append one entry to `REGISTRY` in `providers.py`. The generic
`build_verifier()` factory covers the common auth transports, so most providers
are a single declaration:

```python
"Acme": Provider(
    "Acme", "AC", AI, "#ff6600",
    [r"^acme_[A-Za-z0-9]{32}$"],
    build_verifier("Acme", "https://api.acme.com/v1/me",
                   _detail("Acme", "email", "name")),
    placeholder="acme_…", endpoint="GET api.acme.com/v1/me",
    docs="https://acme.com/settings/keys", env="ACME_API_KEY"),
```

`mode=` selects the transport: `bearer` (default), `header` for a custom header
such as `x-api-key`, `query` for a query-string key, `basic` for HTTP basic, or
`path` for a key interpolated into the URL via `{key}`. Set `broad=True` for a
shape-only pattern so it is treated as a candidate rather than an
identification. The UI, catalog, detector and tests pick it up automatically.

## Animations

The interface uses ~30 CSS keyframe animations: a drifting aurora backdrop,
panning grid, floating orbs, gradient-sweep title, shine sweeps, staggered card
and row entrances, hover lift with a travelling border beam, pulsing status
rings, a self-drawing SVG health ring, growing latency bars with sheen, a
striped live progress track, shimmering skeletons, a scan line, an equaliser,
drawn check marks and a one-shot success burst.

All of it is **pure CSS** — Streamlit strips `<script>` from markdown, so motion
is driven by `@keyframes` plus per-element custom properties (`--d` for stagger
delay, `--w` for bar width, `--off` for ring fill). Nothing animates layout
properties that would cause reflow thrash.

**Motion is never load-bearing.** Status is always carried by colour, icon and
text together. A single `prefers-reduced-motion` block disables the entire
system, and tests assert that it stays last in the stylesheet and restores the
end state of anything whose geometry is animated.

## Security notes

- Enter only keys you own and are authorised to test.
- Keys are sent only to the provider they belong to, over HTTPS, and are masked
  (`gsk_…abcd (48 chars)`) in every table, export and log line.
- A live result means the tested endpoint accepted the credential. It does
  **not** prove access to every model, workspace, repository or paid quota, and
  it is not a billing or entitlement check.
- Remote error text is HTML-escaped before rendering; tests cover that.
- Don't paste production secrets into screenshots, and don't expose this app on
  a public address — Streamlit has no authentication layer of its own.
