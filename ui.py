"""Presentation layer for Keyscope: design tokens, animation system, HTML builders.

This module owns every pixel. It contains no provider knowledge and performs no
network access -- it turns plain data (dicts, strings, numbers) into the styled,
animated markup that ``app.py`` hands to Streamlit.

Animation policy
----------------
Every motion in this file is pure CSS. Streamlit strips ``<script>`` from
markdown, so animations are driven by ``@keyframes`` plus per-element custom
properties (``--d`` for stagger delay, ``--w`` for bar width, ``--off`` for ring
fill). That keeps motion declarative, GPU-composited and free of layout
thrash -- and it means a re-run of the script replays entrances naturally.

Entrances are staggered via ``--d`` so a grid assembles rather than popping in
all at once. Durations stay in the 180-700ms band for interface feedback and
20-40s for ambient background motion, which reads as atmosphere rather than
distraction.

Accessibility: a single ``prefers-reduced-motion`` block at the end of the
stylesheet disables all of it, so the app remains fully usable for people who
are motion-sensitive, and nothing above is allowed to convey meaning through
movement alone -- colour, text and icons always carry the status too.
"""

from __future__ import annotations

import html
import math
from typing import Iterable, Sequence

# --- Design tokens ----------------------------------------------------------

TONE_OK = "ok"
TONE_BAD = "bad"
TONE_WARN = "warn"
TONE_INFO = "info"
TONE_IDLE = "idle"

TONE_HEX = {
    TONE_OK: "#34d399",
    TONE_BAD: "#f87171",
    TONE_WARN: "#fbbf24",
    TONE_INFO: "#818cf8",
    TONE_IDLE: "#94a3b8",
}

RING_RADIUS = 52
RING_CIRCUMFERENCE = round(2 * math.pi * RING_RADIUS, 2)


def esc(value: object) -> str:
    """HTML-escape any value for safe interpolation into markup."""
    return html.escape(str(value), quote=True)


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

/* ========================================================================
   1. Design tokens
   ===================================================================== */
:root {
  --ks-bg: #07090f;
  --ks-surface: rgba(255, 255, 255, 0.034);
  --ks-surface-2: rgba(255, 255, 255, 0.055);
  --ks-border: rgba(148, 163, 184, 0.14);
  --ks-border-bright: rgba(148, 163, 184, 0.28);
  --ks-text: #e8ecf4;
  --ks-muted: #96a3b8;
  --ks-dim: #64748b;
  --ks-indigo: #6366f1;
  --ks-violet: #8b5cf6;
  --ks-cyan: #06b6d4;
  --ks-ok: #34d399;
  --ks-bad: #f87171;
  --ks-warn: #fbbf24;
  --ks-info: #818cf8;
  --ks-idle: #94a3b8;
  --ks-radius: 14px;
  --ks-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --ks-ease-soft: cubic-bezier(0.4, 0, 0.2, 1);
  --ks-shadow: 0 18px 40px -18px rgba(0, 0, 0, 0.75);
}

html, body, [class*="css"], .stApp, button, input, textarea, select {
  font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
}
code, kbd, pre, .ks-mono { font-family: 'JetBrains Mono', ui-monospace, monospace !important; }

#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"] { visibility: hidden; }
.block-container { max-width: 1280px; padding-top: 2rem; padding-bottom: 4rem; }
h1, h2, h3, h4 { letter-spacing: -0.02em; font-weight: 650; color: var(--ks-text); }
::selection { background: rgba(99, 102, 241, 0.32); }

/* Scrollbar ------------------------------------------------------------- */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: rgba(148, 163, 184, 0.2); border-radius: 999px;
  border: 2px solid transparent; background-clip: content-box;
  transition: background 0.25s var(--ks-ease-soft);
}
::-webkit-scrollbar-thumb:hover { background: rgba(148, 163, 184, 0.42); background-clip: content-box; }

/* ========================================================================
   2. Animated backdrop -- drifting aurora, panning grid, floating orbs
   ===================================================================== */
.stApp { background: var(--ks-bg); }

.stApp::before {
  content: ""; position: fixed; inset: -25%; z-index: 0; pointer-events: none;
  background:
    radial-gradient(38% 42% at 18% 14%, rgba(99, 102, 241, 0.34), transparent 62%),
    radial-gradient(34% 38% at 84% 8%, rgba(139, 92, 246, 0.26), transparent 60%),
    radial-gradient(42% 46% at 72% 92%, rgba(6, 182, 212, 0.20), transparent 64%),
    radial-gradient(30% 34% at 8% 86%, rgba(236, 72, 153, 0.14), transparent 60%);
  filter: blur(46px) saturate(132%);
  animation: ksAurora 38s var(--ks-ease-soft) infinite alternate;
}

.stApp::after {
  content: ""; position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.052) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.052) 1px, transparent 1px);
  background-size: 58px 58px;
  -webkit-mask-image: radial-gradient(130% 90% at 50% 0%, #000 8%, transparent 70%);
  mask-image: radial-gradient(130% 90% at 50% 0%, #000 8%, transparent 70%);
  animation: ksGridPan 44s linear infinite;
}

[data-testid="stAppViewContainer"] > section, .block-container,
[data-testid="stSidebar"], [data-testid="stHeader"] { position: relative; z-index: 1; }

.ks-orbs { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
.ks-orb {
  position: absolute; border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, rgba(165, 180, 252, 0.5), rgba(99, 102, 241, 0));
  animation: ksFloatOrb linear infinite;
}

/* ========================================================================
   3. Keyframe library
   ===================================================================== */
@keyframes ksAurora {
  0%   { transform: translate3d(0, 0, 0) scale(1); }
  33%  { transform: translate3d(2.5%, -2%, 0) scale(1.07) rotate(1.5deg); }
  66%  { transform: translate3d(-2%, 2.5%, 0) scale(1.03) rotate(-1deg); }
  100% { transform: translate3d(1.5%, 1.5%, 0) scale(1.09) rotate(0.8deg); }
}
@keyframes ksGridPan {
  from { background-position: 0 0, 0 0; }
  to   { background-position: 58px 58px, 58px 58px; }
}
@keyframes ksFloatOrb {
  0%   { transform: translate3d(0, 0, 0) scale(1); opacity: 0; }
  12%  { opacity: 0.55; }
  88%  { opacity: 0.4; }
  100% { transform: translate3d(28px, -130px, 0) scale(1.25); opacity: 0; }
}
@keyframes ksRise {
  from { opacity: 0; transform: translate3d(0, 16px, 0); }
  to   { opacity: 1; transform: translate3d(0, 0, 0); }
}
@keyframes ksFade { from { opacity: 0; } to { opacity: 1; } }
@keyframes ksPop {
  0%   { opacity: 0; transform: scale(0.9); filter: blur(5px); }
  60%  { opacity: 1; transform: scale(1.015); filter: blur(0); }
  100% { opacity: 1; transform: scale(1); filter: blur(0); }
}
@keyframes ksTiltIn {
  from { opacity: 0; transform: perspective(900px) rotateX(9deg) translateY(18px); }
  to   { opacity: 1; transform: perspective(900px) rotateX(0) translateY(0); }
}
@keyframes ksSlideInRight {
  from { opacity: 0; transform: translate3d(22px, 0, 0); }
  to   { opacity: 1; transform: translate3d(0, 0, 0); }
}
@keyframes ksGradientSweep {
  0%   { background-position: 0% 50%; }
  50%  { background-position: 100% 50%; }
  100% { background-position: 0% 50%; }
}
@keyframes ksShine {
  0%   { transform: translateX(-130%) skewX(-18deg); opacity: 0; }
  18%  { opacity: 0.85; }
  60%  { opacity: 0.5; }
  100% { transform: translateX(290%) skewX(-18deg); opacity: 0; }
}
@keyframes ksShimmer {
  from { background-position: -420px 0; }
  to   { background-position: 420px 0; }
}
@keyframes ksSpinSlow { to { transform: rotate(360deg); } }
@keyframes ksPulseGlow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.34); }
  50%      { box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }
}
@keyframes ksPulseRing {
  0%   { transform: scale(0.72); opacity: 0.85; }
  100% { transform: scale(2.5); opacity: 0; }
}
@keyframes ksBreathe {
  0%, 100% { opacity: 0.55; transform: scale(0.94); }
  50%      { opacity: 1; transform: scale(1.06); }
}
@keyframes ksShake {
  0%, 100%      { transform: translateX(0); }
  18%, 54%      { transform: translateX(-4px); }
  36%, 72%      { transform: translateX(4px); }
  86%           { transform: translateX(-2px); }
}
@keyframes ksFloat {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-7px); }
}
@keyframes ksCaret { 0%, 48% { opacity: 1; } 50%, 100% { opacity: 0; } }
@keyframes ksDrawRing {
  from { stroke-dashoffset: var(--circ); }
  to   { stroke-dashoffset: var(--off); }
}
@keyframes ksGrowBar { from { width: 0; } to { width: var(--w); } }
@keyframes ksStripes { from { background-position: 0 0; } to { background-position: 34px 0; } }
@keyframes ksBeamX {
  0%   { transform: translateX(-100%); opacity: 0; }
  10%  { opacity: 1; }
  90%  { opacity: 1; }
  100% { transform: translateX(100%); opacity: 0; }
}
@keyframes ksScanLine {
  0%   { top: 0; opacity: 0; }
  10%  { opacity: 1; }
  90%  { opacity: 1; }
  100% { top: 100%; opacity: 0; }
}
@keyframes ksBurst {
  0%   { transform: translate3d(0, 0, 0) scale(1); opacity: 1; }
  100% { transform: translate3d(var(--tx), var(--ty), 0) scale(0.25); opacity: 0; }
}
@keyframes ksDotPulse {
  0%, 80%, 100% { transform: scale(0.65); opacity: 0.45; }
  40%           { transform: scale(1); opacity: 1; }
}
@keyframes ksSheen {
  0%   { left: -40%; }
  100% { left: 140%; }
}
@keyframes ksCountPop {
  0%   { opacity: 0; transform: translateY(8px) scale(0.86); filter: blur(4px); }
  65%  { opacity: 1; transform: translateY(0) scale(1.04); filter: blur(0); }
  100% { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes ksWave {
  0%, 100% { transform: scaleY(0.35); }
  50%      { transform: scaleY(1); }
}
@keyframes ksTick {
  0%   { stroke-dashoffset: 28; }
  100% { stroke-dashoffset: 0; }
}

/* ========================================================================
   4. Entrance utilities (stagger via inline --d)
   ===================================================================== */
.ks-rise  { animation: ksRise 0.56s var(--ks-ease) both; animation-delay: var(--d, 0s); }
.ks-fade  { animation: ksFade 0.6s var(--ks-ease-soft) both; animation-delay: var(--d, 0s); }
.ks-pop   { animation: ksPop 0.52s var(--ks-ease) both; animation-delay: var(--d, 0s); }
.ks-tilt  { animation: ksTiltIn 0.62s var(--ks-ease) both; animation-delay: var(--d, 0s); }
.ks-slide { animation: ksSlideInRight 0.48s var(--ks-ease) both; animation-delay: var(--d, 0s); }

/* ========================================================================
   5. Hero
   ===================================================================== */
.ks-hero {
  position: relative; overflow: hidden; border-radius: 20px; padding: 26px 28px;
  margin-bottom: 22px; border: 1px solid var(--ks-border);
  background:
    linear-gradient(135deg, rgba(99, 102, 241, 0.14), rgba(139, 92, 246, 0.07) 42%, rgba(6, 182, 212, 0.08)),
    var(--ks-surface);
  -webkit-backdrop-filter: blur(14px); backdrop-filter: blur(14px);
  box-shadow: var(--ks-shadow);
  animation: ksTiltIn 0.7s var(--ks-ease) both;
}
.ks-hero::before {
  content: ""; position: absolute; top: 0; left: 0; width: 42%; height: 100%;
  background: linear-gradient(100deg, transparent, rgba(255, 255, 255, 0.11), transparent);
  animation: ksShine 5.5s var(--ks-ease-soft) 0.5s infinite;
  pointer-events: none;
}
.ks-hero::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(60% 120% at 88% -20%, rgba(129, 140, 248, 0.22), transparent 60%);
}
.ks-hero-row { position: relative; z-index: 2; display: flex; align-items: flex-start; justify-content: space-between; gap: 22px; flex-wrap: wrap; }
.ks-hero-left { display: flex; gap: 16px; align-items: center; }
.ks-logo {
  width: 52px; height: 52px; flex: 0 0 52px; border-radius: 15px; display: grid; place-items: center;
  background: linear-gradient(140deg, var(--ks-indigo), var(--ks-violet) 55%, var(--ks-cyan));
  box-shadow: 0 10px 26px -8px rgba(99, 102, 241, 0.75);
  animation: ksFloat 5.5s ease-in-out infinite;
  position: relative;
}
.ks-logo::after {
  content: ""; position: absolute; inset: -5px; border-radius: 19px;
  border: 1px solid rgba(165, 180, 252, 0.35);
  animation: ksBreathe 3.4s ease-in-out infinite;
}
.ks-logo svg { width: 26px; height: 26px; }
.ks-hero h1 {
  margin: 0; font-size: 1.72rem; font-weight: 750; line-height: 1.15;
  background: linear-gradient(96deg, #ffffff 8%, #c7d2fe 32%, #a5f3fc 52%, #ffffff 78%);
  background-size: 260% 100%;
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
  animation: ksGradientSweep 9s ease-in-out infinite;
}
.ks-hero .ks-tag { color: var(--ks-muted); font-size: 0.93rem; margin: 5px 0 0; }
.ks-caret { display: inline-block; width: 8px; color: var(--ks-info); animation: ksCaret 1.15s step-end infinite; }
.ks-hero-right { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }

.ks-chip {
  display: inline-flex; align-items: center; gap: 7px; font-size: 0.74rem; font-weight: 600;
  padding: 6px 12px; border-radius: 999px; white-space: nowrap;
  border: 1px solid var(--ks-border-bright); background: rgba(255, 255, 255, 0.05);
  color: var(--ks-muted); transition: all 0.25s var(--ks-ease-soft);
  animation: ksRise 0.5s var(--ks-ease) both; animation-delay: var(--d, 0s);
}
.ks-chip:hover { transform: translateY(-2px); border-color: rgba(165, 180, 252, 0.5); color: var(--ks-text); }
.ks-chip-accent { color: #c7d2fe; border-color: rgba(99, 102, 241, 0.45); background: rgba(99, 102, 241, 0.14); }
.ks-chip b { color: var(--ks-text); font-weight: 700; }

/* Live heartbeat dot ---------------------------------------------------- */
.ks-live { position: relative; width: 7px; height: 7px; border-radius: 50%; background: var(--ks-ok); flex: 0 0 7px; }
.ks-live::after {
  content: ""; position: absolute; inset: 0; border-radius: 50%; background: var(--ks-ok);
  animation: ksPulseRing 2s var(--ks-ease-soft) infinite;
}

/* ========================================================================
   6. Section headers
   ===================================================================== */
.ks-section {
  display: flex; align-items: baseline; gap: 12px; margin: 26px 0 13px;
  animation: ksFade 0.5s var(--ks-ease-soft) both;
}
.ks-section .ks-label {
  color: var(--ks-muted); font-size: 0.73rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.1em; white-space: nowrap;
}
.ks-section .ks-rule {
  flex: 1; height: 1px;
  background: linear-gradient(90deg, var(--ks-border-bright), transparent);
}
.ks-section .ks-hint { color: var(--ks-dim); font-size: 0.78rem; }

/* ========================================================================
   7. Cards & provider grid
   ===================================================================== */
.ks-card {
  position: relative; overflow: hidden; border-radius: var(--ks-radius);
  border: 1px solid var(--ks-border); background: var(--ks-surface);
  -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  padding: 15px 16px 13px; margin-bottom: 9px;
  transition: transform 0.32s var(--ks-ease), border-color 0.32s var(--ks-ease-soft),
              box-shadow 0.32s var(--ks-ease-soft), background 0.32s var(--ks-ease-soft);
  animation: ksRise 0.56s var(--ks-ease) both; animation-delay: var(--d, 0s);
  --accent: #6366f1;
}
.ks-card:hover {
  transform: translateY(-4px);
  border-color: color-mix(in srgb, var(--accent) 48%, transparent);
  background: var(--ks-surface-2);
  box-shadow: 0 20px 44px -22px rgba(0, 0, 0, 0.85),
              0 0 0 1px color-mix(in srgb, var(--accent) 22%, transparent),
              0 0 32px -14px color-mix(in srgb, var(--accent) 60%, transparent);
}
/* Travelling beam along the top edge on hover */
.ks-card::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0; transition: opacity 0.3s var(--ks-ease-soft);
}
.ks-card:hover::before { opacity: 1; animation: ksBeamX 1.5s var(--ks-ease-soft) infinite; }
/* Corner glow that blooms from the accent colour */
.ks-card::after {
  content: ""; position: absolute; top: -40%; right: -20%; width: 180px; height: 180px;
  border-radius: 50%; pointer-events: none;
  background: radial-gradient(circle, color-mix(in srgb, var(--accent) 26%, transparent), transparent 68%);
  opacity: 0; transition: opacity 0.4s var(--ks-ease-soft);
}
.ks-card:hover::after { opacity: 1; }

.ks-card-top { display: flex; align-items: center; gap: 11px; }
.ks-avatar {
  width: 36px; height: 36px; flex: 0 0 36px; border-radius: 11px; display: grid; place-items: center;
  font-size: 0.74rem; font-weight: 750; letter-spacing: 0.02em; color: #08090d;
  background: linear-gradient(140deg, var(--accent), color-mix(in srgb, var(--accent) 45%, #ffffff));
  box-shadow: 0 6px 16px -7px color-mix(in srgb, var(--accent) 85%, transparent);
  transition: transform 0.34s var(--ks-ease);
}
.ks-card:hover .ks-avatar { transform: rotate(-7deg) scale(1.08); }
.ks-card-name { font-weight: 650; font-size: 0.96rem; color: var(--ks-text); line-height: 1.2; }
.ks-card-cat { color: var(--ks-dim); font-size: 0.72rem; margin-top: 2px; }
.ks-card-meta {
  color: var(--ks-dim); font-size: 0.75rem; margin-top: 10px;
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
}
.ks-card-body { margin-top: 11px; }

/* ========================================================================
   8. Status pills, dots and tone system
   ===================================================================== */
.ks-pill {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 0.71rem; font-weight: 650; padding: 4px 10px; border-radius: 999px;
  border: 1px solid transparent; white-space: nowrap; line-height: 1.5;
}
.ks-pill-ok   { color: var(--ks-ok);   background: rgba(16, 185, 129, 0.12); border-color: rgba(16, 185, 129, 0.34); animation: ksPulseGlow 2.6s var(--ks-ease-soft) infinite; }
.ks-pill-bad  { color: var(--ks-bad);  background: rgba(239, 68, 68, 0.12);  border-color: rgba(239, 68, 68, 0.34);  animation: ksShake 0.5s var(--ks-ease-soft) 1; }
.ks-pill-warn { color: var(--ks-warn); background: rgba(245, 158, 11, 0.12); border-color: rgba(245, 158, 11, 0.34); }
.ks-pill-info { color: var(--ks-info); background: rgba(99, 102, 241, 0.13);  border-color: rgba(99, 102, 241, 0.36); }
.ks-pill-idle { color: var(--ks-idle); background: rgba(148, 163, 184, 0.09); border-color: rgba(148, 163, 184, 0.22); }

.ks-dot { width: 6px; height: 6px; border-radius: 50%; flex: 0 0 6px; background: currentColor; position: relative; }
.ks-pill-ok .ks-dot::after {
  content: ""; position: absolute; inset: 0; border-radius: 50%; background: currentColor;
  animation: ksPulseRing 2.1s var(--ks-ease-soft) infinite;
}
.ks-pill-warn .ks-dot { animation: ksBreathe 1.9s ease-in-out infinite; }
.ks-pill-idle .ks-dot { opacity: 0.6; }

/* ========================================================================
   9. Metric tiles & ring gauge
   ===================================================================== */
.ks-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 10px; }
.ks-metric {
  position: relative; overflow: hidden; border-radius: var(--ks-radius);
  border: 1px solid var(--ks-border); background: var(--ks-surface);
  -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  padding: 14px 16px; transition: transform 0.3s var(--ks-ease), border-color 0.3s var(--ks-ease-soft);
  animation: ksRise 0.52s var(--ks-ease) both; animation-delay: var(--d, 0s);
}
.ks-metric:hover { transform: translateY(-3px); border-color: var(--ks-border-bright); }
.ks-metric::after {
  content: ""; position: absolute; left: 0; bottom: 0; height: 2px; width: 100%;
  background: linear-gradient(90deg, var(--tone, var(--ks-indigo)), transparent);
  transform-origin: left; animation: ksGrowBar 0.9s var(--ks-ease) both; animation-delay: var(--d, 0s);
  --w: 100%;
}
.ks-metric .ks-m-label {
  color: var(--ks-muted); font-size: 0.72rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.07em;
}
.ks-metric .ks-m-value {
  font-size: 1.62rem; font-weight: 750; color: var(--ks-text); line-height: 1.15; margin-top: 7px;
  font-variant-numeric: tabular-nums;
  animation: ksCountPop 0.62s var(--ks-ease) both; animation-delay: calc(var(--d, 0s) + 0.1s);
}
.ks-metric .ks-m-value.ks-tone { color: var(--tone); }
.ks-metric .ks-m-sub { color: var(--ks-dim); font-size: 0.73rem; margin-top: 3px; }

.ks-gauge-wrap {
  display: flex; align-items: center; gap: 20px; padding: 18px 20px;
  border-radius: var(--ks-radius); border: 1px solid var(--ks-border);
  background: var(--ks-surface); -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  animation: ksPop 0.6s var(--ks-ease) both;
  position: relative; overflow: hidden;
}
.ks-gauge-wrap::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(70% 120% at 0% 50%, color-mix(in srgb, var(--tone) 14%, transparent), transparent 62%);
}
.ks-gauge { position: relative; width: 124px; height: 124px; flex: 0 0 124px; }
.ks-gauge svg { width: 100%; height: 100%; transform: rotate(-90deg); }
.ks-gauge .ks-ring-bg { fill: none; stroke: rgba(148, 163, 184, 0.14); stroke-width: 9; }
.ks-gauge .ks-ring-fg {
  fill: none; stroke: var(--tone); stroke-width: 9; stroke-linecap: round;
  stroke-dasharray: var(--circ); stroke-dashoffset: var(--off);
  filter: drop-shadow(0 0 7px color-mix(in srgb, var(--tone) 60%, transparent));
  animation: ksDrawRing 1.25s var(--ks-ease) 0.18s both;
}
.ks-gauge .ks-ring-spin {
  fill: none; stroke: color-mix(in srgb, var(--tone) 35%, transparent);
  stroke-width: 1.5; stroke-dasharray: 3 9;
}
.ks-gauge-center {
  position: absolute; inset: 0; display: grid; place-items: center; text-align: center;
}
.ks-gauge-center .ks-g-num {
  font-size: 1.86rem; font-weight: 780; color: var(--ks-text); font-variant-numeric: tabular-nums;
  animation: ksCountPop 0.7s var(--ks-ease) 0.3s both; line-height: 1;
}
.ks-gauge-center .ks-g-unit { font-size: 0.68rem; color: var(--ks-dim); letter-spacing: 0.08em; text-transform: uppercase; margin-top: 3px; }
.ks-gauge-text { min-width: 0; position: relative; z-index: 1; }
.ks-gauge-text h4 { margin: 0 0 5px; font-size: 1.02rem; }
.ks-gauge-text p { margin: 0; color: var(--ks-muted); font-size: 0.85rem; line-height: 1.55; }
.ks-gauge-legend { display: flex; gap: 8px; margin-top: 11px; flex-wrap: wrap; }

/* ========================================================================
   10. Latency bars
   ===================================================================== */
.ks-bars { display: flex; flex-direction: column; gap: 9px; }
.ks-bar-row {
  display: grid; grid-template-columns: 136px 1fr 74px; align-items: center; gap: 12px;
  animation: ksSlideInRight 0.5s var(--ks-ease) both; animation-delay: var(--d, 0s);
}
.ks-bar-name { font-size: 0.82rem; color: var(--ks-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ks-bar-track {
  height: 8px; border-radius: 999px; background: rgba(148, 163, 184, 0.1); overflow: hidden; position: relative;
}
.ks-bar-fill {
  height: 100%; border-radius: 999px; width: var(--w);
  background: linear-gradient(90deg, color-mix(in srgb, var(--tone) 55%, transparent), var(--tone));
  animation: ksGrowBar 1.05s var(--ks-ease) both; animation-delay: calc(var(--d, 0s) + 0.1s);
  position: relative; overflow: hidden;
}
.ks-bar-fill::after {
  content: ""; position: absolute; top: 0; bottom: 0; left: -40%; width: 40%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent);
  animation: ksSheen 2.4s var(--ks-ease-soft) 1.2s infinite;
}
.ks-bar-val { font-size: 0.78rem; color: var(--ks-muted); text-align: right; font-variant-numeric: tabular-nums; }

/* Mini equaliser used as a "working" indicator -------------------------- */
.ks-eq { display: inline-flex; align-items: flex-end; gap: 2px; height: 11px; }
.ks-eq i {
  width: 2.5px; height: 100%; border-radius: 2px; background: var(--ks-info); display: block;
  transform-origin: bottom; animation: ksWave 1.05s ease-in-out infinite;
}
.ks-eq i:nth-child(2) { animation-delay: 0.14s; }
.ks-eq i:nth-child(3) { animation-delay: 0.28s; }
.ks-eq i:nth-child(4) { animation-delay: 0.42s; }

/* ========================================================================
   11. Results table
   ===================================================================== */
.ks-table-wrap {
  border-radius: var(--ks-radius); border: 1px solid var(--ks-border); overflow: hidden;
  background: var(--ks-surface); -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  animation: ksRise 0.6s var(--ks-ease) both;
}
.ks-table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
.ks-table thead th {
  text-align: left; padding: 11px 14px; color: var(--ks-muted);
  font-size: 0.69rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em;
  background: rgba(255, 255, 255, 0.03); border-bottom: 1px solid var(--ks-border);
  white-space: nowrap;
}
.ks-table tbody tr {
  border-bottom: 1px solid rgba(148, 163, 184, 0.08);
  animation: ksRise 0.48s var(--ks-ease) both; animation-delay: var(--d, 0s);
  transition: background 0.22s var(--ks-ease-soft);
}
.ks-table tbody tr:last-child { border-bottom: none; }
.ks-table tbody tr:hover { background: rgba(255, 255, 255, 0.04); }
.ks-table td { padding: 12px 14px; color: var(--ks-text); vertical-align: middle; }
.ks-table .ks-td-name { display: flex; align-items: center; gap: 9px; font-weight: 600; white-space: nowrap; }
.ks-table .ks-avatar-sm {
  width: 25px; height: 25px; flex: 0 0 25px; border-radius: 8px; display: grid; place-items: center;
  font-size: 0.6rem; font-weight: 750; color: #08090d;
  background: linear-gradient(140deg, var(--accent), color-mix(in srgb, var(--accent) 45%, #ffffff));
}
.ks-table .ks-td-detail { color: var(--ks-muted); font-size: 0.8rem; max-width: 320px; }
.ks-table .ks-td-mono { font-size: 0.76rem; color: var(--ks-dim); white-space: nowrap; }
.ks-mini-track { height: 5px; width: 54px; border-radius: 999px; background: rgba(148, 163, 184, 0.13); overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 8px; }
.ks-mini-fill {
  display: block; height: 100%; border-radius: 999px; width: var(--w); background: var(--tone);
  animation: ksGrowBar 0.9s var(--ks-ease) both; animation-delay: calc(var(--d, 0s) + 0.15s);
}

/* ========================================================================
   12. Scanner: progress strip, skeletons, scan panel
   ===================================================================== */
.ks-scan {
  position: relative; overflow: hidden; border-radius: var(--ks-radius);
  border: 1px solid rgba(99, 102, 241, 0.3); padding: 18px 20px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.1), rgba(139, 92, 246, 0.05)), var(--ks-surface);
  -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
  animation: ksPop 0.4s var(--ks-ease) both;
}
.ks-scan::after {
  content: ""; position: absolute; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, transparent, rgba(165, 180, 252, 0.9), transparent);
  animation: ksScanLine 2.1s var(--ks-ease-soft) infinite; pointer-events: none;
}
.ks-scan-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
.ks-scan-title { display: flex; align-items: center; gap: 10px; font-weight: 650; font-size: 0.97rem; }
.ks-scan-count { color: var(--ks-muted); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
.ks-spinner {
  width: 17px; height: 17px; flex: 0 0 17px; border-radius: 50%;
  border: 2px solid rgba(165, 180, 252, 0.25); border-top-color: var(--ks-info);
  animation: ksSpinSlow 0.75s linear infinite;
}
.ks-track {
  height: 7px; border-radius: 999px; background: rgba(148, 163, 184, 0.13);
  overflow: hidden; margin: 14px 0 13px; position: relative;
}
.ks-track-fill {
  height: 100%; border-radius: 999px; width: var(--w);
  background: linear-gradient(90deg, var(--ks-indigo), var(--ks-violet), var(--ks-cyan));
  background-size: 200% 100%;
  transition: width 0.5s var(--ks-ease);
  animation: ksGradientSweep 2.6s linear infinite;
  position: relative; overflow: hidden;
}
.ks-track-fill::after {
  content: ""; position: absolute; inset: 0;
  background-image: linear-gradient(115deg, rgba(255, 255, 255, 0.22) 25%, transparent 25%,
    transparent 50%, rgba(255, 255, 255, 0.22) 50%, rgba(255, 255, 255, 0.22) 75%, transparent 75%);
  background-size: 34px 34px; animation: ksStripes 0.75s linear infinite;
}
.ks-scan-chips { display: flex; flex-wrap: wrap; gap: 7px; }
.ks-scan-chip {
  display: inline-flex; align-items: center; gap: 7px; font-size: 0.75rem; font-weight: 600;
  padding: 5px 11px; border-radius: 999px; border: 1px solid var(--ks-border);
  background: rgba(255, 255, 255, 0.04); color: var(--ks-muted);
  animation: ksPop 0.4s var(--ks-ease) both; animation-delay: var(--d, 0s);
  transition: all 0.3s var(--ks-ease-soft);
}
.ks-scan-chip.is-done { color: var(--ks-ok); border-color: rgba(16, 185, 129, 0.35); background: rgba(16, 185, 129, 0.1); }
.ks-scan-chip.is-pending .ks-dots { display: inline-flex; gap: 2.5px; }
.ks-scan-chip.is-pending .ks-dots i {
  width: 3.5px; height: 3.5px; border-radius: 50%; background: var(--ks-info); display: block;
  animation: ksDotPulse 1.3s ease-in-out infinite;
}
.ks-scan-chip.is-pending .ks-dots i:nth-child(2) { animation-delay: 0.18s; }
.ks-scan-chip.is-pending .ks-dots i:nth-child(3) { animation-delay: 0.36s; }
.ks-check { width: 11px; height: 11px; flex: 0 0 11px; }
.ks-check path { stroke: var(--ks-ok); stroke-width: 2.6; fill: none; stroke-linecap: round; stroke-dasharray: 28; animation: ksTick 0.45s var(--ks-ease) both; }

.ks-skel {
  border-radius: 10px; background: linear-gradient(90deg,
    rgba(148, 163, 184, 0.07) 25%, rgba(148, 163, 184, 0.16) 37%, rgba(148, 163, 184, 0.07) 63%);
  background-size: 420px 100%; animation: ksShimmer 1.3s linear infinite;
}

/* ========================================================================
   13. Detector result
   ===================================================================== */
.ks-verdict {
  position: relative; overflow: hidden; border-radius: 18px; padding: 22px 24px;
  border: 1px solid color-mix(in srgb, var(--tone) 38%, transparent);
  background: linear-gradient(135deg, color-mix(in srgb, var(--tone) 13%, transparent), transparent 55%), var(--ks-surface);
  -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
  animation: ksTiltIn 0.62s var(--ks-ease) both;
}
.ks-verdict::before {
  content: ""; position: absolute; top: 0; left: 0; width: 38%; height: 100%;
  background: linear-gradient(100deg, transparent, color-mix(in srgb, var(--tone) 18%, transparent), transparent);
  animation: ksShine 3.4s var(--ks-ease-soft) 0.3s 2; pointer-events: none;
}
.ks-verdict::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(55% 110% at 92% 0%, color-mix(in srgb, var(--tone) 18%, transparent), transparent 62%);
}
.ks-verdict-row { position: relative; z-index: 2; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.ks-verdict-icon {
  width: 56px; height: 56px; flex: 0 0 56px; border-radius: 17px; display: grid; place-items: center;
  background: color-mix(in srgb, var(--tone) 17%, transparent);
  border: 1px solid color-mix(in srgb, var(--tone) 42%, transparent);
  animation: ksPop 0.55s var(--ks-ease) 0.12s both; position: relative;
}
.ks-verdict-icon::after {
  content: ""; position: absolute; inset: -1px; border-radius: 17px;
  border: 1px solid color-mix(in srgb, var(--tone) 55%, transparent);
  animation: ksPulseRing 2.4s var(--ks-ease-soft) infinite;
}
.ks-verdict-icon svg { width: 26px; height: 26px; stroke: var(--tone); fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.ks-verdict-main { flex: 1; min-width: 190px; }
.ks-verdict-main .ks-vt { color: var(--ks-dim); font-size: 0.71rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; }
.ks-verdict-main h3 { margin: 4px 0 8px; font-size: 1.32rem; animation: ksCountPop 0.6s var(--ks-ease) 0.2s both; }
.ks-verdict-detail { position: relative; z-index: 2; color: var(--ks-muted); font-size: 0.88rem; line-height: 1.62; margin-top: 15px; padding-top: 15px; border-top: 1px solid var(--ks-border); animation: ksFade 0.6s var(--ks-ease-soft) 0.35s both; }
.ks-conf { text-align: center; animation: ksPop 0.5s var(--ks-ease) 0.22s both; }
.ks-conf .ks-conf-v { font-size: 1.28rem; font-weight: 750; color: var(--tone); }
.ks-conf .ks-conf-l { font-size: 0.68rem; color: var(--ks-dim); text-transform: uppercase; letter-spacing: 0.08em; }

/* Candidate probe list -------------------------------------------------- */
.ks-probe { display: flex; flex-direction: column; gap: 7px; }
.ks-probe-row {
  display: flex; align-items: center; gap: 11px; padding: 10px 13px; border-radius: 11px;
  border: 1px solid var(--ks-border); background: var(--ks-surface); font-size: 0.83rem;
  animation: ksSlideInRight 0.46s var(--ks-ease) both; animation-delay: var(--d, 0s);
  transition: border-color 0.26s var(--ks-ease-soft), transform 0.26s var(--ks-ease);
}
.ks-probe-row:hover { transform: translateX(3px); border-color: var(--ks-border-bright); }
.ks-probe-name { font-weight: 600; min-width: 128px; }
.ks-probe-detail { color: var(--ks-muted); flex: 1; min-width: 0; font-size: 0.8rem; }

/* ========================================================================
   14. Catalog
   ===================================================================== */
.ks-cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(258px, 1fr)); gap: 10px; }
.ks-cat-card {
  position: relative; overflow: hidden; border-radius: 13px; padding: 13px 15px;
  border: 1px solid var(--ks-border); background: var(--ks-surface);
  animation: ksPop 0.46s var(--ks-ease) both; animation-delay: var(--d, 0s);
  transition: transform 0.3s var(--ks-ease), border-color 0.3s var(--ks-ease-soft), box-shadow 0.3s var(--ks-ease-soft);
  --accent: #6366f1;
}
.ks-cat-card:hover {
  transform: translateY(-3px) scale(1.012);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 16px 34px -20px rgba(0, 0, 0, 0.8), 0 0 26px -16px color-mix(in srgb, var(--accent) 70%, transparent);
}
.ks-cat-card .ks-cc-top { display: flex; align-items: center; gap: 10px; margin-bottom: 9px; }
.ks-cat-card .ks-cc-name { font-weight: 650; font-size: 0.9rem; }
.ks-cat-card .ks-cc-row { display: flex; gap: 7px; align-items: baseline; font-size: 0.74rem; margin-top: 5px; }
.ks-cat-card .ks-cc-key { color: var(--ks-dim); min-width: 56px; }
.ks-cat-card .ks-cc-val { color: var(--ks-muted); word-break: break-word; }
.ks-pattern {
  font-size: 0.71rem; color: #a5b4fc; background: rgba(99, 102, 241, 0.1);
  border: 1px solid rgba(99, 102, 241, 0.2); border-radius: 6px; padding: 1px 6px; word-break: break-all;
}

/* ========================================================================
   15. Success burst & empty state
   ===================================================================== */
.ks-burst { position: relative; height: 0; }
.ks-burst i {
  position: absolute; left: 50%; top: 0; width: 6px; height: 6px; border-radius: 2px;
  background: var(--c); animation: ksBurst 1.15s var(--ks-ease) both; animation-delay: var(--d);
}
.ks-empty {
  text-align: center; padding: 46px 24px; border-radius: var(--ks-radius);
  border: 1px dashed var(--ks-border-bright); background: rgba(255, 255, 255, 0.018);
  animation: ksPop 0.6s var(--ks-ease) both;
}
.ks-empty-icon {
  width: 58px; height: 58px; margin: 0 auto 15px; border-radius: 18px; display: grid; place-items: center;
  background: linear-gradient(140deg, rgba(99, 102, 241, 0.2), rgba(6, 182, 212, 0.14));
  border: 1px solid var(--ks-border-bright); animation: ksFloat 4.4s ease-in-out infinite;
}
.ks-empty-icon svg { width: 26px; height: 26px; stroke: #a5b4fc; fill: none; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.ks-empty h4 { margin: 0 0 7px; font-size: 1.03rem; }
.ks-empty p { margin: 0 auto; max-width: 430px; color: var(--ks-muted); font-size: 0.86rem; line-height: 1.6; }

.ks-note {
  border-radius: 12px; padding: 13px 15px; font-size: 0.83rem; line-height: 1.62;
  border: 1px solid var(--ks-border); background: var(--ks-surface); color: var(--ks-muted);
  animation: ksFade 0.5s var(--ks-ease-soft) both; animation-delay: var(--d, 0s);
  border-left: 2px solid var(--tone, var(--ks-indigo));
}
.ks-note b, .ks-note strong { color: var(--ks-text); }
.ks-foot { color: var(--ks-dim); font-size: 0.79rem; line-height: 1.6; margin-top: 16px; animation: ksFade 0.7s var(--ks-ease-soft) 0.2s both; }

/* ========================================================================
   16. Streamlit widget overrides
   ===================================================================== */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  position: relative; overflow: hidden; border-radius: 10px; font-weight: 600; font-size: 0.84rem;
  border: 1px solid var(--ks-border-bright); background: rgba(255, 255, 255, 0.045);
  color: var(--ks-text);
  transition: transform 0.2s var(--ks-ease), border-color 0.25s var(--ks-ease-soft),
              background 0.25s var(--ks-ease-soft), box-shadow 0.25s var(--ks-ease-soft);
}
.stButton > button::after, .stDownloadButton > button::after, .stFormSubmitButton > button::after {
  content: ""; position: absolute; top: 0; bottom: 0; left: -45%; width: 42%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.17), transparent);
  transform: skewX(-18deg); opacity: 0; transition: opacity 0.2s;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
  transform: translateY(-2px); border-color: rgba(129, 140, 248, 0.55);
  background: rgba(99, 102, 241, 0.14);
  box-shadow: 0 10px 24px -14px rgba(99, 102, 241, 0.9);
}
.stButton > button:hover::after, .stDownloadButton > button:hover::after, .stFormSubmitButton > button:hover::after {
  opacity: 1; animation: ksSheen 0.85s var(--ks-ease-soft);
}
.stButton > button:active, .stFormSubmitButton > button:active { transform: translateY(0) scale(0.985); }
.stButton > button:disabled { opacity: 0.4; transform: none; box-shadow: none; }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: linear-gradient(135deg, var(--ks-indigo), var(--ks-violet));
  border-color: transparent; color: #fff;
  box-shadow: 0 10px 26px -14px rgba(99, 102, 241, 0.95);
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {
  box-shadow: 0 14px 34px -14px rgba(139, 92, 246, 1); filter: brightness(1.08);
}
/* Pull a provider card's action button snugly under the card (progressive) */
[data-testid="stElementContainer"]:has(.ks-card) + [data-testid="stElementContainer"] .stButton > button {
  margin-top: -4px;
}

[data-testid="stSidebar"] {
  background: rgba(10, 13, 22, 0.78);
  -webkit-backdrop-filter: blur(18px); backdrop-filter: blur(18px);
  border-right: 1px solid var(--ks-border);
}
[data-testid="stSidebar"] .block-container { padding-top: 1.3rem; }

[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
  border-radius: 10px !important; background: rgba(255, 255, 255, 0.04) !important;
  border: 1px solid var(--ks-border) !important; color: var(--ks-text) !important;
  font-size: 0.86rem !important;
  transition: border-color 0.24s var(--ks-ease-soft), box-shadow 0.24s var(--ks-ease-soft), background 0.24s;
}
[data-testid="stTextInput"] input:focus, [data-testid="stTextArea"] textarea:focus {
  border-color: rgba(129, 140, 248, 0.7) !important;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.18) !important;
  background: rgba(255, 255, 255, 0.06) !important;
}
[data-testid="stTextInput"] label, [data-testid="stMultiSelect"] label, [data-testid="stSelectbox"] label {
  font-size: 0.79rem !important; color: var(--ks-muted) !important; font-weight: 600 !important;
}

[data-baseweb="tag"] {
  border-radius: 7px !important;
  background: rgba(99, 102, 241, 0.2) !important;
  animation: ksPop 0.32s var(--ks-ease) both;
}

.stTabs [data-baseweb="tab-list"] {
  gap: 2px; border-bottom: 1px solid var(--ks-border); background: transparent;
}
.stTabs [data-baseweb="tab"] {
  padding: 9px 17px; border-radius: 9px 9px 0 0; font-size: 0.87rem; font-weight: 600;
  color: var(--ks-muted); transition: color 0.22s var(--ks-ease-soft), background 0.22s var(--ks-ease-soft);
}
.stTabs [data-baseweb="tab"]:hover { color: var(--ks-text); background: rgba(255, 255, 255, 0.04); }
.stTabs [aria-selected="true"] { color: var(--ks-text) !important; }
.stTabs [data-baseweb="tab-highlight"] {
  background: linear-gradient(90deg, var(--ks-indigo), var(--ks-violet), var(--ks-cyan)) !important;
  height: 2px !important; border-radius: 2px;
}
.stTabs [data-baseweb="tab-panel"] { animation: ksRise 0.45s var(--ks-ease) both; padding-top: 6px; }

[data-testid="stExpander"] {
  border-radius: 12px !important; border: 1px solid var(--ks-border) !important;
  background: var(--ks-surface) !important; overflow: hidden;
}
[data-testid="stExpander"] summary { font-size: 0.85rem; font-weight: 600; transition: background 0.22s; }
[data-testid="stExpander"] summary:hover { background: rgba(255, 255, 255, 0.04); }

[data-testid="stDataFrame"] { border-radius: 11px; overflow: hidden; border: 1px solid var(--ks-border); }
hr, [data-testid="stDivider"] { border-color: var(--ks-border) !important; }
[data-testid="stAlert"] { border-radius: 11px; animation: ksRise 0.42s var(--ks-ease) both; }
[data-testid="stSpinner"] > div { border-top-color: var(--ks-info) !important; }
[data-testid="stToast"] { animation: ksSlideInRight 0.4s var(--ks-ease) both; }

/* ========================================================================
   17. Reduced motion -- one switch disables every animation above
   ===================================================================== */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    animation-delay: 0ms !important;
    transition-duration: 0.001ms !important;
  }
  .stApp::before, .stApp::after, .ks-orbs, .ks-hero::before, .ks-scan::after { animation: none !important; }
  .ks-orbs { display: none; }
  .ks-gauge .ks-ring-fg { stroke-dashoffset: var(--off); }
  .ks-bar-fill, .ks-mini-fill, .ks-track-fill { width: var(--w); }
}

/* ========================================================================
   18. Narrow viewports
   ===================================================================== */
@media (max-width: 760px) {
  .ks-hero { padding: 20px; }
  .ks-hero h1 { font-size: 1.36rem; }
  .ks-gauge-wrap { flex-direction: column; align-items: flex-start; }
  .ks-bar-row { grid-template-columns: 96px 1fr 62px; gap: 8px; }
  .ks-table .ks-td-detail { max-width: 180px; }
}
</style>
"""

# Ambient floating orbs. Fixed values keep renders deterministic.
_ORBS = [
    (6, 18, 78, 26, 0), (17, 72, 54, 31, 3), (29, 36, 96, 23, 7),
    (44, 86, 62, 29, 1), (58, 12, 108, 35, 5), (71, 64, 70, 27, 9),
    (83, 28, 86, 33, 2), (93, 78, 58, 24, 6),
]


def inject() -> str:
    """The stylesheet plus the ambient backdrop layer.

    Call once per script run, before any other markup.
    """
    orbs = "".join(
        f'<span class="ks-orb" style="left:{left}%;top:{top}%;width:{size}px;'
        f'height:{size}px;animation-duration:{duration}s;animation-delay:{delay}s"></span>'
        for left, top, size, duration, delay in _ORBS
    )
    return f'{CSS}<div class="ks-orbs" aria-hidden="true">{orbs}</div>'


# ---------------------------------------------------------------------------
# Small shared pieces
# ---------------------------------------------------------------------------

_LOCK_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="4" y="10.5" width="16" height="11" rx="2.5"></rect>'
    '<path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"></path>'
    '<circle cx="12" cy="16" r="1.4" fill="#fff" stroke="none"></circle></svg>'
)

_VERDICT_ICONS = {
    TONE_OK: '<path d="M4 12.5l5 5L20 6.5"></path>',
    TONE_BAD: '<path d="M6 6l12 12M18 6L6 18"></path>',
    TONE_WARN: '<path d="M12 4l9 16H3z"></path><path d="M12 10v4.5M12 17.4v.2"></path>',
    TONE_INFO: '<circle cx="12" cy="12" r="9"></circle><path d="M12 11v6M12 7.6v.2"></path>',
    TONE_IDLE: '<circle cx="12" cy="12" r="9"></circle><path d="M9.2 9.6a3 3 0 1 1 4.3 3c-.9.7-1.5 1.2-1.5 2.4"></path><path d="M12 17.8v.2"></path>',
}

_TICK_SVG = ('<svg class="ks-check" viewBox="0 0 24 24" aria-hidden="true">'
             '<path d="M4 12.5l5 5L20 6.5"></path></svg>')


def pill(label: str, tone: str = TONE_IDLE) -> str:
    """A status pill. ``ok`` pulses, ``bad`` shakes once, ``warn`` breathes."""
    return (f'<span class="ks-pill ks-pill-{esc(tone)}">'
            f'<span class="ks-dot"></span>{esc(label)}</span>')


def section(label: str, hint: str = "") -> str:
    """A section header with a fading rule and optional right-aligned hint."""
    tail = f'<span class="ks-hint">{esc(hint)}</span>' if hint else ""
    return (f'<div class="ks-section"><span class="ks-label">{esc(label)}</span>'
            f'<span class="ks-rule"></span>{tail}</div>')


def note(body_html: str, tone: str = TONE_INFO, delay: float = 0.0) -> str:
    """A bordered note. ``body_html`` is trusted markup built by the caller."""
    return (f'<div class="ks-note" style="--tone:{TONE_HEX.get(tone, TONE_HEX[TONE_INFO])};'
            f'--d:{delay}s">{body_html}</div>')


def footnote(text: str) -> str:
    return f'<div class="ks-foot">{esc(text)}</div>'


def spacer(height: int = 10) -> str:
    return f'<div style="height:{height}px"></div>'


# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------

def hero(chips: Sequence[tuple[str, bool]]) -> str:
    """The masthead: floating logo, gradient-sweep title, animated stat chips.

    ``chips`` is a sequence of ``(html_label, accent)`` pairs.
    """
    rendered = "".join(
        f'<span class="ks-chip{" ks-chip-accent" if accent else ""}" '
        f'style="--d:{0.18 + i * 0.07:.2f}s">{label}</span>'
        for i, (label, accent) in enumerate(chips)
    )
    return (
        '<div class="ks-hero">'
        '<div class="ks-hero-row">'
        '<div class="ks-hero-left">'
        f'<div class="ks-logo">{_LOCK_SVG}</div>'
        '<div>'
        '<h1>Keyscope</h1>'
        '<p class="ks-tag">Verify that your API credentials are live, straight from '
        'each provider<span class="ks-caret">_</span></p>'
        '</div></div>'
        f'<div class="ks-hero-right">{rendered}</div>'
        '</div></div>'
    )


def sidebar_brand(provider_count: int) -> str:
    return (
        '<div style="display:flex;align-items:center;gap:11px;padding:2px 0 14px;'
        'border-bottom:1px solid var(--ks-border);margin-bottom:16px" class="ks-fade">'
        '<div style="width:34px;height:34px;flex:0 0 34px;border-radius:10px;display:grid;'
        'place-items:center;background:linear-gradient(140deg,#6366f1,#8b5cf6 55%,#06b6d4);'
        'box-shadow:0 7px 18px -8px rgba(99,102,241,.8)">'
        '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="#fff" '
        'stroke-width="2.1" stroke-linecap="round"><rect x="4" y="10.5" width="16" height="11" '
        'rx="2.5"></rect><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"></path></svg></div>'
        '<div><div style="font-weight:700;font-size:0.95rem;letter-spacing:-0.01em">Keyscope</div>'
        f'<div style="color:var(--ks-dim);font-size:0.71rem">{provider_count} providers supported</div>'
        '</div></div>'
    )


# ---------------------------------------------------------------------------
# Provider cards
# ---------------------------------------------------------------------------

def provider_card(name: str, mark: str, category: str, accent: str,
                  status: str, tone: str, meta: str, delay: float = 0.0,
                  extra_html: str = "") -> str:
    """One provider tile: gradient avatar, status pill, metadata line."""
    return (
        f'<div class="ks-card" style="--accent:{esc(accent)};--d:{delay:.2f}s">'
        '<div class="ks-card-top">'
        f'<div class="ks-avatar">{esc(mark)}</div>'
        f'<div style="min-width:0"><div class="ks-card-name">{esc(name)}</div>'
        f'<div class="ks-card-cat">{esc(category)}</div></div>'
        '</div>'
        f'<div class="ks-card-body">{pill(status, tone)}</div>'
        f'<div class="ks-card-meta">{meta}</div>'
        f'{extra_html}</div>'
    )


# ---------------------------------------------------------------------------
# Metrics & gauge
# ---------------------------------------------------------------------------

def metrics(items: Sequence[tuple[str, str, str, str]]) -> str:
    """A responsive tile row. Items are ``(label, value, sub, tone)``."""
    cells = []
    for i, (label, value, sub, tone) in enumerate(items):
        hex_tone = TONE_HEX.get(tone, TONE_HEX[TONE_INFO])
        tone_cls = " ks-tone" if tone in (TONE_OK, TONE_BAD, TONE_WARN) else ""
        cells.append(
            f'<div class="ks-metric" style="--tone:{hex_tone};--d:{i * 0.08:.2f}s">'
            f'<div class="ks-m-label">{esc(label)}</div>'
            f'<div class="ks-m-value{tone_cls}">{esc(value)}</div>'
            f'<div class="ks-m-sub">{esc(sub)}</div></div>'
        )
    return f'<div class="ks-metrics">{"".join(cells)}</div>'


def health_gauge(score: int, headline: str, body: str, tone: str,
                 legend: Sequence[tuple[str, str]] = ()) -> str:
    """An SVG ring that draws itself from 0 to ``score`` percent."""
    score = max(0, min(100, int(score)))
    offset = round(RING_CIRCUMFERENCE * (1 - score / 100), 2)
    hex_tone = TONE_HEX.get(tone, TONE_HEX[TONE_INFO])
    chips = "".join(
        f'<span class="ks-chip" style="--d:{0.5 + i * 0.07:.2f}s">'
        f'<span class="ks-dot" style="color:{esc(colour)}"></span>{esc(text)}</span>'
        for i, (text, colour) in enumerate(legend)
    )
    return (
        f'<div class="ks-gauge-wrap" style="--tone:{hex_tone}">'
        f'<div class="ks-gauge" style="--circ:{RING_CIRCUMFERENCE};--off:{offset}">'
        '<svg viewBox="0 0 124 124" aria-hidden="true">'
        f'<circle class="ks-ring-bg" cx="62" cy="62" r="{RING_RADIUS}"></circle>'
        f'<circle class="ks-ring-spin" cx="62" cy="62" r="{RING_RADIUS - 13}"></circle>'
        f'<circle class="ks-ring-fg" cx="62" cy="62" r="{RING_RADIUS}"></circle>'
        '</svg>'
        '<div class="ks-gauge-center">'
        f'<div><div class="ks-g-num">{score}<span style="font-size:0.95rem;'
        f'color:var(--ks-dim)">%</span></div>'
        '<div class="ks-g-unit">live</div></div></div></div>'
        f'<div class="ks-gauge-text"><h4>{esc(headline)}</h4><p>{esc(body)}</p>'
        f'<div class="ks-gauge-legend">{chips}</div></div></div>'
    )


# ---------------------------------------------------------------------------
# Latency bars
# ---------------------------------------------------------------------------

def latency_bars(rows: Sequence[tuple[str, float, str]]) -> str:
    """Horizontal bars that grow from zero. Rows are ``(name, ms, tone)``."""
    if not rows:
        return ""
    peak = max(ms for _, ms, _ in rows) or 1.0
    out = []
    for i, (name, ms, tone) in enumerate(rows):
        width = max(5.0, round(ms / peak * 100, 1))
        out.append(
            f'<div class="ks-bar-row" style="--d:{i * 0.07:.2f}s">'
            f'<div class="ks-bar-name">{esc(name)}</div>'
            f'<div class="ks-bar-track"><div class="ks-bar-fill" style="--w:{width}%;'
            f'--tone:{TONE_HEX.get(tone, TONE_HEX[TONE_INFO])}"></div></div>'
            f'<div class="ks-bar-val">{ms:,.0f} ms</div></div>'
        )
    return f'<div class="ks-bars">{"".join(out)}</div>'


# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------

_TABLE_HEADERS = ("Provider", "Status", "Latency", "HTTP", "Detail", "Key", "Checked")


def results_table(rows: Sequence[dict], tone_of, meta_of) -> str:
    """A custom results table with staggered row entrances and inline latency bars.

    ``tone_of(status)`` returns a tone key; ``meta_of(provider)`` returns an
    ``(mark, accent)`` pair.
    """
    if not rows:
        return ""
    latencies = [r["Response Time (ms)"] for r in rows
                 if isinstance(r["Response Time (ms)"], (int, float))]
    peak = max(latencies) if latencies else 1.0

    head = "".join(f"<th>{esc(h)}</th>" for h in _TABLE_HEADERS)
    body = []
    for i, row in enumerate(rows):
        tone = tone_of(row["Status"])
        hex_tone = TONE_HEX.get(tone, TONE_HEX[TONE_IDLE])
        mark, accent = meta_of(row["Provider"])
        ms = row["Response Time (ms)"]
        if isinstance(ms, (int, float)):
            width = max(6.0, round(ms / peak * 100, 1))
            latency = (f'<span class="ks-mini-track"><span class="ks-mini-fill" '
                       f'style="--w:{width}%;--tone:{hex_tone};--d:{i * 0.05:.2f}s">'
                       f'</span></span><span class="ks-td-mono">{ms:,.0f} ms</span>')
        else:
            latency = '<span class="ks-td-mono">&mdash;</span>'
        body.append(
            f'<tr style="--d:{i * 0.055:.2f}s">'
            f'<td><span class="ks-td-name"><span class="ks-avatar-sm" '
            f'style="--accent:{esc(accent)}">{esc(mark)}</span>{esc(row["Provider"])}</span></td>'
            f'<td>{pill(row["Status"], tone)}</td>'
            f'<td style="white-space:nowrap">{latency}</td>'
            f'<td class="ks-td-mono">{esc(row["HTTP Code"] or "—")}</td>'
            f'<td class="ks-td-detail">{esc(row["Account / Detail"])}</td>'
            f'<td class="ks-td-mono ks-mono">{esc(row["Key"] or "—")}</td>'
            f'<td class="ks-td-mono">{esc(row["Checked At"][11:] or "—")}</td>'
            '</tr>'
        )
    return (f'<div class="ks-table-wrap"><table class="ks-table">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody>'
            f'</table></div>')


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_panel(targets: Sequence[str], done: int, total: int,
               finished: Iterable[str] = (), label: str = "Verifying credentials") -> str:
    """The live progress panel shown while verification requests are in flight.

    Re-rendered into the same placeholder after each completion, so the track
    advances and chips flip from pulsing dots to a drawn check mark.
    """
    complete = set(finished)
    percent = round(done / total * 100) if total else 0
    chips = "".join(
        (f'<span class="ks-scan-chip is-done" style="--d:{i * 0.05:.2f}s">'
         f'{_TICK_SVG}{esc(name)}</span>')
        if name in complete else
        (f'<span class="ks-scan-chip is-pending" style="--d:{i * 0.05:.2f}s">'
         f'<span class="ks-dots"><i></i><i></i><i></i></span>{esc(name)}</span>')
        for i, name in enumerate(targets)
    )
    return (
        '<div class="ks-scan"><div class="ks-scan-head">'
        f'<div class="ks-scan-title"><span class="ks-spinner"></span>{esc(label)}'
        '<span class="ks-eq"><i></i><i></i><i></i><i></i></span></div>'
        f'<div class="ks-scan-count">{done} of {total} complete &middot; {percent}%</div>'
        '</div>'
        f'<div class="ks-track"><div class="ks-track-fill" style="--w:{percent}%"></div></div>'
        f'<div class="ks-scan-chips">{chips}</div></div>'
    )


def skeleton_rows(count: int = 3) -> str:
    """Shimmering placeholders sized like result rows."""
    rows = "".join(
        f'<div style="display:flex;gap:12px;align-items:center;padding:12px 14px;'
        f'border-bottom:1px solid rgba(148,163,184,.08)">'
        f'<div class="ks-skel" style="width:25px;height:25px;border-radius:8px"></div>'
        f'<div class="ks-skel" style="width:{98 - i * 6}px;height:11px"></div>'
        f'<div class="ks-skel" style="width:76px;height:19px;border-radius:999px"></div>'
        f'<div class="ks-skel" style="flex:1;height:11px"></div></div>'
        for i in range(count)
    )
    return f'<div class="ks-table-wrap">{rows}</div>'


# ---------------------------------------------------------------------------
# Detector verdict
# ---------------------------------------------------------------------------

def verdict(detected: str, status: str, detail: str, confidence: str, tone: str) -> str:
    """The detector's headline result card."""
    hex_tone = TONE_HEX.get(tone, TONE_HEX[TONE_IDLE])
    icon = _VERDICT_ICONS.get(tone, _VERDICT_ICONS[TONE_IDLE])
    return (
        f'<div class="ks-verdict" style="--tone:{hex_tone}">'
        '<div class="ks-verdict-row">'
        f'<div class="ks-verdict-icon"><svg viewBox="0 0 24 24" aria-hidden="true">{icon}</svg></div>'
        '<div class="ks-verdict-main">'
        '<div class="ks-vt">Detected credential</div>'
        f'<h3>{esc(detected)}</h3>{pill(status, tone)}</div>'
        f'<div class="ks-conf"><div class="ks-conf-v">{esc(confidence)}</div>'
        '<div class="ks-conf-l">Confidence</div></div></div>'
        f'<div class="ks-verdict-detail">{esc(detail)}</div></div>'
    )


def probe_rows(rows: Sequence[tuple[str, str, str, str]]) -> str:
    """Per-candidate probe outcomes. Rows are ``(name, status, detail, tone)``."""
    if not rows:
        return ""
    out = "".join(
        f'<div class="ks-probe-row" style="--d:{i * 0.07:.2f}s">'
        f'<span class="ks-probe-name">{esc(name)}</span>{pill(status, tone)}'
        f'<span class="ks-probe-detail">{esc(detail)}</span></div>'
        for i, (name, status, detail, tone) in enumerate(rows)
    )
    return f'<div class="ks-probe">{out}</div>'


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def catalog_cards(entries: Sequence[dict]) -> str:
    """Browsable provider reference cards."""
    cards = []
    for i, entry in enumerate(entries):
        verifiable = entry["verifiable"]
        badge = pill("Live verifiable", TONE_OK) if verifiable else pill("Format only", TONE_WARN)
        patterns = " ".join(f'<code class="ks-pattern">{esc(p)}</code>'
                            for p in entry["patterns"])
        rows = [
            ('Probe', esc(entry["endpoint"]) or "&mdash;"),
            ('Pattern', patterns),
        ]
        if entry.get("env"):
            rows.append(('Env var', f'<code class="ks-pattern">{esc(entry["env"])}</code>'))
        if entry.get("note"):
            rows.append(('Caveat', esc(entry["note"])))
        detail = "".join(
            f'<div class="ks-cc-row"><span class="ks-cc-key">{key}</span>'
            f'<span class="ks-cc-val">{value}</span></div>'
            for key, value in rows
        )
        cards.append(
            f'<div class="ks-cat-card" style="--accent:{esc(entry["accent"])};'
            f'--d:{min(i * 0.035, 0.7):.2f}s">'
            '<div class="ks-cc-top">'
            f'<span class="ks-avatar-sm" style="--accent:{esc(entry["accent"])}">'
            f'{esc(entry["mark"])}</span>'
            f'<div style="min-width:0"><div class="ks-cc-name">{esc(entry["name"])}</div>'
            f'<div class="ks-card-cat">{esc(entry["category"])}</div></div></div>'
            f'{badge}{detail}</div>'
        )
    return f'<div class="ks-cat-grid">{"".join(cards)}</div>'


# ---------------------------------------------------------------------------
# Flourishes
# ---------------------------------------------------------------------------

_BURST_COLORS = ("#34d399", "#6366f1", "#8b5cf6", "#06b6d4", "#fbbf24", "#f472b6")


def burst(pieces: int = 22) -> str:
    """A one-shot particle burst, used when every checked credential is live.

    Offsets are precomputed in Python so the markup needs no trigonometry at
    render time and stays identical between runs.
    """
    out = []
    for i in range(pieces):
        angle = (i / pieces) * 2 * math.pi
        distance = 58 + (i % 5) * 17
        tx = round(math.cos(angle) * distance, 1)
        ty = round(math.sin(angle) * distance * 0.62 - 26, 1)
        colour = _BURST_COLORS[i % len(_BURST_COLORS)]
        out.append(
            f'<i style="--tx:{tx}px;--ty:{ty}px;--c:{colour};'
            f'--d:{(i % 6) * 0.045:.2f}s"></i>'
        )
    return f'<div class="ks-burst" aria-hidden="true">{"".join(out)}</div>'


def empty_state(headline: str, body: str) -> str:
    return (
        '<div class="ks-empty"><div class="ks-empty-icon">'
        '<svg viewBox="0 0 24 24" aria-hidden="true">'
        '<path d="M10.5 3.5h3l.6 2.6 2.3 1.3 2.5-.9 1.5 2.6-1.9 1.9v2.6l1.9 1.9-1.5 2.6-2.5-.9'
        '-2.3 1.3-.6 2.6h-3l-.6-2.6-2.3-1.3-2.5.9-1.5-2.6 1.9-1.9v-2.6L3.6 9.1l1.5-2.6 2.5.9'
        ' 2.3-1.3z"></path><circle cx="12" cy="12" r="2.6"></circle></svg></div>'
        f'<h4>{esc(headline)}</h4><p>{esc(body)}</p></div>'
    )


def legend(entries: Sequence[tuple[str, str, str]]) -> str:
    """Status legend. Entries are ``(label, tone, description)``."""
    rows = "".join(
        f'<div class="ks-probe-row" style="--d:{i * 0.06:.2f}s">'
        f'<span style="min-width:184px">{pill(label, tone)}</span>'
        f'<span class="ks-probe-detail">{esc(description)}</span></div>'
        for i, (label, tone, description) in enumerate(entries)
    )
    return f'<div class="ks-probe">{rows}</div>'
