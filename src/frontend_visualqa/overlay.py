"""In-browser visual overlay for headed mode action visualization."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING, Any

from frontend_visualqa.text_utils import clip_text

if TYPE_CHECKING:
    from playwright.async_api import Page


logger = logging.getLogger(__name__)

Z_INDEX = 2_147_483_646
YUTORI_GREEN = "#1DCD98"
EFFECT_DURATION_MS = 600
BORDER_CYCLE_MS = 1000

PERSISTENT_ROOT_ID = "__n1PersistentRoot"
TRANSIENT_ROOT_ID = "__n1TransientRoot"
GRADIENT_BORDER_ID = "__n1GradientBorder"
BORDER_STYLE_ID = "__n1BorderStyle"
STATUS_CHIP_ID = "__n1StatusChip"
THOUGHT_CARD_ID = "__n1ThoughtCard"
THOUGHT_STYLE_ID = "__n1ThoughtStyle"
CLICK_STYLE_ID = "__n1ClickStyle"
SCROLL_STYLE_ID = "__n1ScrollStyle"
TYPE_STYLE_ID = "__n1TypeStyle"

CURSOR_ID = "__n1Cursor"
DRAG_STYLE_ID = "__n1DragStyle"
CLICK_DURATION_MS = 250
SCROLL_DURATION_MS = 1000
DRAG_DURATION_MS = 200
CURSOR_TRANSITION_MS = 350
THOUGHT_DURATION_MS = 2000

_CURSOR_SVG = (
    '<svg width="134" height="181" viewBox="0 0 134 181" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<g id="Yutori Cursor"><g id="Yutori Cursor_2" filter="url(#filter0_ddddii_2284_14081)">'
    '<path d="M31.1586 11.5639C29.9123 8.57945 32.7297 5.50285 35.812 6.48228L99.1562 26.6099C104.603 28.3406 104.391 36.1195 98.8584 37.5515L92.5099 39.1945C80.7217 42.2453 71.8947 52.0409 70.0833 64.0819L68.6149 73.8422C67.7576 79.541 59.9482 80.5074 57.7275 75.1895L31.1586 11.5639Z" fill="url(#paint0_linear_2284_14081)"/>'
    '<path d="M31.1586 11.5639C29.9123 8.57945 32.7297 5.50285 35.812 6.48228L99.1562 26.6099C104.603 28.3406 104.391 36.1195 98.8584 37.5515L92.5099 39.1945C80.7217 42.2453 71.8947 52.0409 70.0833 64.0819L68.6149 73.8422C67.7576 79.541 59.9482 80.5074 57.7275 75.1895L31.1586 11.5639Z" stroke="url(#paint1_linear_2284_14081)" stroke-width="1.89844"/>'
    '</g></g><defs>'
    '<filter id="filter0_ddddii_2284_14081" x="0.655027" y="0.845703" width="132.671" height="180.046" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB">'
    '<feFlood flood-opacity="0" result="BackgroundImageFix"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="4.5"/><feGaussianBlur stdDeviation="4.5"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.07 0"/>'
    '<feBlend mode="normal" in2="BackgroundImageFix" result="effect1_dropShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="18"/><feGaussianBlur stdDeviation="9"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.06 0"/>'
    '<feBlend mode="normal" in2="effect1_dropShadow_2284_14081" result="effect2_dropShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="40.5"/><feGaussianBlur stdDeviation="12.375"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.04 0"/>'
    '<feBlend mode="normal" in2="effect2_dropShadow_2284_14081" result="effect3_dropShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dy="72"/><feGaussianBlur stdDeviation="14.625"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 0.0627451 0 0 0 0 0.403922 0 0 0 0 0.435294 0 0 0 0.01 0"/>'
    '<feBlend mode="normal" in2="effect3_dropShadow_2284_14081" result="effect4_dropShadow_2284_14081"/>'
    '<feBlend mode="normal" in="SourceGraphic" in2="effect4_dropShadow_2284_14081" result="shape"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dx="2.25" dy="-4.5"/><feGaussianBlur stdDeviation="4.5"/>'
    '<feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.15 0"/>'
    '<feBlend mode="normal" in2="shape" result="effect5_innerShadow_2284_14081"/>'
    '<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>'
    '<feOffset dx="-2.25" dy="6.75"/><feGaussianBlur stdDeviation="4.5"/>'
    '<feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1"/>'
    '<feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.4 0"/>'
    '<feBlend mode="normal" in2="effect5_innerShadow_2284_14081" result="effect6_innerShadow_2284_14081"/>'
    '</filter>'
    '<linearGradient id="paint0_linear_2284_14081" x1="79.2133" y1="2.92857" x2="31.285" y2="73.7171" gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#18AA7E"/><stop offset="0.45" stop-color="#148F6A"/>'
    '<stop offset="0.75" stop-color="#148F6A"/><stop offset="1" stop-color="#159871"/>'
    '</linearGradient>'
    '<linearGradient id="paint1_linear_2284_14081" x1="78.201" y1="2.92858" x2="32.6124" y2="76.674" gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#5AE8BD"/><stop offset="0.5" stop-color="#127D5D"/>'
    '<stop offset="0.9" stop-color="#148F6A"/><stop offset="1" stop-color="#19B385"/>'
    '</linearGradient>'
    '</defs></svg>'
)

_CURSOR_DATA_URI = "data:image/svg+xml;base64," + base64.b64encode(_CURSOR_SVG.encode()).decode()

_ROOT_STYLE = (
    f"position:fixed;top:0;left:0;right:0;bottom:0;"
    f"pointer-events:none;z-index:{Z_INDEX};visibility:visible;opacity:1;"
)


def _set_visibility_opacity_js(element_ref: str, *, visibility: str, opacity: str) -> str:
    return (
        f"{element_ref}.style.visibility = '{visibility}';"
        f"{element_ref}.style.opacity = '{opacity}';"
    )

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

    const chip = document.createElement('div');
    chip.id = '{STATUS_CHIP_ID}';
    chip.style.cssText = 'position:fixed;top:12px;right:12px;background:{YUTORI_GREEN};color:#000;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:10px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;padding:6px 12px;border-radius:999px;box-shadow:0 2px 10px rgba(29,205,152,0.45);z-index:{Z_INDEX + 1};';
    chip.textContent = 'Analyzing';
    root.appendChild(chip);

    document.documentElement.appendChild(root);
}}"""

# Lightweight markdown→HTML renderer ported from yutori-ai/yutori
# navigator-browser-extension/sidepanel.js (renderMarkdown + escapeHtml).
# Kept as a raw triple-quoted string so backslash escapes (\n, \d, \w, \u0000,
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
        return '\u0000CB' + idx + '\u0000';
    });
    const inlineCodes = [];
    text = text.replace(/`([^`]+)`/g, (_m, code) => {
        const idx = inlineCodes.push(code) - 1;
        return '\u0000IC' + idx + '\u0000';
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
    html = html.replace(/\u0000CB(\d+)\u0000/g, (_m, idx) => {
        const block = codeBlocks[Number(idx)];
        const langClass = block.lang ? ' class="language-' + n1escapeHtml(block.lang) + '"' : '';
        const body = n1escapeHtml(block.code.replace(/\n$/, ''));
        return '<pre><code' + langClass + '>' + body + '</code></pre>';
    });
    html = html.replace(/\u0000IC(\d+)\u0000/g, (_m, idx) => {
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
_YUTORI_LOGOTYPE_SVG = '<svg width="248" height="63" viewBox="0 0 248 63" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M234.52 13.9307C232.514 13.9307 230.851 13.2714 229.533 11.9529C228.214 10.6343 227.555 8.97182 227.555 6.96534C227.555 4.95887 228.214 3.29636 229.533 1.97781C230.851 0.659272 232.514 0 234.52 0C236.527 0 238.189 0.659272 239.508 1.97781C240.826 3.29636 241.485 4.95887 241.485 6.96534C241.485 8.97182 240.826 10.6343 239.508 11.9529C238.189 13.2714 236.527 13.9307 234.52 13.9307ZM221.019 61.5702V60.1943L226.953 57.4426V30.9571L221.277 27.2594V25.8836L241.055 21.154H242.431V57.4426L247.333 60.1943V61.5702H221.019Z" fill="currentColor"/><path d="M182.034 61.5702V60.1943L187.968 57.4426V31.817L182.292 28.1194V26.7435L201.21 21.154H202.586L203.36 29.1513H203.79C205.281 26.9155 206.57 25.2243 207.66 24.0777C208.749 22.9312 209.752 22.1573 210.669 21.756C211.644 21.3547 212.733 21.154 213.937 21.154C214.453 21.154 214.998 21.2113 215.571 21.326C216.144 21.4407 216.689 21.584 217.205 21.756C218.179 22.0426 218.839 22.4152 219.183 22.8739C219.584 23.2751 219.785 23.7338 219.785 24.2497C219.785 24.651 219.699 25.1096 219.527 25.6256L217.205 32.161H216.603L215.055 31.645C214.08 31.3011 213.221 31.0718 212.475 30.9571C211.787 30.7851 210.899 30.6991 209.81 30.6991C208.548 30.6991 207.373 31.0144 206.284 31.645C205.195 32.2183 204.249 32.9636 203.446 33.8808V57.4426L210.927 60.1943V61.5702H182.034Z" fill="currentColor"/><path d="M158.321 62.4301C154.079 62.4301 150.324 61.4842 147.056 59.5924C143.789 57.6432 141.237 55.1208 139.403 52.0251C137.626 48.8721 136.737 45.4611 136.737 41.7921C136.737 38.1231 137.626 34.7121 139.403 31.559C141.237 28.406 143.789 25.8836 147.056 23.9917C150.324 22.0999 154.079 21.154 158.321 21.154C162.563 21.154 166.318 22.0999 169.586 23.9917C172.854 25.8836 175.376 28.406 177.153 31.559C178.988 34.7121 179.905 38.1231 179.905 41.7921C179.905 45.4611 178.988 48.8721 177.153 52.0251C175.376 55.1208 172.854 57.6432 169.586 59.5924C166.318 61.4842 162.563 62.4301 158.321 62.4301ZM159.095 57.8726C160.299 57.8726 161.302 57.2133 162.105 55.8948C162.907 54.5762 163.481 52.8564 163.825 50.7352C164.226 48.5568 164.427 46.149 164.427 43.5119C164.427 40.3015 164.197 37.3492 163.739 34.6547C163.28 31.9603 162.535 29.7819 161.503 28.1194C160.528 26.4569 159.267 25.6256 157.719 25.6256C156.401 25.6256 155.34 26.2849 154.538 27.6034C153.735 28.8646 153.133 30.5845 152.732 32.7629C152.388 34.8841 152.216 37.2918 152.216 39.9862C152.216 43.1393 152.445 46.0917 152.904 48.8434C153.42 51.5378 154.165 53.7163 155.139 55.3788C156.171 57.0413 157.49 57.8726 159.095 57.8726Z" fill="currentColor"/><path d="M123.627 62.4301C119.499 62.4301 116.174 61.6849 113.652 60.1943C111.129 58.7038 109.868 56.6113 109.868 53.9169V25.7976H104.279V24.1637L112.62 22.0139L122.853 12.5548H125.347V22.0139H137.557V25.7976H125.347V51.2512C125.347 52.7417 125.949 53.8309 127.152 54.5189C128.414 55.2068 129.876 55.5508 131.538 55.5508C132.57 55.5508 133.459 55.4934 134.204 55.3788C135.006 55.2641 135.838 55.1208 136.698 54.9488L136.956 55.1208V57.0126C135.809 58.4458 133.946 59.7071 131.366 60.7963C128.844 61.8855 126.264 62.4301 123.627 62.4301Z" fill="currentColor"/><path d="M71.4835 62.4301C67.9865 62.4301 65.1201 61.5129 62.8843 59.6784C60.7058 57.8439 59.6166 55.1208 59.6166 51.5092V26.1415L53.6831 23.3898V22.0139H75.0951V48.3275C75.0951 49.99 75.5251 51.2798 76.385 52.1971C77.2449 53.057 78.3342 53.487 79.6527 53.487C80.398 53.487 81.1719 53.315 81.9745 52.971C82.8344 52.6271 83.6083 52.2258 84.2963 51.7671V26.1415L79.6527 23.3898V22.0139H99.7748V57.4426L104.676 60.1943V61.5702H84.2963V56.0667H83.9523C82.0605 57.9585 80.1973 59.5064 78.3628 60.7103C76.5283 61.8569 74.2352 62.4301 71.4835 62.4301Z" fill="currentColor"/><path d="M17.1124 61.5702V60.1943L24.7657 56.5827V35.0847L6.19142 9.80308L0 6.19142V4.81555H30.3551V6.19142L23.7338 9.5451V9.88907L38.4384 29.6672L51.5951 10.0611V9.71709L44.7158 6.19142V4.81555H65.1819V6.19142L59.2484 9.02915L41.1041 35.0847V56.5827L48.7574 60.1943V61.5702H17.1124Z" fill="currentColor"/></svg>'

# Markdown styles scoped to the thought card. The shimmer keyframe stays here
# so the existing single-style-element teardown still cleans everything up.
_THOUGHT_STYLE_CSS = (
    "@keyframes n1thoughtShimmer{0%{background-position:0% 50%}100%{background-position:200% 50%}}"
    f"#{THOUGHT_CARD_ID} strong{{font-weight:700}}"
    f"#{THOUGHT_CARD_ID} em{{font-style:italic}}"
    f"#{THOUGHT_CARD_ID} a{{color:{YUTORI_GREEN};text-decoration:underline;text-underline-offset:2px}}"
    f"#{THOUGHT_CARD_ID} code{{background:rgba(255,255,255,0.08);padding:1px 5px;border-radius:4px;"
    "font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:0.92em}"
    f"#{THOUGHT_CARD_ID} pre{{background:rgba(0,0,0,0.32);border-radius:8px;padding:10px 12px;"
    "overflow-x:auto;margin:8px 0}"
    f"#{THOUGHT_CARD_ID} pre code{{background:transparent;padding:0;font-size:0.9em}}"
    f"#{THOUGHT_CARD_ID} ul{{padding-left:18px;margin:6px 0}}"
    f"#{THOUGHT_CARD_ID} li{{margin:2px 0}}"
    f"#{THOUGHT_CARD_ID} h2,#{THOUGHT_CARD_ID} h3,#{THOUGHT_CARD_ID} h4{{"
    "margin:8px 0 4px;font-weight:700;line-height:1.3}"
    f"#{THOUGHT_CARD_ID} h2{{font-size:1.18em}}"
    f"#{THOUGHT_CARD_ID} h3{{font-size:1.08em}}"
    f"#{THOUGHT_CARD_ID} h4{{font-size:1em;opacity:0.9}}"
)

_THOUGHT_CARD_JS = f"""(args) => {{
    {_RENDER_MARKDOWN_JS}
    const text = args.text;
    const timeoutMs = args.timeout_ms;
    const root = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (!root) return;
    const existing = document.getElementById('{THOUGHT_CARD_ID}');
    if (existing) existing.remove();

    const style = document.getElementById('{THOUGHT_STYLE_ID}') || document.createElement('style');
    style.id = '{THOUGHT_STYLE_ID}';
    style.textContent = {_THOUGHT_STYLE_CSS!r};
    if (!style.isConnected) root.appendChild(style);

    const card = document.createElement('div');
    card.id = '{THOUGHT_CARD_ID}';
    card.style.cssText = 'position:fixed;top:76px;left:50%;transform:translateX(-50%);width:min(720px,calc(100vw - 48px));pointer-events:none;z-index:{Z_INDEX + 1};padding:22px 24px;border-radius:22px;background:linear-gradient(180deg,rgba(8,16,20,0.96),rgba(6,12,16,0.92));border:1px solid rgba(29,205,152,0.28);box-shadow:0 18px 50px rgba(0,0,0,0.28),0 0 0 1px rgba(29,205,152,0.08) inset;backdrop-filter:blur(12px);color:#eef6f3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;';
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:12px;';
    const badge = document.createElement('span');
    badge.style.cssText = 'display:inline-block;padding:5px 10px;border-radius:999px;background:linear-gradient(90deg,rgba(29,205,152,0.22),rgba(90,232,189,0.45),rgba(29,205,152,0.22));background-size:200% 200%;animation:n1thoughtShimmer 1.6s linear infinite;color:#d9fff1;font-size:11px;font-weight:800;letter-spacing:0.9px;text-transform:uppercase;';
    badge.textContent = 'Thinking';

    // Yutori wordmark, placed to the LEFT of the "Thinking" pill (appended
    // before the badge). Inline SVG so currentColor takes effect — the asset
    // uses #334155 by default which would be invisible on the dark card.
    // setAttribute('style', ...) on the parsed <svg> node is the only way to
    // size it after innerHTML insertion (the SVG came with explicit
    // width/height attributes that we override here).
    const brand = document.createElement('span');
    brand.setAttribute('aria-label', 'Yutori');
    brand.style.cssText = 'display:inline-flex;align-items:center;color:rgba(238,246,243,0.55);';
    brand.innerHTML = {_YUTORI_LOGOTYPE_SVG!r};
    const brandSvg = brand.querySelector('svg');
    if (brandSvg) brandSvg.setAttribute('style', 'height:14px;width:auto;display:block');
    header.appendChild(brand);
    header.appendChild(badge);

    // Body uses innerHTML (filled by renderMarkdown). The line-clamp the
    // previous textContent version used would render unpredictably for rich
    // content (lists, headers); upstream show_thought already clips text to
    // 520 chars, so we cap with max-height + overflow:hidden as a safety net.
    const body = document.createElement('div');
    body.style.cssText = 'font-size:17px;line-height:1.55;color:rgba(238,246,243,0.95);max-height:60vh;overflow:hidden;word-break:break-word;';
    body.innerHTML = n1renderMarkdown(text);

    card.appendChild(header);
    card.appendChild(body);
    root.appendChild(card);

    const previousTimer = root.__n1ThoughtTimer;
    if (previousTimer) clearTimeout(previousTimer);
    root.__n1ThoughtTimer = null;
    if (timeoutMs > 0) {{
        root.__n1ThoughtTimer = setTimeout(() => {{
            const current = document.getElementById('{THOUGHT_CARD_ID}');
            if (current) current.remove();
        }}, timeoutMs);
    }}
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

    if (!document.getElementById('{CURSOR_ID}')) {{
        const cursor = document.createElement('img');
        cursor.id = '{CURSOR_ID}';
        cursor.src = '{_CURSOR_DATA_URI}';
        cursor.style.cssText = 'position:fixed;left:-200px;top:-200px;width:75px;height:101px;pointer-events:none;z-index:{Z_INDEX + 2};transition:left {CURSOR_TRANSITION_MS}ms ease-in-out,top {CURSOR_TRANSITION_MS}ms ease-in-out;transform:translate(-17px,-4px);filter:drop-shadow(0 2px 5px rgba(0,0,0,0.18));';
        root.appendChild(cursor);
    }}
}}"""

_REMOVE_ALL_JS = f"""() => {{
    const persistent = document.getElementById('{PERSISTENT_ROOT_ID}');
    if (persistent) {{
        if (persistent.__n1ThoughtTimer) clearTimeout(persistent.__n1ThoughtTimer);
        persistent.remove();
    }}
    const transient = document.getElementById('{TRANSIENT_ROOT_ID}');
    if (transient) transient.remove();
    for (const styleId of ['{BORDER_STYLE_ID}', '{CLICK_STYLE_ID}', '{SCROLL_STYLE_ID}', '{TYPE_STYLE_ID}', '{DRAG_STYLE_ID}', '{THOUGHT_STYLE_ID}']) {{
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
    const current = document.getElementById('{THOUGHT_CARD_ID}');
    if (current) current.remove();
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

    async def claim_started(self) -> None:
        self._active = True
        self._current_status = "Analyzing"
        # Re-mount the overlay after every main-frame navigation. Without
        # this, page.goto / link clicks tear down the DOM and the persistent
        # root only reappears the next time set_status or show_thought
        # happens to fire — visible "appears and disappears" flicker.
        # _inject_persistent_root is idempotent (id guard), so spurious
        # firings are harmless.
        self._navigation_handler = lambda _frame=None: asyncio.create_task(
            self._reinject_after_navigation()
        )
        try:
            self._page.on("domcontentloaded", self._navigation_handler)
        except Exception:
            logger.debug("Failed to attach overlay navigation listener", exc_info=True)
            self._navigation_handler = None
        await self._inject_persistent_root()
        await self._ensure_transient_root()

    async def claim_ended(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._navigation_handler is not None:
            try:
                self._page.remove_listener("domcontentloaded", self._navigation_handler)
            except Exception:
                logger.debug("Failed to detach overlay navigation listener", exc_info=True)
            self._navigation_handler = None
        await self._eval(_REMOVE_ALL_JS)

    async def _reinject_after_navigation(self) -> None:
        """Re-mount the overlay after a main-frame navigation tore down the DOM."""
        if not self._active:
            return
        await self._inject_persistent_root()
        await self._ensure_transient_root()

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
    ) -> None:
        if not self._active:
            return

        await self.clear_thought()
        await self._ensure_transient_root()

        # Cursor-first choreography: move cursor to target, then trigger effect.
        # _move_cursor detects whether the cursor is at its off-screen initial
        # position (first move or after a full-page navigation destroyed the DOM)
        # and teleports instead of transitioning.
        center: dict[str, int] | None = None
        moved = False
        if action_type == "type":
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
            await self._show_click_effect(x, y, num_clicks)
        elif action_type == "scroll":
            await self._show_scroll_effect(x, y, direction)
        elif action_type == "type":
            await self._show_type_effect(center)
        elif action_type == "drag":
            await self._show_drag_effect(start_x, start_y, x, y)

    async def set_status(self, label: str) -> None:
        self._current_status = label
        if not self._active:
            return
        if label != "Analyzing":
            await self.clear_thought()
        await self._inject_persistent_root()

    async def show_thought(self, text: str) -> None:
        if not self._active:
            return
        await self._inject_persistent_root()
        clipped = self._clip_text(text, 520)
        # During "Analyzing" the card stays until clear_thought() is called
        # (by preview_action or a non-Analyzing status transition).
        # Otherwise use the fallback timeout as a safety net.
        timeout_ms = 0 if self._current_status == "Analyzing" else THOUGHT_DURATION_MS
        await self._eval(_THOUGHT_CARD_JS, {"text": clipped, "timeout_ms": timeout_ms})

    async def clear_thought(self) -> None:
        if not self._active:
            return
        await self._eval(_CLEAR_THOUGHT_JS)

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
        await self._set_chip_text(self._current_status)

    async def _set_chip_text(self, label: str) -> None:
        await self._eval(
            f"""() => {{
                const chip = document.getElementById('{STATUS_CHIP_ID}');
                if (chip) chip.textContent = {label!r};
            }}"""
        )

    async def _move_cursor(self, x: int, y: int) -> bool:
        """Move the branded cursor to the given viewport coordinates.

        Returns True if the cursor was teleported (off-screen → target),
        False if it used the CSS transition.  Teleporting happens on the
        first move of a claim and after full-page navigations that destroy
        and recreate the cursor element.
        """
        return bool(await self._safe_evaluate(
            f"""() => {{
                const cursor = document.getElementById('{CURSOR_ID}');
                if (!cursor) return true;
                const offScreen = cursor.style.left === '-200px';
                if (offScreen) {{
                    cursor.style.transition = 'none';
                    cursor.style.left = '{x}px';
                    cursor.style.top = '{y}px';
                    cursor.offsetHeight;
                    cursor.style.transition = 'left {CURSOR_TRANSITION_MS}ms ease-in-out,top {CURSOR_TRANSITION_MS}ms ease-in-out';
                    return true;
                }}
                cursor.style.left = '{x}px';
                cursor.style.top = '{y}px';
                return false;
            }}""",
            default=True,
        ))

    async def _show_click_effect(self, x: int, y: int, num_clicks: int) -> None:
        gap = int(CLICK_DURATION_MS * 0.5)
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                if (!document.getElementById('{CLICK_STYLE_ID}')) {{
                    const style = document.createElement('style');
                    style.id = '{CLICK_STYLE_ID}';
                    style.textContent = '@keyframes n1click{{0%{{width:5px;height:5px;opacity:0.6}}100%{{width:30px;height:30px;opacity:0}}}}';
                    document.head.appendChild(style);
                }}
                for (let i = 0; i < {num_clicks}; i++) {{
                    const delay = i * {gap};
                    const el = document.createElement('div');
                    el.style.cssText = 'position:fixed;left:{x}px;top:{y}px;width:5px;height:5px;background:{YUTORI_GREEN};border-radius:50%;pointer-events:none;z-index:{Z_INDEX};transform:translate(-50%,-50%);animation:n1click {CLICK_DURATION_MS}ms ease-out forwards;animation-delay:' + delay + 'ms;opacity:0;';
                    el.addEventListener('animationend', () => el.remove(), {{once: true}});
                    root.appendChild(el);
                }}
            }}"""
        )

    async def _show_scroll_effect(self, x: int, y: int, direction: str = "down") -> None:
        rotation = {"down": 0, "up": 180, "right": 270, "left": 90}.get(direction, 0)
        # Translate in the scroll direction as the chevron fades out.
        tx = {"right": 18, "left": -18}.get(direction, 0)
        ty = {"down": 18, "up": -18}.get(direction, 0)
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{SCROLL_STYLE_ID}');
                if (existing) existing.remove();

                const style = document.createElement('style');
                style.id = '{SCROLL_STYLE_ID}';
                style.textContent = '@keyframes n1scroll{{0%{{opacity:0.7;transform:translate(-50%,-50%) rotate({rotation}deg)}}100%{{opacity:0;transform:translate(calc(-50% + {tx}px),calc(-50% + {ty}px)) rotate({rotation}deg)}}}}';
                document.head.appendChild(style);

                const container = document.createElement('div');
                container.style.cssText = 'position:fixed;left:{x}px;top:{y}px;width:20px;height:20px;pointer-events:none;z-index:{Z_INDEX};animation:n1scroll {SCROLL_DURATION_MS}ms ease-out forwards;';
                const chevron = document.createElement('div');
                chevron.style.cssText = 'position:absolute;left:50%;top:50%;width:10px;height:10px;border-right:2px solid {YUTORI_GREEN};border-bottom:2px solid {YUTORI_GREEN};transform:translate(-50%,-70%) rotate(45deg);';
                container.appendChild(chevron);
                container.addEventListener('animationend', () => {{ container.remove(); style.remove(); }}, {{once: true}});
                root.appendChild(container);
            }}"""
        )

    async def _show_type_effect(self, center: dict[str, int] | None) -> None:
        if center is None:
            return

        cx = center["x"]
        cy_raw = center["y"]
        show_below = cy_raw < 50
        cy = cy_raw + 30 if show_below else cy_raw - 7

        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{TYPE_STYLE_ID}');
                if (existing) existing.remove();

                const style = document.createElement('style');
                style.id = '{TYPE_STYLE_ID}';
                style.textContent = '@keyframes n1tcaret{{0%,100%{{opacity:1}}50%{{opacity:0}}}}@keyframes n1tdot{{0%,100%{{transform:scale(1);opacity:0.5}}50%{{transform:scale(1.4);opacity:1}}}}@keyframes n1tfade{{0%{{opacity:0}}15%{{opacity:1}}85%{{opacity:1}}100%{{opacity:0}}}}';
                document.head.appendChild(style);

                const container = document.createElement('div');
                container.style.cssText = 'position:fixed;left:{cx + 14}px;top:{cy}px;pointer-events:none;z-index:{Z_INDEX};animation:n1tfade {EFFECT_DURATION_MS}ms ease-out forwards;display:flex;align-items:flex-end;gap:3px;';

                const caret = document.createElement('div');
                caret.style.cssText = 'width:2px;height:14px;background:{YUTORI_GREEN};border-radius:1px;animation:n1tcaret 0.53s step-end infinite;';
                container.appendChild(caret);

                const dots = document.createElement('div');
                dots.style.cssText = 'display:flex;gap:2px;margin-left:2px;margin-bottom:1px;';
                for (let i = 0; i < 3; i++) {{
                    const dot = document.createElement('div');
                    dot.style.cssText = 'width:3px;height:3px;background:{YUTORI_GREEN};border-radius:50%;animation:n1tdot 0.6s ease-in-out infinite;animation-delay:' + (i * 0.12) + 's;';
                    dots.appendChild(dot);
                }}
                container.appendChild(dots);

                // Caret/dots run on infinite animations (never fire animationend),
                // so this listener triggers exactly once when n1tfade completes.
                container.addEventListener('animationend', () => {{ container.remove(); style.remove(); }}, {{once: true}});
                root.appendChild(container);
            }}"""
        )

    async def _show_drag_effect(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        await self._eval(
            f"""() => {{
                const root = document.getElementById('{TRANSIENT_ROOT_ID}');
                if (!root) return;
                const existing = document.getElementById('{DRAG_STYLE_ID}');
                if (existing) existing.remove();

                const style = document.createElement('style');
                style.id = '{DRAG_STYLE_ID}';
                style.textContent = '@keyframes n1dfade{{0%{{opacity:0.5}}100%{{opacity:0}}}}@keyframes n1dtrail{{0%{{opacity:0.6}}100%{{opacity:0}}}}';
                document.head.appendChild(style);

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
        try:
            if arg is None:
                return await self._page.evaluate(script)
            return await self._page.evaluate(script, arg)
        except Exception:
            logger.debug("Overlay evaluate failed (best-effort)", exc_info=True)
            return default

    async def _eval(self, script: str, arg: object | None = None) -> None:
        await self._safe_evaluate(script, arg)

    @staticmethod
    def _clip_text(text: str, limit: int) -> str:
        return clip_text(str(text), limit, ellipsis="…")
