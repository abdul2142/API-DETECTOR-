"""Keyscope -- API credential verification console (Streamlit UI).

Lets you verify that *your own* API keys are accepted by their providers. Keys
live only in the Streamlit session; nothing is written to disk by this app.

Layering
--------
``providers.py``  provider registry + verification (all network access)
``ui.py``         design tokens, animation system, HTML builders (all markup)
``app.py``        this file: state, orchestration, layout

Verification is dispatched through a single queue so that every run -- one
provider or thirty -- streams the same live progress panel while requests are
in flight, then re-renders the results.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Iterable, Sequence

import pandas as pd
import requests
import streamlit as st

import providers as P
import ui

st.set_page_config(
    page_title="Keyscope · API Key Verification",
    page_icon="\U0001f510",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Optional: pick up a local .env so keys can be pre-loaded without typing.
try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional
    pass

st.markdown(ui.inject(), unsafe_allow_html=True)

# --- Session state ----------------------------------------------------------

_DEFAULT_STATE = {
    "results": [],        # list of result rows, one per provider
    "scan_queue": None,   # provider names awaiting verification
    "celebrate": False,   # one-shot burst after an all-live run
    # Note: not "detector" -- that is the detector form's widget key, and
    # Streamlit forbids seeding session state for a widget key.
    "detector_report": None,  # last detector verdict
}
for _key, _value in _DEFAULT_STATE.items():
    st.session_state.setdefault(_key, _value)


# --- Status vocabulary ------------------------------------------------------

LIVE = "Live"
REJECTED = "Rejected"
RATE_LIMITED = "Rate limited"
UNREACHABLE = "Unreachable"
FORMAT_ONLY = "Format only"
BAD_FORMAT = "Invalid format"
NETWORK_ERROR = "Network error"
NOT_CHECKED = "Not checked"

STATE_LABELS = {
    P.VERIFIED: LIVE,
    P.INVALID: REJECTED,
    P.LIMITED: RATE_LIMITED,
    P.UNAVAILABLE: UNREACHABLE,
    P.FORMAT_ONLY: FORMAT_ONLY,
}

# A tone drives colour *and* motion: ok pulses, bad shakes once, warn breathes.
STATUS_TONE = {
    LIVE: ui.TONE_OK,
    REJECTED: ui.TONE_BAD,
    BAD_FORMAT: ui.TONE_BAD,
    NETWORK_ERROR: ui.TONE_BAD,
    RATE_LIMITED: ui.TONE_WARN,
    FORMAT_ONLY: ui.TONE_WARN,
    UNREACHABLE: ui.TONE_INFO,
    NOT_CHECKED: ui.TONE_IDLE,
}

CONCLUSIVE = {LIVE, REJECTED}

COLUMNS = ["Provider", "Status", "Key", "HTTP Code", "Response Time (ms)",
           "Account / Detail", "Checked At"]


def tone_of(status: str) -> str:
    return STATUS_TONE.get(status, ui.TONE_IDLE)


def meta_of(provider: str) -> tuple[str, str]:
    """``(avatar mark, accent colour)`` for a provider, with a safe fallback."""
    entry = P.REGISTRY.get(provider)
    return (entry.mark, entry.accent) if entry else ("?", "#6366f1")


def mask_key(key: str) -> str:
    """Show enough of a key to recognise it without exposing the secret."""
    key = key.strip()
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]} ({len(key)} chars)"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- Verification -----------------------------------------------------------

def run_check(provider: str, key: str) -> dict:
    """Verify one provider's key and return a results-table row.

    Never raises: every failure path becomes a row with an honest status.
    """
    row = {"Provider": provider, "Status": NOT_CHECKED, "Key": "", "HTTP Code": "",
           "Response Time (ms)": "", "Account / Detail": "", "Checked At": _now()}

    if not key or not key.strip():
        row["Account / Detail"] = "No key supplied"
        return row

    row["Key"] = mask_key(key)
    entry = P.REGISTRY.get(provider)
    if entry is None:
        row["Status"] = UNREACHABLE
        row["Account / Detail"] = "Unknown provider"
        return row

    ok, message = P.validate_key_format(provider, key)
    if not ok:
        row["Status"] = BAD_FORMAT
        row["Account / Detail"] = message
        return row

    if entry.verifier is None:
        row["Status"] = FORMAT_ONLY
        row["Account / Detail"] = entry.note or "Cannot be live-verified."
        return row

    try:
        result = entry.verifier(key.strip())
    except requests.RequestException as exc:
        row["Status"] = NETWORK_ERROR
        row["Account / Detail"] = P.safe_error(exc=exc)
        return row
    except Exception as exc:  # a provider quirk must not take the app down
        row["Status"] = UNREACHABLE
        row["Account / Detail"] = f"Verification failed safely: {type(exc).__name__}"
        return row

    detail = result.detail
    if result.state == P.VERIFIED and result.rate_remaining:
        detail += f" | rate limit remaining: {result.rate_remaining}"

    row["Status"] = STATE_LABELS.get(result.state, UNREACHABLE)
    row["HTTP Code"] = result.http_code or ""
    row["Response Time (ms)"] = result.elapsed_ms or ""
    row["Account / Detail"] = detail
    return row


def safe_verify(provider: str, key: str) -> P.VerifyResult:
    """Verify for the detector, converting every failure into a result object."""
    try:
        return P.REGISTRY[provider].verifier(key)  # type: ignore[misc]
    except requests.RequestException as exc:
        return P.VerifyResult(f"Live verification unavailable: {type(exc).__name__}",
                              "Medium", P.UNAVAILABLE)
    except Exception as exc:
        return P.VerifyResult(f"Live verification failed safely: {type(exc).__name__}",
                              "Medium", P.UNAVAILABLE)


def with_progress(names: Sequence[str], worker: Callable[[str], object],
                  placeholder, label: str) -> list[tuple[str, object]]:
    """Run ``worker`` over ``names`` concurrently, streaming a live progress panel.

    Each completion re-renders the panel into the same placeholder, so the
    track advances and chips flip to a drawn check mark while requests are
    still in flight. Streamlit flushes each delta over its websocket, which is
    what makes the animation visible during otherwise-blocking work.
    """
    total = len(names)
    placeholder.markdown(ui.scan_panel(names, 0, total, label=label),
                         unsafe_allow_html=True)

    done: list[tuple[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(8, total)) as pool:
        futures = {pool.submit(worker, name): name for name in names}
        for future in as_completed(futures):
            done.append((futures[future], future.result()))
            placeholder.markdown(
                ui.scan_panel(names, len(done), total,
                              finished=[n for n, _ in done], label=label),
                unsafe_allow_html=True)
    return done


def merge_results(rows: Iterable[dict]) -> None:
    """Replace rows for the providers just checked, keeping the rest."""
    rows = list(rows)
    checked = {row["Provider"] for row in rows}
    kept = [row for row in st.session_state.results if row["Provider"] not in checked]
    merged = kept + rows
    merged.sort(key=lambda row: list(P.REGISTRY).index(row["Provider"])
                if row["Provider"] in P.REGISTRY else 999)
    st.session_state.results = merged

    conclusive = [row for row in rows if row["Status"] in CONCLUSIVE]
    st.session_state.celebrate = bool(conclusive) and all(
        row["Status"] == LIVE for row in conclusive)


def queue_scan(names: Sequence[str]) -> None:
    st.session_state.scan_queue = list(names)
    st.rerun()


# --- Sidebar ----------------------------------------------------------------

ENV_AVAILABLE = {name: os.environ[entry.env]
                 for name, entry in P.REGISTRY.items()
                 if entry.env and os.environ.get(entry.env)}

with st.sidebar:
    st.markdown(ui.sidebar_brand(P.PROVIDER_COUNT), unsafe_allow_html=True)

    if ENV_AVAILABLE:
        st.caption(f"Found {len(ENV_AVAILABLE)} key(s) in your environment.")
        if st.button("Load from environment", width="stretch"):
            # Written before the inputs are created below, which is the
            # supported way to seed a widget's value.
            for _name, _value in ENV_AVAILABLE.items():
                st.session_state[f"k_{_name}"] = _value
            st.toast(f"Loaded {len(ENV_AVAILABLE)} key(s) from the environment.")

    selected = st.multiselect(
        "Providers to check",
        P.VERIFIABLE_PROVIDERS,
        default=[name for name in P.FEATURED_PROVIDERS
                 if name in P.VERIFIABLE_PROVIDERS],
        key="selected_providers",
        help="Pick the credentials you want on the dashboard. "
             f"{len(P.VERIFIABLE_PROVIDERS)} of {P.PROVIDER_COUNT} providers can be "
             "verified live.",
    )

    keys: dict[str, str] = {}
    if selected:
        st.markdown(ui.section("Credentials"), unsafe_allow_html=True)
    for name in selected:
        keys[name] = st.text_input(
            name, type="password", key=f"k_{name}",
            placeholder=P.REGISTRY[name].placeholder,
        )

    supplied = [name for name in selected if keys.get(name, "").strip()]

    st.divider()
    if st.button(f"Verify {len(supplied) or 'all'} credential"
                 f"{'' if len(supplied) == 1 else 's'}",
                 type="primary", width="stretch", disabled=not supplied):
        queue_scan(supplied)
    if st.button("Clear results", width="stretch",
                 disabled=not st.session_state.results):
        st.session_state.results = []
        st.session_state.celebrate = False
        st.rerun()

    st.divider()
    st.caption("Keys are held only in this browser session and are never written "
               "to disk by this app. Avoid sharing screenshots that contain secrets.")
    st.caption("Quota information is provider-dependent. Rate-limit headers are "
               "shown when available; billing accuracy is not guaranteed.")


# --- Hero -------------------------------------------------------------------

_results = st.session_state.results
_live_count = sum(1 for row in _results if row["Status"] == LIVE)

st.markdown(ui.hero([
    ('<span class="ks-live"></span>Session only', True),
    (f"<b>{P.PROVIDER_COUNT}</b>&nbsp;providers", False),
    (f"<b>{len(supplied)}</b>&nbsp;ready", False),
    (f"<b>{_live_count}</b>&nbsp;live", False),
]), unsafe_allow_html=True)

tab_dash, tab_detect, tab_catalog, tab_about = st.tabs(
    ["Dashboard", "Key detector", "Provider catalog", "How it works"])


# ===========================================================================
# Dashboard
# ===========================================================================

with tab_dash:
    # --- Queued verification run, with the live progress panel -------------
    if st.session_state.scan_queue:
        queue = [name for name in st.session_state.scan_queue if name in P.REGISTRY]
        panel = st.empty()
        skeleton = st.empty()
        skeleton.markdown(ui.skeleton_rows(min(len(queue), 4)), unsafe_allow_html=True)

        # Resolve the keys here, on the main thread: session state is not
        # available inside the worker threads that with_progress dispatches to.
        pending = {name: st.session_state.get(f"k_{name}", "") for name in queue}

        completed = with_progress(
            queue,
            lambda name: run_check(name, pending[name]),
            panel,
            "Verifying credentials",
        )
        merge_results(row for _, row in completed)

        st.session_state.scan_queue = None
        panel.empty()
        skeleton.empty()
        st.rerun()

    results = st.session_state.results
    latest = {row["Provider"]: row for row in results}

    # --- Summary -----------------------------------------------------------
    if results:
        frame = pd.DataFrame(results)
        live = int((frame["Status"] == LIVE).sum())
        rejected = int((frame["Status"] == REJECTED).sum())
        conclusive = int(frame["Status"].isin(CONCLUSIVE).sum())
        latency = pd.to_numeric(frame["Response Time (ms)"], errors="coerce")
        average = latency.mean()
        fastest = latency.min()

        score = round(live / conclusive * 100) if conclusive else 0
        if conclusive == 0:
            headline, tone = "No conclusive answer yet", ui.TONE_INFO
            body = ("None of the providers returned a definitive accept or reject. "
                    "That usually means a network problem or a credential that "
                    "cannot be verified on its own.")
        elif live == conclusive:
            headline, tone = "All checked credentials are live", ui.TONE_OK
            body = (f"{live} of {conclusive} credential"
                    f"{'' if conclusive == 1 else 's'} were accepted by their own "
                    "provider. Inconclusive checks are excluded from this score.")
        elif live == 0:
            headline, tone = "No credential was accepted", ui.TONE_BAD
            body = (f"All {conclusive} conclusive check"
                    f"{'' if conclusive == 1 else 's'} were rejected by the provider. "
                    "Check for a rotated, revoked or mistyped key.")
        else:
            headline, tone = "Some credentials need attention", ui.TONE_WARN
            body = (f"{live} of {conclusive} conclusive check"
                    f"{'' if conclusive == 1 else 's'} were accepted; "
                    f"{rejected} rejected. Inconclusive results are excluded.")

        if st.session_state.celebrate and live and live == conclusive:
            st.markdown(ui.burst(), unsafe_allow_html=True)
            st.session_state.celebrate = False

        st.markdown(
            ui.health_gauge(score, headline, body, tone, legend=[
                (f"{live} live", ui.TONE_HEX[ui.TONE_OK]),
                (f"{rejected} rejected", ui.TONE_HEX[ui.TONE_BAD]),
                (f"{len(frame) - conclusive} inconclusive", ui.TONE_HEX[ui.TONE_INFO]),
            ]),
            unsafe_allow_html=True)

        st.markdown(ui.spacer(12), unsafe_allow_html=True)
        st.markdown(ui.metrics([
            ("Checked", str(len(frame)), "providers this session", ui.TONE_IDLE),
            ("Live", str(live), "accepted by provider", ui.TONE_OK),
            ("Rejected", str(rejected), "credential refused", ui.TONE_BAD),
            ("Avg. response", f"{average:,.0f} ms" if pd.notna(average) else "—",
             "across all probes", ui.TONE_IDLE),
            ("Fastest", f"{fastest:,.0f} ms" if pd.notna(fastest) else "—",
             "quickest round trip", ui.TONE_IDLE),
        ]), unsafe_allow_html=True)

    # --- Provider grid -----------------------------------------------------
    st.markdown(
        ui.section("Providers", f"{len(supplied)} of {len(selected)} have a key entered"
                   if selected else "choose providers in the sidebar"),
        unsafe_allow_html=True)

    if not selected:
        st.markdown(ui.empty_state(
            "No providers selected",
            "Use the sidebar to pick the credentials you want to verify, paste a key "
            "for each one, then run a check. Nothing leaves this session except the "
            "read-only request to the provider you chose."), unsafe_allow_html=True)
    else:
        per_row = 3
        for start in range(0, len(selected), per_row):
            chunk = selected[start:start + per_row]
            columns = st.columns(per_row, gap="small")
            for offset, provider in enumerate(chunk):
                entry = P.REGISTRY[provider]
                row = latest.get(provider)
                has_key = bool(keys.get(provider, "").strip())

                if row:
                    status, meta = row["Status"], f"Checked {row['Checked At'][11:]}"
                    if row["Response Time (ms)"]:
                        meta += f' &middot; {row["Response Time (ms)"]:,.0f} ms'
                elif has_key:
                    status, meta = NOT_CHECKED, "Key entered, ready to verify"
                else:
                    status, meta = NOT_CHECKED, "Waiting for a key"

                with columns[offset]:
                    st.markdown(
                        ui.provider_card(
                            provider, entry.mark, entry.category, entry.accent,
                            status, tone_of(status), meta,
                            delay=(start // per_row) * 0.06 + offset * 0.05),
                        unsafe_allow_html=True)
                    if st.button("Verify", key=f"go_{provider}", width="stretch",
                                 disabled=not has_key):
                        queue_scan([provider])

    # --- Results -----------------------------------------------------------
    st.markdown(ui.section("Results", "newest state per provider"),
                unsafe_allow_html=True)

    if results:
        st.markdown(ui.results_table(results, tone_of, meta_of),
                    unsafe_allow_html=True)

        timed = [(row["Provider"], float(row["Response Time (ms)"]), tone_of(row["Status"]))
                 for row in results
                 if isinstance(row["Response Time (ms)"], (int, float))]
        if timed:
            st.markdown(ui.section("Response time", "per provider round trip"),
                        unsafe_allow_html=True)
            st.markdown(ui.latency_bars(sorted(timed, key=lambda item: item[1])),
                        unsafe_allow_html=True)

        with st.expander("Raw data and export"):
            # Coerce the numeric columns: unchecked rows carry "", and a mixed
            # str/float column cannot be serialised to Arrow for st.dataframe.
            frame = pd.DataFrame(results)[COLUMNS].copy()
            frame["Response Time (ms)"] = pd.to_numeric(
                frame["Response Time (ms)"], errors="coerce").round(1)
            frame["HTTP Code"] = pd.to_numeric(
                frame["HTTP Code"], errors="coerce").astype("Int64")
            st.dataframe(frame, width="stretch", hide_index=True)
            left, right = st.columns(2)
            left.download_button("Download CSV", frame.to_csv(index=False).encode("utf-8"),
                                 "keyscope_results.csv", "text/csv", width="stretch")
            right.download_button("Download JSON",
                                  json.dumps(results, indent=2).encode("utf-8"),
                                  "keyscope_results.json", "application/json",
                                  width="stretch")
    else:
        st.markdown(ui.empty_state(
            "No checks yet",
            "Results appear here once you verify a credential. Each row records the "
            "provider's own verdict, the HTTP status it returned and how long the "
            "round trip took."), unsafe_allow_html=True)

    st.markdown(ui.footnote(
        "A live result means the tested endpoint accepted the credential. It does not "
        "prove access to every model, workspace, repository or paid quota."),
        unsafe_allow_html=True)


# ===========================================================================
# Key detector
# ===========================================================================

def analyse(key: str, placeholder) -> dict:
    """Detect and, where possible, verify an arbitrary credential.

    Returns a verdict dict plus the per-candidate probe rows the UI renders.
    The honesty rules live here: a format match alone is never reported as an
    active key, and a rejection from a broad/ambiguous pattern never pins the
    key to that provider.
    """
    blank = {"probes": []}
    candidates = P.detect_candidates(key)

    if not candidates:
        return {**blank, "detected": "Unknown or unsupported", "confidence": "Low",
                "status": "No provider signature matched", "tone": ui.TONE_IDLE,
                "detail": "The value does not match any supported provider format. It "
                          "may be a custom token, an internal credential, or a provider "
                          "this detector does not cover."}

    # A specific signature always beats a broad, shape-only match.
    specific = [name for name in candidates if not P.REGISTRY[name].broad]
    if specific:
        candidates = specific

    verifiable = [name for name in candidates if P.REGISTRY[name].verifiable]
    if not verifiable:
        entry = P.REGISTRY[candidates[0]]
        return {**blank, "detected": entry.name, "confidence": "Medium",
                "status": FORMAT_ONLY, "tone": ui.TONE_WARN,
                "detail": entry.note or "This credential type cannot be proven valid "
                                        "on its own."}

    probed = with_progress(verifiable, lambda name: safe_verify(name, key),
                           placeholder, "Probing candidate providers")
    placeholder.empty()

    outcomes: list[tuple[str, P.VerifyResult]] = [
        (name, result) for name, result in probed]  # type: ignore[misc]
    rows = [(name, STATE_LABELS.get(result.state, UNREACHABLE), result.detail,
             tone_of(STATE_LABELS.get(result.state, UNREACHABLE)))
            for name, result in outcomes]
    verdict = {"probes": sorted(rows, key=lambda row: row[1] != LIVE)}

    verified = [(n, r) for n, r in outcomes if r.state == P.VERIFIED]
    limited = [(n, r) for n, r in outcomes if r.state == P.LIMITED]
    invalid = [(n, r) for n, r in outcomes if r.state == P.INVALID]
    unavailable = [(n, r) for n, r in outcomes if r.state == P.UNAVAILABLE]

    if len(verified) == 1:
        name, result = verified[0]
        return {**verdict, "detected": name, "confidence": "High",
                "status": "Verified live", "tone": ui.TONE_OK, "detail": result.detail}
    if len(verified) > 1:
        return {**verdict, "detected": "Multiple providers accepted it",
                "confidence": "High", "status": "Accepted by several endpoints",
                "tone": ui.TONE_OK,
                "detail": "Live verification succeeded for: "
                          + ", ".join(name for name, _ in verified) + "."}
    if limited:
        name = limited[0][0] if len(limited) == 1 else "Multiple candidates"
        return {**verdict, "detected": name, "confidence": "Medium",
                "status": RATE_LIMITED, "tone": ui.TONE_WARN,
                "detail": "The provider rate-limited the request, which is not "
                          "evidence that the key is invalid. Try again shortly."}

    # A rejection only identifies the provider when the pattern was specific.
    pinned = [(n, r) for n, r in invalid if not P.REGISTRY[n].broad]
    if pinned and len(pinned) == len(verifiable):
        name = pinned[0][0] if len(pinned) == 1 else "Format candidates"
        return {**verdict, "detected": name, "confidence": "High",
                "status": "Rejected by provider", "tone": ui.TONE_BAD,
                "detail": "; ".join(f"{n}: {r.detail}" for n, r in pinned)}

    if any(P.REGISTRY[name].broad for name, _ in outcomes):
        return {**verdict, "detected": "Unknown or unsupported", "confidence": "Low",
                "status": "Not verified", "tone": ui.TONE_IDLE,
                "detail": "The value has the shape of an opaque token, but no provider "
                          "authenticated it. It will not be labelled on shape alone."}
    if unavailable:
        name = candidates[0] if len(candidates) == 1 else "Multiple candidates"
        return {**verdict, "detected": name, "confidence": "Medium",
                "status": UNREACHABLE, "tone": ui.TONE_INFO,
                "detail": "; ".join(f"{n}: {r.detail}" for n, r in unavailable)}

    return {**verdict, "detected": candidates[0] if len(candidates) == 1
            else "Multiple candidates", "confidence": "Medium",
            "status": "Not verified", "tone": ui.TONE_WARN,
            "detail": "The structure matches a known provider, but no successful live "
                      "authentication response was received."}


with tab_detect:
    st.markdown(ui.section("Detect and verify any key",
                           "structure first, then a live probe"),
                unsafe_allow_html=True)
    st.markdown(ui.note(
        "Paste any credential. Keyscope identifies the likely provider from the key's "
        "structure, then verifies it against that provider when a safe read-only "
        "endpoint exists. <b>A format match alone is never reported as an active "
        "key.</b>", ui.TONE_INFO), unsafe_allow_html=True)
    st.markdown(ui.spacer(14), unsafe_allow_html=True)

    with st.form("detector", border=False):
        candidate_key = st.text_input("API key or token", type="password",
                                      placeholder="Paste a key")
        submitted = st.form_submit_button("Analyse key", type="primary")

    progress = st.empty()

    if submitted:
        cleaned = candidate_key.strip()
        if not cleaned:
            st.warning("Enter a key first.")
            st.session_state.detector_report = None
        else:
            st.session_state.detector_report = analyse(cleaned, progress)

    report = st.session_state.detector_report
    if report:
        st.markdown(ui.verdict(report["detected"], report["status"], report["detail"],
                               report["confidence"], report["tone"]),
                    unsafe_allow_html=True)
        if report["probes"]:
            st.markdown(ui.section("Candidate probes",
                                   f"{len(report['probes'])} endpoint"
                                   f"{'' if len(report['probes']) == 1 else 's'} queried"),
                        unsafe_allow_html=True)
            st.markdown(ui.probe_rows(report["probes"]), unsafe_allow_html=True)

    st.markdown(ui.footnote(
        "“High” confidence means the provider itself accepted or rejected the "
        "credential. Broad or opaque formats are only named once an endpoint "
        "authenticates them."), unsafe_allow_html=True)


# ===========================================================================
# Provider catalog
# ===========================================================================

with tab_catalog:
    st.markdown(ui.section("Provider catalog",
                           f"{P.PROVIDER_COUNT} credential types, "
                           f"{len(P.VERIFIABLE_PROVIDERS)} live-verifiable"),
                unsafe_allow_html=True)

    filters = st.columns([2, 3])
    query = filters[0].text_input("Search", placeholder="Provider name…",
                                  label_visibility="collapsed")
    chosen = filters[1].multiselect("Categories", P.CATEGORY_ORDER,
                                    placeholder="All categories",
                                    label_visibility="collapsed")

    needle = query.strip().lower()
    entries = [
        {"name": entry.name, "mark": entry.mark, "category": entry.category,
         "accent": entry.accent, "patterns": entry.patterns,
         "endpoint": entry.endpoint, "env": entry.env, "note": entry.note,
         "verifiable": entry.verifiable}
        for entry in P.REGISTRY.values()
        if (not needle or needle in entry.name.lower())
        and (not chosen or entry.category in chosen)
    ]

    if entries:
        st.markdown(ui.catalog_cards(entries), unsafe_allow_html=True)
    else:
        st.markdown(ui.empty_state(
            "Nothing matches that filter",
            "Clear the search box or pick a different category to see the "
            "supported credential types again."), unsafe_allow_html=True)

    st.markdown(ui.footnote(
        "Every probe listed here is a read-only request against the provider's own "
        "API. Keyscope never creates, modifies or deletes anything in your accounts."),
        unsafe_allow_html=True)


# ===========================================================================
# How it works
# ===========================================================================

with tab_about:
    st.markdown(ui.section("What a result means"), unsafe_allow_html=True)
    st.markdown(ui.legend([
        (LIVE, ui.TONE_OK,
         "The provider accepted the credential (HTTP 2xx) on a read-only endpoint."),
        (REJECTED, ui.TONE_BAD,
         "The provider explicitly refused the credential, usually 401 or 403."),
        (RATE_LIMITED, ui.TONE_WARN,
         "The request was throttled (429). This is not evidence that the key is bad."),
        (FORMAT_ONLY, ui.TONE_WARN,
         "The structure is recognised but cannot be proven live — for example an "
         "AWS access key id, which needs its secret half."),
        (UNREACHABLE, ui.TONE_INFO,
         "No conclusion could be drawn: a network failure or an unexpected status."),
        (BAD_FORMAT, ui.TONE_BAD,
         "The value does not match the pattern that provider's keys use."),
    ]), unsafe_allow_html=True)

    st.markdown(ui.section("How verification works"), unsafe_allow_html=True)
    st.markdown(ui.note(
        "Each provider has one <b>read-only</b> verification call against its own API "
        "— for example <code>GET /v1/models</code> for OpenAI, Anthropic and Groq, "
        "<code>GET /user</code> for GitHub, or <code>POST auth.test</code> for Slack. "
        "Transient network errors and 5xx responses are retried with backoff; "
        "authentication outcomes are returned immediately, because a retry would not "
        "change them and would waste a provider request.", ui.TONE_INFO, 0.0),
        unsafe_allow_html=True)
    st.markdown(ui.spacer(9), unsafe_allow_html=True)
    st.markdown(ui.note(
        "Detection and verification are kept strictly separate. Detection asks "
        "<i>does this key look like provider X?</i>; verification asks <i>does X accept "
        "it?</i> Only the second can report a key as live. Verifiers also fail "
        "<b>open</b>: when a response cannot be interpreted the result is "
        "<b>Unreachable</b>, never <b>Rejected</b>.", ui.TONE_OK, 0.08),
        unsafe_allow_html=True)

    st.markdown(ui.section("Security"), unsafe_allow_html=True)
    st.markdown(ui.note(
        "Keys live only in this Streamlit session and are never written to disk by "
        "this app. They are sent only to the provider you selected, over HTTPS, and "
        "are masked in every table and export. Enter only credentials you own and are "
        "authorised to test, and avoid pasting production secrets into screenshots.",
        ui.TONE_WARN, 0.0), unsafe_allow_html=True)
    st.markdown(ui.spacer(9), unsafe_allow_html=True)
    st.markdown(ui.note(
        "A live result proves the tested endpoint accepted the credential. It does "
        "<b>not</b> prove access to every model, workspace, repository or paid quota, "
        "and it is not a billing or entitlement check.", ui.TONE_WARN, 0.08),
        unsafe_allow_html=True)

    st.markdown(ui.section("Accessibility"), unsafe_allow_html=True)
    st.markdown(ui.note(
        "Every animation in this interface is decorative. Status is always carried by "
        "colour, icon and text together, never by movement alone, and the whole motion "
        "system switches off automatically when your system requests reduced motion.",
        ui.TONE_INFO), unsafe_allow_html=True)
