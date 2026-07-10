"""In-browser visual overlay for headed mode action visualization."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from frontend_visualqa.text_utils import clip_text_preserving_lines
from frontend_visualqa.utils import safe_page_evaluate

if TYPE_CHECKING:
    from playwright.async_api import Page


logger = logging.getLogger(__name__)

Z_INDEX = 2_147_483_646
YUTORI_GREEN = "#1DCD98"
BORDER_CYCLE_MS = 1000

PERSISTENT_ROOT_ID = "__n1PersistentRoot"
TRANSIENT_ROOT_ID = "__n1TransientRoot"
GRADIENT_BORDER_ID = "__n1GradientBorder"
BORDER_STYLE_ID = "__n1BorderStyle"
THOUGHT_CARD_ID = "__n1ThoughtCard"
THOUGHT_STYLE_ID = "__n1ThoughtStyle"
CLICK_STYLE_ID = "__n1ClickStyle"

CURSOR_ID = "__n1Cursor"
BADGE_ID = "__n1Badge"
BADGE_SLOT_ID = "__n1BadgeSlot"
BADGE_LOGO_ID = "__n1BadgeLogo"
BADGE_GLYPH_ID = "__n1BadgeGlyph"
DRAG_STYLE_ID = "__n1DragStyle"
BADGE_KF_STYLE_ID = "__n1BadgeKf"
CLICK_DURATION_MS = 250
DRAG_DURATION_MS = 200
CURSOR_TRANSITION_MS = 350
THOUGHT_DURATION_MS = 2000
# Thought capsule shrink→expand on a new reasoning: collapse to the 48px badge
# (THOUGHT_COLLAPSE_MS), then the badge width transition expands it to fit
# (THOUGHT_EXPAND_MS — must match the badge CSS width transition). Used to hold a
# click until the pill is fully visible (see _await_thought_settled).
THOUGHT_COLLAPSE_MS = 360
THOUGHT_EXPAND_MS = 340

_CURSOR_SVG = (
    '<svg width="110" height="130" viewBox="0 0 110 130" fill="none" xmlns="http://www.w3.org/2000/svg"> <g id="Default Live Cursor"> <g id="Badge" filter="url(#filter0_ddddii_45_139)"> <rect x="45.3307" y="27.3717" width="48" height="48" rx="10.962" fill="url(#paint0_linear_45_139)"/> <rect x="45.3307" y="27.3717" width="48" height="48" rx="10.962" stroke="url(#paint1_linear_45_139)"/> <g id="yLoop"> <path d="M80.8847 38.0808C82.1521 37.5842 83.3202 38.0103 83.6386 38.9578C83.9999 40.0335 83.2812 40.9254 82.2812 41.3797C73.98 45.1497 65.5645 51.8231 65.5644 57.1961C65.5644 60.7133 67.6232 61.8054 69.2607 61.8054C70.8982 61.8053 72.9687 60.7132 72.9687 57.1961C72.9687 55.1469 71.7266 53.1005 70.5117 51.6082C70.5117 51.6082 71.8191 50.0465 73.3418 48.9539C75.573 51.5308 76.9794 54.0813 76.9794 57.1961C76.9794 62.1932 73.7452 65.8716 69.2607 65.8719C64.776 65.8718 61.541 62.1933 61.541 57.1961C61.541 49.1584 73.6804 40.9043 80.8847 38.0808ZM55.0224 38.9597C55.3407 38.012 56.5088 37.5851 57.7763 38.0818C60.72 39.2354 64.4873 41.296 67.914 43.8777C66.3195 45.1252 65.0859 46.4549 65.0859 46.4549C62.4438 44.5139 59.4042 42.7542 56.3798 41.3806C55.38 40.9264 54.6614 40.0353 55.0224 38.9597Z" fill="url(#paint2_linear_45_139)"/> <path d="M80.8847 38.0808C82.1521 37.5842 83.3202 38.0103 83.6386 38.9578C83.9999 40.0335 83.2812 40.9254 82.2812 41.3797C73.98 45.1497 65.5645 51.8231 65.5644 57.1961C65.5644 60.7133 67.6232 61.8054 69.2607 61.8054C70.8982 61.8053 72.9687 60.7132 72.9687 57.1961C72.9687 55.1469 71.7266 53.1005 70.5117 51.6082C70.5117 51.6082 71.8191 50.0465 73.3418 48.9539C75.573 51.5308 76.9794 54.0813 76.9794 57.1961C76.9794 62.1932 73.7452 65.8716 69.2607 65.8719C64.776 65.8718 61.541 62.1933 61.541 57.1961C61.541 49.1584 73.6804 40.9043 80.8847 38.0808ZM55.0224 38.9597C55.3407 38.012 56.5088 37.5851 57.7763 38.0818C60.72 39.2354 64.4873 41.296 67.914 43.8777C66.3195 45.1252 65.0859 46.4549 65.0859 46.4549C62.4438 44.5139 59.4042 42.7542 56.3798 41.3806C55.38 40.9264 54.6614 40.0353 55.0224 38.9597Z" fill="#F8FAFC" style="mix-blend-mode:overlay"/> <path d="M80.8847 38.0808C82.1521 37.5842 83.3202 38.0103 83.6386 38.9578C83.9999 40.0335 83.2812 40.9254 82.2812 41.3797C73.98 45.1497 65.5645 51.8231 65.5644 57.1961C65.5644 60.7133 67.6232 61.8054 69.2607 61.8054C70.8982 61.8053 72.9687 60.7132 72.9687 57.1961C72.9687 55.1469 71.7266 53.1005 70.5117 51.6082C70.5117 51.6082 71.8191 50.0465 73.3418 48.9539C75.573 51.5308 76.9794 54.0813 76.9794 57.1961C76.9794 62.1932 73.7452 65.8716 69.2607 65.8719C64.776 65.8718 61.541 62.1933 61.541 57.1961C61.541 49.1584 73.6804 40.9043 80.8847 38.0808ZM55.0224 38.9597C55.3407 38.012 56.5088 37.5851 57.7763 38.0818C60.72 39.2354 64.4873 41.296 67.914 43.8777C66.3195 45.1252 65.0859 46.4549 65.0859 46.4549C62.4438 44.5139 59.4042 42.7542 56.3798 41.3806C55.38 40.9264 54.6614 40.0353 55.0224 38.9597Z" fill="#F8FAFC" fill-opacity="0.5"/> </g> </g> <g id="Yutori Cursor" filter="url(#filter1_ddddii_45_139)"> <path d="M17.7686 7.16441C16.8071 4.87975 18.9606 2.51614 21.3246 3.26145L51.2884 12.7081C54.3315 13.6675 54.214 18.0135 51.1235 18.8071C43.5544 20.7507 37.8792 27.0293 36.7077 34.7557L36.5332 35.9063C36.0312 39.2174 31.4966 39.7823 30.1974 36.6956L17.7686 7.16441Z" fill="url(#paint3_linear_45_139)"/> <path d="M17.7686 7.16441C16.8071 4.87975 18.9606 2.51614 21.3246 3.26145L51.2884 12.7081C54.3315 13.6675 54.214 18.0135 51.1235 18.8071C43.5544 20.7507 37.8792 27.0293 36.7077 34.7557L36.5332 35.9063C36.0312 39.2174 31.4966 39.7823 30.1974 36.6956L17.7686 7.16441Z" stroke="url(#paint4_linear_45_139)"/> </g> </g> <defs> <filter id="filter0_ddddii_45_139" x="29.2307" y="24.4717" width="80.2" height="105.4" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB"> <feFlood flood-opacity="0" result="BackgroundImageFix"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="2.4"/> <feGaussianBlur stdDeviation="2.4"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.07 0"/> <feBlend mode="normal" in2="BackgroundImageFix" result="effect1_dropShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="9.6"/> <feGaussianBlur stdDeviation="4.8"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.06 0"/> <feBlend mode="normal" in2="effect1_dropShadow_45_139" result="effect2_dropShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="21.6"/> <feGaussianBlur stdDeviation="6.6"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.04 0"/> <feBlend mode="normal" in2="effect2_dropShadow_45_139" result="effect3_dropShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="38.4"/> <feGaussianBlur stdDeviation="7.8"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.01 0"/> <feBlend mode="normal" in2="effect3_dropShadow_45_139" result="effect4_dropShadow_45_139"/> <feBlend mode="normal" in="SourceGraphic" in2="effect4_dropShadow_45_139" result="shape"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dx="1.2" dy="-2.4"/> <feGaussianBlur stdDeviation="2.4"/> <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/> <feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.15 0"/> <feBlend mode="normal" in2="shape" result="effect5_innerShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dx="-1.2" dy="3.6"/> <feGaussianBlur stdDeviation="2.4"/> <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/> <feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.4 0"/> <feBlend mode="normal" in2="effect5_innerShadow_45_139" result="effect6_innerShadow_45_139"/> </filter> <filter id="filter1_ddddii_45_139" x="1.90735e-06" y="4.29153e-06" width="71.0401" height="98.1868" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB"> <feFlood flood-opacity="0" result="BackgroundImageFix"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="2.62043"/> <feGaussianBlur stdDeviation="2.62043"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.07 0"/> <feBlend mode="normal" in2="BackgroundImageFix" result="effect1_dropShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="10.4817"/> <feGaussianBlur stdDeviation="5.24087"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.06 0"/> <feBlend mode="normal" in2="effect1_dropShadow_45_139" result="effect2_dropShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="23.5839"/> <feGaussianBlur stdDeviation="7.20619"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.04 0"/> <feBlend mode="normal" in2="effect2_dropShadow_45_139" result="effect3_dropShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dy="41.9269"/> <feGaussianBlur stdDeviation="8.51641"/> <feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.01 0"/> <feBlend mode="normal" in2="effect3_dropShadow_45_139" result="effect4_dropShadow_45_139"/> <feBlend mode="normal" in="SourceGraphic" in2="effect4_dropShadow_45_139" result="shape"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dx="1.31022" dy="-2.62043"/> <feGaussianBlur stdDeviation="2.62043"/> <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/> <feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.15 0"/> <feBlend mode="normal" in2="shape" result="effect5_innerShadow_45_139"/> <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/> <feOffset dx="-1.31022" dy="3.93065"/> <feGaussianBlur stdDeviation="2.62043"/> <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/> <feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.4 0"/> <feBlend mode="normal" in2="effect5_innerShadow_45_139" result="effect6_innerShadow_45_139"/> </filter> <linearGradient id="paint0_linear_45_139" x1="76.1071" y1="27.3717" x2="52.9875" y2="65.2146" gradientUnits="userSpaceOnUse"> <stop stop-color="#18AA7E"/> <stop offset="0.45" stop-color="#148F6A"/> <stop offset="0.75" stop-color="#148F6A"/> <stop offset="1" stop-color="#159871"/> </linearGradient> <linearGradient id="paint1_linear_45_139" x1="93.3307" y1="27.3717" x2="45.3307" y2="75.3718" gradientUnits="userSpaceOnUse"> <stop stop-color="#5AE8BD"/> <stop offset="0.5" stop-color="#127D5D"/> <stop offset="1" stop-color="#19B385"/> </linearGradient> <linearGradient id="paint2_linear_45_139" x1="69.3591" y1="37.8241" x2="58.779" y2="60.9714" gradientUnits="userSpaceOnUse"> <stop stop-color="#5DF3C6"/> <stop offset="0.45" stop-color="#29B188"/> <stop offset="0.75" stop-color="#22A67E"/> <stop offset="1" stop-color="#22C191"/> </linearGradient> <linearGradient id="paint3_linear_45_139" x1="41.8168" y1="0.821967" x2="17.2954" y2="37.3243" gradientUnits="userSpaceOnUse"> <stop stop-color="#18AA7E"/> <stop offset="0.45" stop-color="#148F6A"/> <stop offset="0.75" stop-color="#148F6A"/> <stop offset="1" stop-color="#159871"/> </linearGradient> <linearGradient id="paint4_linear_45_139" x1="25.3307" y1="1.37175" x2="18.0622" y2="39.8967" gradientUnits="userSpaceOnUse"> <stop stop-color="#5AE8BD"/> <stop offset="0.5" stop-color="#009367"/> <stop offset="0.9" stop-color="#19B385"/> </linearGradient> </defs> </svg>'
)

_CURSOR_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_CURSOR_SVG.encode()).decode()

# The pointer and badge are split: the pointer ships as an image while the
# badge is a live DOM element (built in _PERSISTENT_ROOT_JS), so the thought
# capsule can stretch the badge itself — rim and inner shadows wrap the
# expanded surface instead of leaving a nested badge square around the y-loop.
_BADGE_START = _CURSOR_SVG.index('<g id="Badge"')
_POINTER_START = _CURSOR_SVG.index('<g id="Yutori Cursor"')
_POINTER_SVG = _CURSOR_SVG[:_BADGE_START] + _CURSOR_SVG[_POINTER_START:]
_POINTER_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_POINTER_SVG.encode()).decode()

# 1x pointer from the Live Cursor mockup (Figma node 78:307 via
# experimental/mp/api-graphics/live-cursor/preview.html): arrow geometry sized
# for the 40px badge, drop + inner shadows baked into the SVG filter. Placed at
# (-16.5, -2.8) in the cursor box so the arrow tip lands at the box origin.
_POINTER_V2_SVG = '<svg preserveAspectRatio="none" viewBox="0 0 71.0401 98.1868" fill="none" xmlns="http://www.w3.org/2000/svg" ><g id="Yutori Cursor" filter="url(#filter0_ddddii_75_281)"><path d="M17.7686 7.1644C16.8071 4.87975 18.9606 2.51614 21.3246 3.26145L51.2884 12.7081C54.3315 13.6675 54.214 18.0135 51.1235 18.8071C43.5544 20.7507 37.8792 27.0293 36.7077 34.7557L36.5332 35.9063C36.0312 39.2174 31.4966 39.7823 30.1974 36.6956L17.7686 7.1644Z" fill="url(#paint0_linear_75_281)" /><path d="M17.7686 7.1644C16.8071 4.87975 18.9606 2.51614 21.3246 3.26145L51.2884 12.7081C54.3315 13.6675 54.214 18.0135 51.1235 18.8071C43.5544 20.7507 37.8792 27.0293 36.7077 34.7557L36.5332 35.9063C36.0312 39.2174 31.4966 39.7823 30.1974 36.6956L17.7686 7.1644Z" stroke="url(#paint1_linear_75_281)" /></g><defs><filter id="filter0_ddddii_75_281" x="0" y="0" width="71.0401" height="98.1868" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB" ><feFlood flood-opacity="0" result="BackgroundImageFix" /><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha" /><feOffset dy="2.62043" /><feGaussianBlur stdDeviation="2.62043" /><feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.07 0" /><feBlend mode="normal" in2="BackgroundImageFix" result="effect1_dropShadow_75_281" /><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha" /><feOffset dy="10.4817" /><feGaussianBlur stdDeviation="5.24087" /><feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.06 0" /><feBlend mode="normal" in2="effect1_dropShadow_75_281" result="effect2_dropShadow_75_281" /><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha" /><feOffset dy="23.5839" /><feGaussianBlur stdDeviation="7.20619" /><feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.04 0" /><feBlend mode="normal" in2="effect2_dropShadow_75_281" result="effect3_dropShadow_75_281" /><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha" /><feOffset dy="41.9269" /><feGaussianBlur stdDeviation="8.51641" /><feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.01 0" /><feBlend mode="normal" in2="effect3_dropShadow_75_281" result="effect4_dropShadow_75_281" /><feBlend mode="normal" in="SourceGraphic" in2="effect4_dropShadow_75_281" result="shape" /><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha" /><feOffset dx="1.31022" dy="-2.62043" /><feGaussianBlur stdDeviation="2.62043" /><feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1" /><feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.15 0" /><feBlend mode="normal" in2="shape" result="effect5_innerShadow_75_281" /><feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha" /><feOffset dx="-1.31022" dy="3.93065" /><feGaussianBlur stdDeviation="2.62043" /><feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1" /><feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.4 0" /><feBlend mode="normal" in2="effect5_innerShadow_75_281" result="effect6_innerShadow_75_281" /></filter><linearGradient id="paint0_linear_75_281" x1="41.8168" y1="0.821961" x2="17.2954" y2="37.3243" gradientUnits="userSpaceOnUse" ><stop stop-color="#18AA7E" /><stop offset="0.45" stop-color="#148F6A" /><stop offset="0.75" stop-color="#148F6A" /><stop offset="1" stop-color="#159871" /></linearGradient><linearGradient id="paint1_linear_75_281" x1="25.3307" y1="1.37174" x2="18.0622" y2="39.8967" gradientUnits="userSpaceOnUse" ><stop stop-color="#5AE8BD" /><stop offset="0.5" stop-color="#009367" /><stop offset="0.9" stop-color="#19B385" /></linearGradient></defs></svg>'
_POINTER_V2_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_POINTER_V2_SVG.encode()).decode()

_YLOOP_START = _CURSOR_SVG.index('<g id="yLoop"')
_YLOOP_SEG = _CURSOR_SVG[_YLOOP_START:_POINTER_START]
_YLOOP_GROUP = _YLOOP_SEG[: _YLOOP_SEG.rindex("</g>")]
_YLOOP_GRAD_START = _CURSOR_SVG.index('<linearGradient id="paint2_linear_45_139"')
_YLOOP_GRAD = _CURSOR_SVG[_YLOOP_GRAD_START : _CURSOR_SVG.index("</linearGradient>", _YLOOP_GRAD_START) + len("</linearGradient>")]
_YLOOP_SVG = (
    '<svg width="48" height="48" viewBox="45.3307 27.3717 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">'
    + _YLOOP_GROUP
    + "<defs>"
    + _YLOOP_GRAD
    + "</defs></svg>"
)
_YLOOP_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_YLOOP_SVG.encode()).decode()

def _styled_glyph(paths: str, stroke_width: float) -> str:
    # Give stroke icons the y-loop's glossy treatment: a light-green gradient
    # stroke + a white overlay-blend layer + a translucent white layer.
    grad = (
        "<defs><linearGradient id='gg' x1='0.72' y1='0.05' x2='0.28' y2='0.95'>"
        "<stop stop-color='#5DF3C6'/><stop offset='0.45' stop-color='#29B188'/>"
        "<stop offset='0.75' stop-color='#22A67E'/><stop offset='1' stop-color='#22C191'/>"
        "</linearGradient></defs>"
    )
    layers = (
        f"<g stroke='url(#gg)'>{paths}</g>"
        f"<g stroke='#F8FAFC' style='mix-blend-mode:overlay'>{paths}</g>"
        f"<g stroke='#F8FAFC' stroke-opacity='0.5'>{paths}</g>"
    )
    return (
        "<svg width='100%' height='100%' viewBox='0 0 24 24' fill='none' xmlns='http://www.w3.org/2000/svg'>"
        f"<g fill='none' stroke-width='{stroke_width}' stroke-linecap='round' stroke-linejoin='round'>{layers}</g>"
        f"{grad}</svg>"
    )


_GLYPH_CHEVRON = _styled_glyph("<polyline points='6 10 12 16 18 10'></polyline>", 3)
_GLYPH_TYPE = _styled_glyph(
    "<line x1='12' y1='5' x2='12' y2='19'></line>"
    "<path d='M8 5h8'></path><path d='M8 19h8'></path>",
    2.5,
)
_GLYPH_COPY = _styled_glyph(
    "<rect width='14' height='14' x='8' y='8' rx='2' ry='2'></rect>"
    "<path d='M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2'></path>",
    2,
)
_GLYPH_PASTE = _styled_glyph(
    "<path d='M11 14h10'></path><path d='M16 4h2a2 2 0 0 1 2 2v1.344'></path>"
    "<path d='m17 18 4-4-4-4'></path>"
    "<path d='M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 1.793-1.113'></path>"
    "<rect x='8' y='2' width='8' height='4' rx='1'></rect>",
    2,
)

_ROOT_STYLE = (
    f"position:fixed;top:0;left:0;right:0;bottom:0;"
    f"pointer-events:none;z-index:{Z_INDEX};visibility:visible;opacity:1;"
)


def _set_visibility_opacity_js(element_ref: str, *, visibility: str, opacity: str) -> str:
    return (
        f"{element_ref}.style.visibility = '{visibility}';"
        f"{element_ref}.style.opacity = '{opacity}';"
    )


def _inject_style_js(style_id: str, css_js_expr: str, *, guard: bool = False) -> str:
    """Return JS snippet that injects a <style> element into document.head.

    guard=True: no-op if the element already exists (one-time inject).
    guard=False (default): remove any existing element first, then create fresh.
    css_js_expr is a JS expression that evaluates to the CSS text string.
    """
    if guard:
        return (
            f"if (!document.getElementById('{style_id}')) {{\n"
            f"    const style = document.createElement('style');\n"
            f"    style.id = '{style_id}';\n"
            f"    style.textContent = {css_js_expr};\n"
            f"    document.head.appendChild(style);\n"
            f"}}"
        )
    return (
        f"const existing = document.getElementById('{style_id}');\n"
        f"if (existing) existing.remove();\n"
        f"const style = document.createElement('style');\n"
        f"style.id = '{style_id}';\n"
        f"style.textContent = {css_js_expr};\n"
        f"document.head.appendChild(style);"
    )


# Animated y-loop draw cycle (retract -> CCW dot hop -> redraw, with the
# depth-slice weave at the self-intersection), ported from the Live Cursor
# mockup at experimental/mp/api-graphics/live-cursor/preview.html (yutori
# PR #10782). Only the animation is ported; geometry is adapted to the badge
# slot via percentage placement (mark = 60% of the badge box, optically
# centered, matching the mockup's in-badge placement). Injected into
# _PERSISTENT_ROOT_JS; the rAF loop stops itself once the logo node is
# detached (navigation re-injects the root and restarts it).
_YLOOP_ANIM_JS = r"""
    function n1BuildLoopLogo(logo) {
        logo.innerHTML = '<svg style="position:absolute;left:20%;top:21.9%;width:60%;height:58.3%;overflow:visible" viewBox="14 24 232 236" fill="none" xmlns="http://www.w3.org/2000/svg">'
            + '<defs><linearGradient id="__n1LoopGrad" x1="114" y1="0" x2="198" y2="193" gradientUnits="userSpaceOnUse">'
            + '<stop stop-color="#C6FAFB"/><stop offset="0.45" stop-color="#A4FBFC"/><stop offset="0.75" stop-color="#9DFBFA"/><stop offset="1" stop-color="#9DFBFC"/>'
            + '</linearGradient></defs>'
            + '<g transform="translate(244 25) scale(-1 1)">'
            + '<path class="__n1lp" fill="none" stroke="url(#__n1LoopGrad)" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"></path>'
            + '<g class="__n1dl"><rect class="__n1ms" fill="#148f6a"></rect></g>'
            + '<circle class="__n1th" r="0" fill="#148f6a"></circle>'
            + '<path class="__n1mt" fill="none" stroke="url(#__n1LoopGrad)" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"></path>'
            + '</g>'
            + '<circle class="__n1dot" r="15" fill="url(#__n1LoopGrad)" opacity="0"></circle>'
            + '</svg>';
        const loopPath = logo.querySelector('.__n1lp');
        const depthLayer = logo.querySelector('.__n1dl');
        const maskShape = logo.querySelector('.__n1ms');
        const maskTop = logo.querySelector('.__n1mt');
        const tipHalo = logo.querySelector('.__n1th');
        const travelDot = logo.querySelector('.__n1dot');
        const LOOP_D = 'M213.648 15.1484C213.648 15.1484 69.6484 91.1484 69.6484 164.454C69.6484 199.482 91.2215 219.493 117.833 219.493C144.445 219.493 166.018 199.482 166.018 164.454C166.018 89.6484 15.1484 15.1484 15.1484 15.1484';
        loopPath.setAttribute('d', LOOP_D);

        // Loop draw-cycle geometry, precomputed from the constant LOOP_D above:
        // the self-intersection crossover point, the over-strand tangent, and
        // the depth-weave top segment. These were previously derived on every
        // mount via an O(n^2) closest-pair search (findCrossover) plus
        // buildSegment/tangentAt getPointAtLength sampling — recomputed on every
        // navigation re-inject. They are deterministic in LOOP_D, so they are
        // baked. To regenerate after changing LOOP_D, run the scratchpad script
        // measure_yloop.py (see PR notes) and paste its outputs here.
        const LOOP_L = 599.655517578125;
        const loopCross = { x: 117.2813835144043, y: 83.21451950073242, len1: 118.12913367983097, len2: 476.5209121321995 };
        const overAngle = -138.05597736147777;
        const topSegmentD = 'M140.95 107.75L139.83 106.41L138.70 105.08L137.56 103.77L136.40 102.46L135.23 101.17L134.05 99.88L132.86 98.61L131.66 97.34L130.46 96.09L129.24 94.84L128.01 93.60L126.77 92.37L125.53 91.15L124.28 89.94L123.02 88.73L121.75 87.54L120.47 86.35L119.19 85.17L117.90 83.99L116.60 82.83L115.30 81.67L113.99 80.52L112.67 79.38L111.35 78.24L110.02 77.11L108.69 75.99L107.35 74.87L106.00 73.76L104.65 72.66L103.30 71.56L101.94 70.47L100.57 69.39L99.20 68.31L97.82 67.24L96.44 66.17L95.06 65.11L93.67 64.06L92.28 63.01L90.88 61.97';
        const MASK_W = 44, MASK_H = 36;
        maskShape.setAttribute('x', (loopCross.x - MASK_W / 2).toFixed(2));
        maskShape.setAttribute('y', (loopCross.y - MASK_H / 2).toFixed(2));
        maskShape.setAttribute('width', MASK_W);
        maskShape.setAttribute('height', MASK_H);
        maskShape.setAttribute('transform', 'rotate(' + (overAngle + 90).toFixed(2) + ' ' + loopCross.x + ' ' + loopCross.y + ')');
        maskTop.setAttribute('d', topSegmentD);

        const MASK_HALF = 34;
        const MASK_TOP_L = 68.0010757446289;
        const MASK_TOP_START_LEN = loopCross.len2 - MASK_HALF;
        const MASK_TOP_END_LEN = loopCross.len2 + MASK_HALF;
        const TIP_HALO_R_MAX = 22;

        function smoothstep(e0, e1, x) {
            const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0)));
            return t * t * (3 - 2 * t);
        }
        function easeInCubic(t) { return t * t * t; }
        function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

        const PHASE1_END = 0.3, PHASE2_END = 0.42, MIN_DOT = 0.5;
        const MASK_HOLD_END_FRAME = 200, MASK_EXIT_END_FRAME = 230;
        function phase1LoopVisibleAtFrame(frame) {
            const p = frame / 1000;
            const local = Math.max(0, Math.min(1, p / PHASE1_END));
            return LOOP_L - (LOOP_L - MIN_DOT) * easeInCubic(local);
        }

        const LOOP_END_XY = [244 - 15.1484, 25 + 15.1484];
        const LOOP_START_XY = [244 - 213.648, 25 + 15.1484];
        const DOT_CP1 = [220, 10], DOT_CP2 = [40, 10];
        function cubicBezierPt(t, P0, P1, P2, P3) {
            const mt = 1 - t, mt2 = mt * mt, t2 = t * t;
            return [
                mt2 * mt * P0[0] + 3 * mt2 * t * P1[0] + 3 * mt * t2 * P2[0] + t2 * t * P3[0],
                mt2 * mt * P0[1] + 3 * mt2 * t * P1[1] + 3 * mt * t2 * P2[1] + t2 * t * P3[1],
            ];
        }
        travelDot.setAttribute('cx', LOOP_END_XY[0].toFixed(2));
        travelDot.setAttribute('cy', LOOP_END_XY[1].toFixed(2));

        function renderAt(p) {
            let loopVisible = 0, loopDashOffset = 0;
            let dotOpacity = 0, dotX = LOOP_END_XY[0], dotY = LOOP_END_XY[1];
            let depthLo = 0, depthHi = 0;
            if (p <= PHASE1_END) {
                const local = p / PHASE1_END;
                loopVisible = LOOP_L - (LOOP_L - MIN_DOT) * easeInCubic(local);
                loopDashOffset = loopVisible - LOOP_L;
                depthHi = phase1LoopVisibleAtFrame(MASK_HOLD_END_FRAME);
                depthLo = phase1LoopVisibleAtFrame(MASK_EXIT_END_FRAME);
            } else if (p <= PHASE2_END) {
                const local = (p - PHASE1_END) / (PHASE2_END - PHASE1_END);
                dotOpacity = 1;
                const pt = cubicBezierPt(local, LOOP_END_XY, DOT_CP1, DOT_CP2, LOOP_START_XY);
                dotX = pt[0]; dotY = pt[1];
            } else {
                const local = (p - PHASE2_END) / (1 - PHASE2_END);
                loopVisible = MIN_DOT + (LOOP_L - MIN_DOT) * easeOutCubic(local);
                loopDashOffset = 0;
                depthLo = MASK_TOP_START_LEN;
                depthHi = MASK_TOP_END_LEN;
            }

            if (loopVisible > 0.01) {
                loopPath.style.display = '';
                loopPath.setAttribute('stroke-dasharray', loopVisible.toFixed(2) + ' ' + (LOOP_L * 2).toFixed(2));
                loopPath.setAttribute('stroke-dashoffset', loopDashOffset.toFixed(2));
            } else {
                loopPath.style.display = 'none';
            }

            if (dotOpacity > 0) {
                travelDot.style.display = '';
                travelDot.setAttribute('cx', dotX.toFixed(2));
                travelDot.setAttribute('cy', dotY.toFixed(2));
                travelDot.setAttribute('opacity', dotOpacity.toFixed(3));
            } else {
                travelDot.style.display = 'none';
            }

            const depthFactor = depthHi > depthLo ? smoothstep(depthLo, depthHi, loopVisible) : 0;
            const curH = MASK_H * depthFactor;
            maskShape.setAttribute('height', curH.toFixed(2));
            maskShape.setAttribute('y', (loopCross.y + MASK_H / 2 - curH).toFixed(2));

            let maskTopVisible = 0, maskTopOffset = 0;
            if (p <= PHASE1_END) {
                const winLeft = LOOP_L - loopVisible;
                if (winLeft <= MASK_TOP_START_LEN) {
                    maskTopVisible = MASK_TOP_L;
                } else if (winLeft < MASK_TOP_END_LEN) {
                    const eat = (winLeft - MASK_TOP_START_LEN) / (MASK_TOP_END_LEN - MASK_TOP_START_LEN);
                    maskTopVisible = MASK_TOP_L * (1 - eat);
                    maskTopOffset = -MASK_TOP_L * eat;
                }
            } else if (p > PHASE2_END) {
                const grow = Math.max(0, Math.min(1, (loopVisible - MASK_TOP_START_LEN) / (MASK_TOP_END_LEN - MASK_TOP_START_LEN)));
                maskTopVisible = MASK_TOP_L * grow;
            }
            if (maskTopVisible > 0.01) {
                maskTop.style.display = '';
                maskTop.setAttribute('stroke-dasharray', maskTopVisible.toFixed(2) + ' ' + (MASK_TOP_L * 2).toFixed(2));
                maskTop.setAttribute('stroke-dashoffset', maskTopOffset.toFixed(2));
            } else {
                maskTop.style.display = 'none';
            }

            let tipHaloR = 0;
            if (p > PHASE2_END && loopVisible > MASK_TOP_START_LEN && loopVisible < MASK_TOP_END_LEN) {
                const overlapT = (loopVisible - MASK_TOP_START_LEN) / (MASK_TOP_END_LEN - MASK_TOP_START_LEN);
                const k = Math.min(smoothstep(0, 0.2, overlapT), 1 - smoothstep(0.8, 1, overlapT));
                tipHaloR = TIP_HALO_R_MAX * k;
                const tipPt = loopPath.getPointAtLength(Math.min(loopVisible, LOOP_L));
                tipHalo.setAttribute('cx', tipPt.x.toFixed(2));
                tipHalo.setAttribute('cy', tipPt.y.toFixed(2));
            }
            if (tipHaloR > 0.5) {
                tipHalo.style.display = '';
                tipHalo.setAttribute('r', tipHaloR.toFixed(2));
            } else {
                tipHalo.style.display = 'none';
            }
            depthLayer.style.display = depthFactor > 0.001 ? '' : 'none';
        }

        const CYCLE_MS = 1600, PAUSE_MS = 300;
        let startTime = performance.now();
        let lingerAt = 0;
        function frame(now) {
            if (!logo.isConnected) return;
            if (lingerAt) {
                if (now - lingerAt > PAUSE_MS) { lingerAt = 0; startTime = now; }
                requestAnimationFrame(frame);
                return;
            }
            const t = Math.min(1, (now - startTime) / CYCLE_MS);
            renderAt(t);
            if (t >= 1) lingerAt = now;
            requestAnimationFrame(frame);
        }
        renderAt(0);
        requestAnimationFrame(frame);
    }
"""

_PERSISTENT_ROOT_JS = f"""() => {{
    if (document.getElementById('{PERSISTENT_ROOT_ID}')) return;
    const root = document.createElement('div');
    root.id = '{PERSISTENT_ROOT_ID}';
    root.style.cssText = '{_ROOT_STYLE}';

    // Compositor-only animation: bake the gradient at peak intensity and
    // pulse element opacity instead of animating background-image. Animating
    // background-image would force a full-viewport repaint every frame on
    // the main thread (gradient stops aren't compositor-friendly); pulsing
    // opacity on a static layer runs entirely on the GPU compositor thread.
    // The original lerp was alpha 0.25→0.4; baking α=0.4 and animating
    // opacity 0.625→1.0 reproduces the same effective range (0.625 × 0.4
    // = 0.25). Spread is locked at 6%; through filter:blur(4px) the
    // dropped 3%↔6% pulse is visually negligible.
    const borderStyle = document.createElement('style');
    borderStyle.id = '{BORDER_STYLE_ID}';
    borderStyle.textContent =
        '@keyframes n1border{{' +
            'from{{opacity:0.625}}' +
            'to{{opacity:1}}' +
        '}}';
    root.appendChild(borderStyle);

    const border = document.createElement('div');
    border.id = '{GRADIENT_BORDER_ID}';
    // will-change + translateZ promote the element to its own GPU layer so
    // the opacity pulse never touches the page's compositor budget.
    border.style.cssText =
        'position:absolute;inset:0;pointer-events:none;filter:blur(4px);' +
        'will-change:opacity;transform:translateZ(0);' +
        'background-image:' +
            'linear-gradient(to right, rgba(29,205,152,0.4) 0%, transparent 6%),' +
            'linear-gradient(to left, rgba(29,205,152,0.4) 0%, transparent 6%),' +
            'linear-gradient(to bottom, rgba(29,205,152,0.4) 0%, transparent 6%),' +
            'linear-gradient(to top, rgba(29,205,152,0.4) 0%, transparent 6%);' +
        'animation:n1border {BORDER_CYCLE_MS // 2}ms ease-in-out infinite alternate;';
    root.appendChild(border);

    // Status chip intentionally not rendered — the thought card already
    // conveys what the agent is doing, so the "Analyzing" pill was redundant.
    // set_status / _current_status are retained (they gate thought-card
    // persistence).

    // Cursor lives in the persistent root so it survives navigation
    // (the persistent root is re-mounted on every domcontentloaded) and
    // the screenshot hide/restore cycle (only the persistent root is
    // restored after a screenshot). OverlayController calls
    // _restore_cursor_position right after _inject_persistent_root to
    // teleport the cursor back to its last known viewport coordinates,
    // so the user always sees it where it was, never off-screen.
{_YLOOP_ANIM_JS}
    if (!document.getElementById('{CURSOR_ID}')) {{
        const cursor = document.createElement('div');
        cursor.id = '{CURSOR_ID}';
        cursor.style.cssText = 'position:fixed;left:-200px;top:-200px;width:110px;height:130px;pointer-events:none;z-index:{Z_INDEX + 2};transition:left {CURSOR_TRANSITION_MS}ms ease-in-out,top {CURSOR_TRANSITION_MS}ms ease-in-out,opacity 200ms ease-in-out;transform:translate(-18.33px,-3.45px);';
        // The badge is a live element carrying the full Figma treatment (fill
        // gradient, gradient rim, two white inner shadows, teal drop shadows).
        // The thought capsule stretches THIS element, so expanded and idle
        // states are one continuous surface.
        const badge = document.createElement('div');
        badge.id = '{BADGE_ID}';
        badge.style.cssText = 'position:absolute;left:45.33px;top:27.37px;width:48px;height:48px;border-radius:10.962px;background:linear-gradient(211.4deg,#18AA7E 13.6%,#148F6A 43.9%,#148F6A 64%,#159871 80.8%);box-shadow:inset 1.1px -2.4px 4.7px rgba(255,255,255,0.15),inset -1.1px 3.6px 4.7px rgba(255,255,255,0.4),0 2.4px 2.4px rgba(16,103,111,0.07),0 9.6px 4.7px rgba(16,103,111,0.06),0 21.5px 6.6px rgba(16,103,111,0.04),0 38.4px 7.7px rgba(16,103,111,0.01);overflow:hidden;pointer-events:none;transition:width 340ms cubic-bezier(0.22,1,0.36,1),top 260ms ease-in-out;';
        // Slot pins the y-loop (and morphing action glyph) to the badge end.
        const slot = document.createElement('div');
        slot.id = '{BADGE_SLOT_ID}';
        slot.style.cssText = 'position:absolute;top:0;left:0;width:48px;height:48px;pointer-events:none;';
        const logo = document.createElement('div');
        logo.id = '{BADGE_LOGO_ID}';
        logo.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;transition:opacity 180ms ease;';
        n1BuildLoopLogo(logo);
        slot.appendChild(logo);
        const badgeGlyph = document.createElement('div');
        badgeGlyph.id = '{BADGE_GLYPH_ID}';
        badgeGlyph.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;display:flex;align-items:center;justify-content:center;opacity:0;';
        slot.appendChild(badgeGlyph);
        badge.appendChild(slot);
        const rim = document.createElement('div');
        rim.style.cssText = 'position:absolute;inset:0;border-radius:10.962px;padding:1px;background:linear-gradient(to bottom left,#5AE8BD,#127D5D 50%,#19B385);-webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none;';
        badge.appendChild(rim);
        cursor.appendChild(badge);
        const cursorImg = document.createElement('img');
        cursorImg.src = '{_POINTER_DATA_URI}';
        cursorImg.style.cssText = 'position:absolute;left:0;top:0;width:110px;height:130px;filter:drop-shadow(0 2px 5px rgba(0,0,0,0.18));';
        cursor.appendChild(cursorImg);
        root.appendChild(cursor);
    }}

    document.documentElement.appendChild(root);
}}"""


# Lightweight markdown→HTML renderer ported from yutori-ai/yutori
# navigator-browser-extension/sidepanel.js (renderMarkdown + escapeHtml).
# Kept as a raw triple-quoted string so backslash escapes (\n, \d, \w, \x00,
# etc.) reach the JS engine literally. Injected into _THOUGHT_CARD_JS via
# f-string interpolation — the substituted contents' braces are NOT re-parsed
# as f-string fields, which is why this file can stay readable.
#
# Coverage: fenced code blocks, inline code, [text](url) with scheme allow-list
# (https/mailto only), bare-URL linkification, ATX headers, **bold**, *italic*,
# - / 1. lists, soft line breaks. Sentinel-extracts code blocks/spans BEFORE
# escapeHtml so backtick contents survive the link/list/emphasis passes.
#
# Trust model: thought text comes from the Navigator LLM, not from the page
# being verified. escapeHtml runs on everything that isn't a fenced/inline code
# capture; link hrefs are scheme-checked; non-allow-listed schemes fall through
# as plain text. We render with innerHTML in good conscience.
_RENDER_MARKDOWN_JS = r"""
function n1escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
const N1_SAFE_LINK_SCHEME = /^(?:https?:\/\/|mailto:)/i;
function n1renderMarkdown(text) {
    if (!text) return '';
    const codeBlocks = [];
    text = text.replace(/```(\w*)\r?\n?([\s\S]*?)```/g, (_m, lang, code) => {
        const idx = codeBlocks.push({ lang, code }) - 1;
        return '\x00CB' + idx + '\x00';
    });
    const inlineCodes = [];
    text = text.replace(/`([^`]+)`/g, (_m, code) => {
        const idx = inlineCodes.push(code) - 1;
        return '\x00IC' + idx + '\x00';
    });
    let html = n1escapeHtml(text);
    html = html.replace(/\[([^\]]+)\]\(([^)\s'"]+)\)/g, (m, label, url) => {
        if (!N1_SAFE_LINK_SCHEME.test(url)) return m;
        return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
    });
    html = html.replace(
        /(^|[^"'>=])(https?:\/\/[^\s<>"']+)/g,
        (_m, pre, url) => pre + '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + '</a>'
    );
    html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    html = html.replace(/(?<![*\w])\*([^*]+)\*(?![*\w])/g, '<em>$1</em>');
    html = html.replace(/(?<![_\w])_([^_]+)_(?![_\w])/g, '<em>$1</em>');
    html = html.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>[\s\S]*?<\/li>)(?=\s*(?:<li>|$))/g, (_match, content, offset, string) => {
        const before = string.substring(0, offset);
        const isFirstInList = !before.endsWith('</li>\n') && !before.endsWith('</li>');
        return isFirstInList ? '<ul>' + content : content;
    });
    html = html.replace(/(<\/li>)(?!\s*<li>)/g, '$1</ul>');
    html = html.replace(/\n(?!<\/?(?:h[1-6]|ul|li|p|pre))/g, '<br>');
    html = html.replace(/(<br>){3,}/g, '<br><br>');
    html = html.replace(/\x00CB(\d+)\x00/g, (_m, idx) => {
        const block = codeBlocks[Number(idx)];
        const langClass = block.lang ? ' class="language-' + n1escapeHtml(block.lang) + '"' : '';
        const body = n1escapeHtml(block.code.replace(/\n$/, ''));
        return '<pre><code' + langClass + '>' + body + '</code></pre>';
    });
    html = html.replace(/\x00IC(\d+)\x00/g, (_m, idx) => {
        return '<code>' + n1escapeHtml(inlineCodes[Number(idx)]) + '</code>';
    });
    return html;
}
"""

# Yutori wordmark (Logotype, March 2025) sourced from yutori-ai/yutori
# navigator-browser-extension/icons/Yutori.Logotype.03.14.2025.svg. Inlined
# into the DOM (rather than loaded as an <img> data URI) so currentColor
# resolves — the original asset uses #334155 which would be invisible on the
# dark thought-card background. Single-quoted Python string with only
# double quotes inside makes Python's repr() emit a JS-valid string literal
# at f-string-substitution time.

# Markdown styles scoped to the thought card. The shimmer keyframe stays here
# so the existing single-style-element teardown still cleans everything up.
_THOUGHT_STYLE_CSS = (
    f"#{THOUGHT_CARD_ID} strong{{font-weight:700}}"
    f"#{THOUGHT_CARD_ID} em{{font-style:italic}}"
    f"#{THOUGHT_CARD_ID} a{{color:{YUTORI_GREEN};text-decoration:underline;text-underline-offset:2px}}"
    f"#{THOUGHT_CARD_ID} code{{background:rgba(255,255,255,0.08);padding:1px 5px;border-radius:4px;"
    "font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:0.92em}"
    f"#{THOUGHT_CARD_ID} pre{{background:rgba(0,0,0,0.32);border-radius:8px;padding:10px 12px;"
    "overflow-x:auto;margin:8px 0}"
    f"#{THOUGHT_CARD_ID} pre code{{background:transparent;padding:0;font-size:0.9em}}"
    f"#{THOUGHT_CARD_ID} ul{{padding-left:0;margin:0;list-style:none}}"
    f"#{THOUGHT_CARD_ID} li{{margin:0;padding:0}}"
    f"#{THOUGHT_CARD_ID} h2,#{THOUGHT_CARD_ID} h3,#{THOUGHT_CARD_ID} h4{{"
    "margin:0;font-weight:700;line-height:1.3}"
    f"#{THOUGHT_CARD_ID} h2{{font-size:1.18em}}"
    f"#{THOUGHT_CARD_ID} h3{{font-size:1.08em}}"
    f"#{THOUGHT_CARD_ID} h4{{font-size:1em;opacity:0.9}}"
)



_THOUGHT_CARD_JS = f"""(args) => {{
    {_RENDER_MARKDOWN_JS}
    const text = args.text || '';
    const timeoutMs = args.timeout_ms;
    const cx = args.cx;
    const cy = args.cy;
    const root = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (!root) return;
    {_inject_style_js(THOUGHT_STYLE_ID, repr(_THOUGHT_STYLE_CSS), guard=True)}
    const badge = document.getElementById('{BADGE_ID}');
    const slot = document.getElementById('{BADGE_SLOT_ID}');
    if (!badge || !slot) return;
    const existing = document.getElementById('{THOUGHT_CARD_ID}');
    const hadExisting = !!existing;
    if (existing) existing.remove();
    if (root.__n1ThoughtTimer) {{ clearTimeout(root.__n1ThoughtTimer); root.__n1ThoughtTimer = null; }}
    if (root.__n1CollapseTimer) {{ clearTimeout(root.__n1CollapseTimer); root.__n1CollapseTimer = null; }}

    // The thought stretches the badge element itself into a capsule — one
    // continuous surface whose rim + inner shadows wrap whatever size it has.
    // Short text stays a single-line pill; longer text grows to a taller
    // two-line pill and clamps with an ellipsis (rather than scrolling a
    // single-line marquee), keeping the y-loop centered in the grown pill.
    const B = 48, MAXW = 340, GAP = 10, END = 15, LH = 15;
    const TOP0 = 27.37;                     // badge's default top within the cursor box
    const goLeft = (cx != null && cx >= 0) && ((cx - 18.33 + 45.33 + MAXW + 14) > window.innerWidth);
    if (goLeft) {{
        badge.style.left = 'auto'; badge.style.right = '16.67px';
        slot.style.left = 'auto'; slot.style.right = '0';
    }} else {{
        badge.style.right = 'auto'; badge.style.left = '45.33px';
        slot.style.right = 'auto'; slot.style.left = '0';
    }}
    // Mirror the vertical badge flip from _move_cursor: near the bottom edge the
    // badge sits above the pointer, so a thought shown before the action (cursor
    // already low) doesn't hang the pill below the viewport. cy is -1 before the
    // first cursor move — leave the default top in that case. Keep the 72 / -68.5
    // constants in sync with _move_cursor.
    if (cy != null && cy >= 0) {{
        badge.style.top = (cy + 72 > window.innerHeight) ? '-68.5px' : (TOP0 + 'px');
    }}

    const vp = document.createElement('div');
    vp.id = '{THOUGHT_CARD_ID}';
    vp.style.cssText = 'position:absolute;top:0;bottom:0;overflow:hidden;display:flex;align-items:center;opacity:0;transition:opacity 140ms ease;pointer-events:none;'
        + (goLeft ? ('left:' + END + 'px;right:' + (B + GAP) + 'px;') : ('left:' + (B + GAP) + 'px;right:' + END + 'px;'));
    const inner = document.createElement('span');
    // Rendered single-line first so its natural width can be measured; switched
    // to a clamped two-line box below when it would exceed the max pill width.
    // Reasoning is rendered as sanitized markdown (n1renderMarkdown escapes HTML
    // and scheme-checks links) so emphasis/code/links/lists format instead of
    // showing raw syntax — matching the pre-redesign card.
    inner.style.cssText = 'display:inline-block;white-space:nowrap;text-align:left;background:linear-gradient(180deg,#C6FAFB 0%,#A4FBFC 55%,#9DFBFC 100%);-webkit-background-clip:text;background-clip:text;color:transparent;-webkit-text-fill-color:transparent;font-size:12px;line-height:' + LH + 'px;font-weight:500;letter-spacing:0.1px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;';
    inner.innerHTML = n1renderMarkdown(text);
    vp.appendChild(inner);
    badge.appendChild(vp);

    const applyExpand = () => {{
        requestAnimationFrame(() => {{
            if (!document.getElementById('{THOUGHT_CARD_ID}')) return;
            const singleW = inner.scrollWidth;
            const oneLine = (B + GAP + singleW + END) <= MAXW;
            const width = oneLine ? (B + GAP + singleW + END) : MAXW;
            if (!oneLine) {{
                // Cap at two lines via a block container + max-height, NOT
                // -webkit-line-clamp's -webkit-box: markdown block content (lists,
                // headers) lays out and clips LEFT-aligned here, whereas -webkit-box
                // only positions inline text and shoves block children toward center
                // (the "centered pill" bug). text-align:left guards page inheritance.
                inner.style.whiteSpace = 'normal';
                inner.style.width = (width - (B + GAP) - END) + 'px';
                inner.style.display = 'block';
                inner.style.textAlign = 'left';
                inner.style.maxHeight = (2 * LH) + 'px';
                inner.style.overflow = 'hidden';
            }}
            badge.style.width = width + 'px';
            vp.style.opacity = '1';
        }});
    }};
    if (hadExisting) {{
        // Replacing a visible thought: collapse the pill back to the 48px cursor
        // badge, then expand to fit the new text, so the green container visibly
        // shrinks and re-grows on each new reasoning (the badge's 340ms width
        // transition animates both). preview_action no longer clears the thought
        // between actions, so without this the pill would just resize in place.
        badge.style.width = B + 'px';
        root.__n1CollapseTimer = setTimeout(() => {{
            root.__n1CollapseTimer = null;
            applyExpand();
        }}, {THOUGHT_COLLAPSE_MS});
    }} else {{
        applyExpand();
    }}

    if (timeoutMs > 0) {{
        root.__n1ThoughtTimer = setTimeout(() => {{
            const current = document.getElementById('{THOUGHT_CARD_ID}');
            if (!current) return;
            current.style.opacity = '0';
            badge.style.width = B + 'px';
            root.__n1CollapseTimer = setTimeout(() => {{
                root.__n1CollapseTimer = null;
                current.remove();
                badge.style.left = '45.33px'; badge.style.right = 'auto';
                slot.style.left = '0'; slot.style.right = 'auto';
            }}, 360);
        }}, timeoutMs);
    }}
    return hadExisting;
}}"""

_TRANSIENT_ROOT_JS = f"""() => {{
    let root = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (!root) {{
        root = document.createElement('div');
        root.id = '{TRANSIENT_ROOT_ID}';
        root.style.cssText = '{_ROOT_STYLE}';
        document.documentElement.appendChild(root);
    }}
    {_set_visibility_opacity_js("root", visibility="visible", opacity="1")}
    // Cursor lives in the persistent root now (see _PERSISTENT_ROOT_JS).
    // Keeping it there means it survives navigations and screenshot
    // hide/restore cycles automatically — the user always sees the cursor
    // at its last known position. The transient root is reserved for
    // short-lived effect animations (clicks, scrolls, drags, types) that
    // self-clean via animationend.
}}"""

_REMOVE_ALL_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        if (persistent.__n1ThoughtTimer) clearTimeout(persistent.__n1ThoughtTimer);
        if (persistent.__n1CollapseTimer) clearTimeout(persistent.__n1CollapseTimer);
        persistent.remove();
    }}
    const transient = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (transient) transient.remove();
    for (const styleId of ['{BORDER_STYLE_ID}', '{CLICK_STYLE_ID}', '{DRAG_STYLE_ID}', '{THOUGHT_STYLE_ID}', '{BADGE_KF_STYLE_ID}']) {{
        const style = document.getElementById(styleId);
        if (style) style.remove();
    }}
}}"""

_CLEAR_THOUGHT_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent && persistent.__n1ThoughtTimer) {{
        clearTimeout(persistent.__n1ThoughtTimer);
        persistent.__n1ThoughtTimer = null;
    }}
    if (persistent && persistent.__n1CollapseTimer) {{
        clearTimeout(persistent.__n1CollapseTimer);
        persistent.__n1CollapseTimer = null;
    }}
    const vp = document.getElementById('{THOUGHT_CARD_ID}');
    const badge = document.getElementById('{BADGE_ID}');
    const slot = document.getElementById('{BADGE_SLOT_ID}');
    if (vp) vp.style.opacity = '0';
    if (badge) badge.style.width = '48px';
    const collapseTimer = setTimeout(() => {{
        if (persistent && persistent.__n1CollapseTimer === collapseTimer) persistent.__n1CollapseTimer = null;
        if (vp) vp.remove();
        if (badge) {{ badge.style.left = '45.33px'; badge.style.right = 'auto'; }}
        if (slot) {{ slot.style.left = '0'; slot.style.right = 'auto'; }}
    }}, 360);
    if (persistent) persistent.__n1CollapseTimer = collapseTimer;
}}"""

def _toggle_both_roots_js(*, visibility: str, opacity: str) -> str:
    """Generate JS to set visibility/opacity on both overlay roots."""
    return f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        {_set_visibility_opacity_js("persistent", visibility=visibility, opacity=opacity)}
    }}
    const transient = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (transient) {{
        {_set_visibility_opacity_js("transient", visibility=visibility, opacity=opacity)}
    }}
}}"""


# Keep the full-viewport overlay layers mounted during capture.
# In headed Chromium, toggling display on these layers can trigger
# a visible compositor snap even though the page viewport is unchanged.
_HIDE_BOTH_JS = _toggle_both_roots_js(visibility="hidden", opacity="0")
_RESTORE_PERSISTENT_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        {_set_visibility_opacity_js("persistent", visibility="visible", opacity="1")}
    }}
}}"""


class OverlayController:
    """Manage in-browser visual effects for a single claim lifecycle."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._active = False
        self._current_status = "Analyzing"
        # Bound handler kept on self so claim_ended can detach exactly the
        # listener we attached (anonymous lambdas would leak and survive
        # past the claim, re-injecting overlay into unrelated pages).
        self._navigation_handler: Any | None = None
        # Last known cursor position in CSS viewport coordinates. Updated by
        # every _move_cursor call. After navigation the persistent root is
        # re-injected with cursor at -200px,-200px (the off-screen sentinel);
        # _restore_cursor_position teleports it back to (_cursor_x, _cursor_y)
        # so the user always sees it where it was. Stays None until the first
        # action — fine, the off-screen sentinel is what we want pre-action.
        self._cursor_x: int | None = None
        self._cursor_y: int | None = None
        # Current reasoning text, if a thought capsule is showing. Kept so a
        # full-page navigation (which tears down the overlay DOM) can replay it
        # in _reinject_after_navigation — the reasoning is shown before the
        # action now, so without this it would vanish on the post-nav page.
        self._thought_text: str | None = None
        # Monotonic time when the current thought's shrink→expand finishes. A
        # click is held until then (see _await_thought_settled) so a navigating
        # click's reasoning is fully visible on the pre-nav page. None when
        # nothing is settling.
        self._thought_settle_at: float | None = None

    async def claim_started(self) -> None:
        self._active = True
        self._current_status = "Analyzing"
        # Re-mount the overlay after every main-frame navigation. Without
        # this, page.goto / link clicks tear down the DOM and the persistent
        # root only reappears the next time set_status or show_thought
        # happens to fire — visible "appears and disappears" flicker.
        # _inject_persistent_root is idempotent (id guard), so spurious
        # firings are harmless.
        # Defensive: a prior listener may still be subscribed if claim_started
        # was called twice without claim_ended between (page.on appends — it
        # doesn't replace). Detach first so we never leak a handler that
        # claim_ended can no longer reference.
        self._detach_navigation_listener()
        self._navigation_handler = lambda _frame=None: asyncio.create_task(
            self._reinject_after_navigation()
        )
        try:
            self._page.on("domcontentloaded", self._navigation_handler)
        except Exception:
            logger.debug("Failed to attach overlay navigation listener", exc_info=True)
            self._navigation_handler = None
        await self._inject_persistent_root()
        await self._restore_cursor_position()
        await self._ensure_transient_root()

    async def claim_ended(self) -> None:
        if not self._active:
            return
        self._active = False
        self._detach_navigation_listener()
        await self._eval(_REMOVE_ALL_JS)

    def _detach_navigation_listener(self) -> None:
        if self._navigation_handler is not None:
            try:
                self._page.remove_listener("domcontentloaded", self._navigation_handler)
            except Exception:
                logger.debug("Failed to detach overlay navigation listener", exc_info=True)
            self._navigation_handler = None

    async def _reinject_after_navigation(self) -> None:
        """Re-mount the overlay after a main-frame navigation tore down the DOM."""
        if not self._active:
            return
        await self._inject_persistent_root()
        # Persistent root re-creates the cursor at -200px,-200px (off-screen).
        # Teleport it back to the last known position so the user perceives
        # the cursor as continuously visible across the navigation.
        await self._restore_cursor_position()
        await self._ensure_transient_root()
        # Reasoning is shown before the action (synced), so a navigation would
        # otherwise drop the capsule for the rest of the turn. Replay it on the
        # rebuilt DOM so the thought survives the navigation.
        if self._thought_text is not None:
            await self.show_thought(self._thought_text)

    async def _restore_cursor_position(self) -> None:
        """Teleport the cursor to the last known position with no transition.

        Called after the persistent root is re-injected (claim_started or
        post-navigation) so the cursor doesn't appear at the off-screen
        sentinel between the persistent-root mount and the next action.
        No-op when no cursor move has happened yet — the off-screen
        sentinel is correct in that case.
        """
        if self._cursor_x is None or self._cursor_y is None:
            return
        await self._safe_evaluate(
            f"""() => {{
                const cursor = document.getElementById('{CURSOR_ID}');
                if (!cursor) return;
                const badge = document.getElementById('{BADGE_ID}');
                if (badge) badge.style.top = ({self._cursor_y} + 72 > window.innerHeight) ? '-68.5px' : '27.37px';
                cursor.style.transition = 'none';
                cursor.style.left = '{self._cursor_x}px';
                cursor.style.top = '{self._cursor_y}px';
                // Force reflow so the transition reset takes effect before
                // we restore the transition for subsequent moves.
                cursor.offsetHeight;
                cursor.style.transition = 'left {CURSOR_TRANSITION_MS}ms ease-in-out,top {CURSOR_TRANSITION_MS}ms ease-in-out,opacity 200ms ease-in-out';
            }}"""
        )

    async def preview_action(
        self,
        action_type: str,
        *,
        x: int = 0,
        y: int = 0,
        start_x: int = 0,
        start_y: int = 0,
        num_clicks: int = 1,
        direction: str = "down",
        amount: int = 1,
    ) -> None:
        if not self._active:
            return

        await self._ensure_transient_root()

        # Cursor-first choreography: move cursor to target, then trigger effect.
        # _move_cursor detects whether the cursor is at its off-screen initial
        # position (first move or after a full-page navigation destroyed the DOM)
        # and teleports instead of transitioning.
        center: dict[str, int] | None = None
        moved = False
        teleported = False
        if action_type in {"type", "copy", "paste"}:
            center = await self._get_focused_element_center()
            if center:
                teleported = await self._move_cursor(center["x"], center["y"])
                moved = True
        elif action_type == "drag":
            teleported = await self._move_cursor(start_x, start_y)
            moved = True
        else:
            teleported = await self._move_cursor(x, y)
            moved = True

        if moved:
            if teleported:
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(CURSOR_TRANSITION_MS / 1000)

        if action_type in {"left_click", "double_click", "triple_click", "middle_click", "right_click"}:
            # Hold the click until the reasoning capsule has finished expanding,
            # so a navigating click's thought is fully visible on the pre-nav page
            # instead of finishing its expand on the destination. The cursor glide
            # above overlaps the expand, so this usually adds little; a thought
            # shown earlier in the turn is already settled (no-op).
            await self._await_thought_settled()
            await self._show_click_effect(x, y, num_clicks)
        elif action_type == "scroll":
            _rot = {"down": 0, "up": 180, "right": -90, "left": 90}.get(direction, 0)
            await self._morph_badge(_GLYPH_CHEVRON, rotate=_rot)
        elif action_type == "type":
            await self._morph_badge(_GLYPH_TYPE)
        elif action_type == "set_element_value":
            await self._morph_badge(_GLYPH_PASTE)
        elif action_type == "copy":
            await self._morph_badge(_GLYPH_COPY)
        elif action_type == "paste":
            await self._morph_badge(_GLYPH_PASTE)
        elif action_type == "drag":
            await self._show_drag_effect(start_x, start_y, x, y)

    async def set_status(self, label: str) -> None:
        self._current_status = label
        if not self._active:
            return
        # A status change no longer clears the thought: the reasoning capsule is
        # shown synced with its action (by claim_verifier, before the action runs)
        # and replaced per-turn by the next show_thought / explicit clear_thought.
        # Clearing here would wipe a just-shown reasoning when an action flips the
        # chip to "Running …"/"Pressing keys". The evidence screenshot still hides
        # the whole overlay, so the model never reads the reasoning off a capture.
        await self._inject_persistent_root()

    async def show_thought(self, text: str) -> None:
        if not self._active:
            return
        self._thought_text = text
        await self._inject_persistent_root()
        clipped = self._clip_text(text, 520)
        # During "Analyzing" the card stays until clear_thought() is called;
        # otherwise the fallback timeout removes it.
        timeout_ms = 0 if self._current_status == "Analyzing" else THOUGHT_DURATION_MS
        had_existing = await self._safe_evaluate(
            _THOUGHT_CARD_JS,
            {
                "text": clipped,
                "timeout_ms": timeout_ms,
                "cx": self._cursor_x if self._cursor_x is not None else -1,
                "cy": self._cursor_y if self._cursor_y is not None else -1,
            },
        )
        # Record when the shrink→expand settles so a click can hold until the pill
        # is fully visible (see _await_thought_settled). The card builder collapses
        # then re-expands (collapse+expand) only when a card already existed in the
        # DOM; a fresh mount — the first thought, or a post-navigation replay onto a
        # rebuilt DOM — expands only. Derive this from the DOM (the JS's returned
        # hadExisting), not from _thought_text: a replay sets _thought_text but runs
        # no collapse, so trusting Python state would add a phantom ~360ms hold.
        settle_ms = (THOUGHT_COLLAPSE_MS + THOUGHT_EXPAND_MS) if had_existing else THOUGHT_EXPAND_MS
        self._thought_settle_at = time.monotonic() + settle_ms / 1000

    async def clear_thought(self) -> None:
        self._thought_text = None
        self._thought_settle_at = None
        if not self._active:
            return
        await self._eval(_CLEAR_THOUGHT_JS)

    async def _await_thought_settled(self) -> None:
        """Wait until the current thought capsule has finished its shrink→expand.

        Lets a navigating click hold until the reasoning is fully visible on the
        pre-navigation page. No-op when nothing is settling (already past, or no
        thought shown). Visualize-only pacing: the real click just fires slightly
        later, like the cursor-glide sleep — Navigator's behavior is unchanged.
        """
        if not self._active or self._thought_settle_at is None:
            return
        remaining = self._thought_settle_at - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def before_screenshot(self) -> None:
        if not self._active:
            return
        await self._eval(_HIDE_BOTH_JS)

    async def after_screenshot(self) -> None:
        if not self._active:
            return
        await self._eval(_RESTORE_PERSISTENT_JS)
        # Leave the transient root hidden; the next preview_action call re-shows it.


    async def _inject_persistent_root(self) -> None:
        await self._eval(_PERSISTENT_ROOT_JS)

    async def _move_cursor(self, x: int, y: int) -> bool:
        """Move the branded cursor to the given viewport coordinates.

        Returns True if the cursor was teleported (off-screen → target),
        False if it used the CSS transition.  Teleporting happens on the
        first move of a claim and after full-page navigations that destroy
        and recreate the cursor element.

        Also updates ``self._cursor_x`` / ``self._cursor_y`` so
        ``_restore_cursor_position`` can replay the move after the next
        navigation. We update unconditionally — if the JS evaluate fails
        (page closed, etc.) the saved position is still the right thing
        to restore on the next page.
        """
        teleported = bool(await self._safe_evaluate(
            f"""() => {{
                const cursor = document.getElementById('{CURSOR_ID}');
                if (!cursor) return true;
                // Flip the 48px badge above the pointer near the bottom edge so it
                // doesn't clip off-screen (it otherwise hangs ~72px below the tip).
                const badge = document.getElementById('{BADGE_ID}');
                if (badge) badge.style.top = ({y} + 72 > window.innerHeight) ? '-68.5px' : '27.37px';
                // The thought pill's horizontal side (left/right of the badge) was
                // chosen at show_thought time from the *previous* cursor x. Recompute
                // it for the new x so the capsule can't run off-screen after the cursor
                // moves to the action target. Mirrors goLeft in _THOUGHT_CARD_JS — keep
                // the constants and formula in sync with it.
                const card = document.getElementById('{THOUGHT_CARD_ID}');
                const slot = document.getElementById('{BADGE_SLOT_ID}');
                if (card && badge && slot) {{
                    const B = 48, MAXW = 340, GAP = 10, END = 15;
                    const goLeft = ({x} + (-18.33 + 45.33 + MAXW + 14)) > window.innerWidth;
                    if (goLeft) {{
                        badge.style.left = 'auto'; badge.style.right = '16.67px';
                        slot.style.left = 'auto'; slot.style.right = '0';
                        card.style.left = END + 'px'; card.style.right = (B + GAP) + 'px';
                    }} else {{
                        badge.style.right = 'auto'; badge.style.left = '45.33px';
                        slot.style.right = 'auto'; slot.style.left = '0';
                        card.style.left = (B + GAP) + 'px'; card.style.right = END + 'px';
                    }}
                }}
                const offScreen = cursor.style.left === '-200px';
                if (offScreen) {{
                    cursor.style.transition = 'none';
                    cursor.style.left = '{x}px';
                    cursor.style.top = '{y}px';
                    cursor.offsetHeight;
                    cursor.style.transition = 'left {CURSOR_TRANSITION_MS}ms ease-in-out,top {CURSOR_TRANSITION_MS}ms ease-in-out,opacity 200ms ease-in-out';
                    return true;
                }}
                cursor.style.left = '{x}px';
                cursor.style.top = '{y}px';
                return false;
            }}""",
            default=True,
        ))
        self._cursor_x = x
        self._cursor_y = y
        return teleported

    async def _show_click_effect(self, x: int, y: int, num_clicks: int) -> None:
        # Ripple ring + glowing center dot, matching the Navigator browser
        # extension's click effect (visual_effects.js showClickEffect): an
        # expanding ring fades out while a center dot shrinks and fades.
        gap = int(CLICK_DURATION_MS * 0.5)
        click_style = _inject_style_js(
            CLICK_STYLE_ID,
            (
                "'@keyframes n1clickring{0%{transform:translate(-50%,-50%) scale(0.4);opacity:1}"
                "50%{opacity:0.9}100%{transform:translate(-50%,-50%) scale(2.5);opacity:0}}"
                "@keyframes n1clickdot{0%{transform:translate(-50%,-50%) scale(1);opacity:1}"
                "50%{opacity:1}100%{transform:translate(-50%,-50%) scale(0.4);opacity:0}}'"
            ),
            guard=True,
        )
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                {click_style}
                for (let i = 0; i < {num_clicks}; i++) {{
                    const delay = i * {gap};
                    const ring = document.createElement('div');
                    ring.style.cssText = 'position:fixed;left:{x}px;top:{y}px;width:50px;height:50px;border:3px solid {YUTORI_GREEN};border-radius:50%;box-shadow:0 0 20px rgba(29,205,152,0.6),inset 0 0 12px rgba(29,205,152,0.2);pointer-events:none;z-index:{Z_INDEX};transform:translate(-50%,-50%);opacity:0;animation:n1clickring {CLICK_DURATION_MS}ms ease-out forwards;animation-delay:' + delay + 'ms;';
                    const dot = document.createElement('div');
                    dot.style.cssText = 'position:fixed;left:{x}px;top:{y}px;width:14px;height:14px;background:{YUTORI_GREEN};border-radius:50%;box-shadow:0 0 16px {YUTORI_GREEN},0 0 32px rgba(29,205,152,0.5);pointer-events:none;z-index:{Z_INDEX};transform:translate(-50%,-50%);opacity:0;animation:n1clickdot {CLICK_DURATION_MS}ms ease-out forwards;animation-delay:' + delay + 'ms;';
                    ring.addEventListener('animationend', () => {{ ring.remove(); dot.remove(); }}, {{once: true}});
                    root.appendChild(ring);
                    root.appendChild(dot);
                }}
            }}"""
        )

    async def _show_drag_effect(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        drag_style = _inject_style_js(
            DRAG_STYLE_ID,
            "'@keyframes n1dfade{0%{opacity:0.5}100%{opacity:0}}@keyframes n1dtrail{0%{opacity:0.6}100%{opacity:0}}'",
        )
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                {drag_style}
                const pressed = document.createElement('div');
                pressed.style.cssText = 'position:fixed;left:{start_x}px;top:{start_y}px;width:8px;height:8px;background:rgba(29,205,152,0.5);border-radius:50%;transform:translate(-50%,-50%);pointer-events:none;z-index:{Z_INDEX};animation:n1dfade {DRAG_DURATION_MS + 100}ms ease-out forwards;';
                root.appendChild(pressed);

                const dx = {end_x} - {start_x};
                const dy = {end_y} - {start_y};
                const length = Math.sqrt(dx * dx + dy * dy);
                const angle = Math.atan2(dy, dx) * 180 / Math.PI;
                const trail = document.createElement('div');
                trail.style.cssText = 'position:fixed;left:{start_x}px;top:{start_y}px;width:' + length + 'px;height:2px;background:linear-gradient(to right,{YUTORI_GREEN},transparent);transform-origin:0 50%;transform:translateY(-50%) rotate(' + angle + 'deg);pointer-events:none;z-index:{Z_INDEX};animation:n1dtrail {DRAG_DURATION_MS + 200}ms ease-out forwards;';
                root.appendChild(trail);

                // Trail animation outlasts the pressed dot, so its animationend
                // is the right signal to tear down both elements + the style.
                trail.addEventListener('animationend', () => {{ pressed.remove(); trail.remove(); style.remove(); }}, {{once: true}});
            }}"""
        )

    async def _morph_badge(self, glyph_svg: str, *, rotate: int = 0, revert_ms: int = 900) -> None:
        # Make the cursor badge the locus of interaction: the action glyph blooms
        # in over the badge (scale+blur+opacity spring, AnimatePresence-style),
        # then blooms back out to reveal the default y-loop after revert_ms.
        rot = f"transform:rotate({rotate}deg);" if rotate else ""
        inner = f"<div style='width:62%;height:62%;display:flex;align-items:center;justify-content:center;{rot}'>{glyph_svg}</div>"
        js = (
            "() => {"
            f" const b = document.getElementById('{BADGE_GLYPH_ID}');"
            " if (!b) return;"
            f" const logo = document.getElementById('{BADGE_LOGO_ID}');"
            " if (logo) logo.style.opacity = '0';"
            f" if (!document.getElementById('{BADGE_KF_STYLE_ID}')) {{"
            f"   const st = document.createElement('style'); st.id = '{BADGE_KF_STYLE_ID}';"
            "   st.textContent = '@keyframes n1badgeIn{0%{opacity:0;transform:scale(0.25);filter:blur(4px)}100%{opacity:1;transform:scale(1);filter:blur(0)}}"
            "@keyframes n1badgeOut{0%{opacity:1;transform:scale(1);filter:blur(0)}100%{opacity:0;transform:scale(0.25);filter:blur(4px)}}';"
            "   document.head.appendChild(st);"
            " }"
            f" b.innerHTML = {json.dumps(inner)};"
            " b.style.animation = 'none'; void b.offsetWidth;"
            " b.style.animation = 'n1badgeIn 300ms cubic-bezier(0.22,1,0.36,1) forwards';"
            " if (b.__n1RevertTimer) clearTimeout(b.__n1RevertTimer);"
            f" b.__n1RevertTimer = setTimeout(() => {{ b.style.animation = 'n1badgeOut 260ms cubic-bezier(0.4,0,1,1) forwards'; if (logo) logo.style.opacity = '1'; }}, {revert_ms});"
            " }"
        )
        await self._eval(js)


    async def _get_focused_element_center(self) -> dict[str, int] | None:
        return await self._safe_evaluate(
            """() => {
                const element = document.activeElement;
                if (!element || element === document.body || element === document.documentElement) return null;
                const rect = element.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return null;
                return {
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                };
            }"""
        )

    async def _ensure_transient_root(self) -> None:
        await self._eval(_TRANSIENT_ROOT_JS)

    async def _safe_evaluate(
        self, script: str, arg: object | None = None, *, default: Any = None
    ) -> Any:
        """Best-effort ``page.evaluate``; return ``default`` on failure (logged at DEBUG)."""
        return await safe_page_evaluate(self._page, script, arg, default=default, log_label="Overlay")

    async def _eval(self, script: str, arg: object | None = None) -> None:
        await self._safe_evaluate(script, arg)

    @staticmethod
    def _clip_text(text: str, limit: int) -> str:
        # Preserve newlines: n1renderMarkdown's block features (headers, lists,
        # fenced code) are line-anchored and would all be dead after a
        # whitespace-collapsing clip.
        return clip_text_preserving_lines(str(text), limit, ellipsis="…")
