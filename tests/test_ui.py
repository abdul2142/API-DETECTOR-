"""Tests for the presentation layer: escaping, geometry and motion policy."""

from __future__ import annotations

import re

import pytest

import ui


# --- Escaping ---------------------------------------------------------------

def test_esc_neutralises_markup():
    assert ui.esc("<script>alert(1)</script>") == (
        "&lt;script&gt;alert(1)&lt;/script&gt;")
    assert ui.esc('a "quoted" & ampersand') == "a &quot;quoted&quot; &amp; ampersand"


def test_untrusted_text_is_escaped_in_every_builder():
    """Provider detail strings come from remote APIs, so they must be escaped."""
    payload = "<img src=x onerror=alert(1)>"
    assert "<img" not in ui.pill(payload)
    assert "<img" not in ui.section(payload, payload)
    assert "<img" not in ui.verdict(payload, payload, payload, payload, ui.TONE_OK)
    assert "<img" not in ui.empty_state(payload, payload)
    assert "<img" not in ui.probe_rows([(payload, payload, payload, ui.TONE_BAD)])


def test_results_table_escapes_remote_detail_text():
    row = {
        "Provider": "Groq", "Status": "Live", "Key": "gsk_…abcd (44 chars)",
        "HTTP Code": 200, "Response Time (ms)": 142.0,
        "Account / Detail": "<b>injected</b>", "Checked At": "2026-10-12 11:30:00",
    }
    markup = ui.results_table([row], lambda _s: ui.TONE_OK, lambda _p: ("GQ", "#f55036"))
    assert "<b>injected</b>" not in markup
    assert "&lt;b&gt;injected&lt;/b&gt;" in markup
    assert "11:30:00" in markup


# --- Ring gauge geometry ----------------------------------------------------

def test_ring_circumference_matches_its_radius():
    assert ui.RING_CIRCUMFERENCE == pytest.approx(2 * 3.141592653589793 * ui.RING_RADIUS,
                                                  abs=0.01)


@pytest.mark.parametrize("score,fraction", [(0, 1.0), (50, 0.5), (100, 0.0)])
def test_gauge_offset_tracks_the_score(score, fraction):
    """A 100% score must leave zero dash offset, i.e. a fully drawn ring."""
    markup = ui.health_gauge(score, "head", "body", ui.TONE_OK)
    offset = float(re.search(r"--off:([0-9.]+)", markup).group(1))
    assert offset == pytest.approx(ui.RING_CIRCUMFERENCE * fraction, abs=0.05)


def test_gauge_clamps_out_of_range_scores():
    assert "--off:0" in ui.health_gauge(250, "h", "b", ui.TONE_OK).replace(" ", "")
    assert ">0<" in ui.health_gauge(-40, "h", "b", ui.TONE_BAD)


# --- Bars and progress ------------------------------------------------------

def test_latency_bars_scale_against_the_slowest_probe():
    markup = ui.latency_bars([("fast", 100.0, ui.TONE_OK), ("slow", 400.0, ui.TONE_OK)])
    widths = [float(w) for w in re.findall(r"--w:([0-9.]+)%", markup)]
    assert widths == [25.0, 100.0]


def test_latency_bars_keep_tiny_values_visible():
    markup = ui.latency_bars([("tiny", 1.0, ui.TONE_OK), ("huge", 9000.0, ui.TONE_OK)])
    widths = [float(w) for w in re.findall(r"--w:([0-9.]+)%", markup)]
    assert min(widths) >= 5.0


def test_latency_bars_tolerate_an_empty_set():
    assert ui.latency_bars([]) == ""


def test_scan_panel_reports_progress():
    markup = ui.scan_panel(["A", "B", "C", "D"], 3, 4, finished=["A", "B", "C"])
    assert "3 of 4 complete" in markup
    assert "--w:75%" in markup
    assert markup.count("is-done") == 3
    assert markup.count("is-pending") == 1


def test_scan_panel_handles_a_zero_total():
    assert "--w:0%" in ui.scan_panel([], 0, 0)


# --- Flourishes -------------------------------------------------------------

def test_burst_emits_the_requested_particle_count():
    assert ui.burst(18).count("<i ") == 18


def test_burst_offsets_are_precomputed_not_trigonometric():
    """Offsets must be literal pixel values so the markup needs no CSS math."""
    markup = ui.burst(8)
    assert "cos(" not in markup and "sin(" not in markup
    assert len(re.findall(r"--tx:-?[0-9.]+px", markup)) == 8


# --- Motion policy ----------------------------------------------------------

def test_reduced_motion_is_honoured():
    assert "@media (prefers-reduced-motion: reduce)" in ui.CSS
    block = ui.CSS.split("prefers-reduced-motion: reduce")[1]
    assert "animation-duration: 0.001ms !important" in block
    assert "animation-iteration-count: 1 !important" in block


def test_reduced_motion_block_is_last_so_it_wins():
    """It must come after the animations it is meant to override."""
    assert ui.CSS.index("prefers-reduced-motion") > ui.CSS.rindex("@keyframes")


def test_reduced_motion_restores_animated_end_states():
    """Elements whose geometry is animated must still render at full size."""
    block = ui.CSS.split("prefers-reduced-motion: reduce")[1]
    assert "stroke-dashoffset: var(--off)" in block
    assert "width: var(--w)" in block


def test_every_animation_used_has_a_keyframe_definition():
    defined = set(re.findall(r"@keyframes\s+(ks[A-Za-z]+)", ui.CSS))
    used = set(re.findall(r"animation:\s*(ks[A-Za-z]+)", ui.CSS))
    assert used - defined == set(), f"undefined animations referenced: {used - defined}"


def test_stylesheet_defines_a_broad_animation_vocabulary():
    """The brief was 'many types of animation' -- keep that from silently eroding."""
    assert len(set(re.findall(r"@keyframes\s+(ks[A-Za-z]+)", ui.CSS))) >= 25


def test_inject_ships_the_stylesheet_and_backdrop():
    markup = ui.inject()
    assert markup.startswith("\n<style>") or markup.lstrip().startswith("<style>")
    assert "ks-orbs" in markup
    assert markup.count("ks-orb\"") == len(ui._ORBS)
