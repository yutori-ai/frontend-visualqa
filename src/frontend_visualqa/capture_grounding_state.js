() => {
    const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
    const isVisible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
            return false;
        }
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
            return false;
        }
        if (rect.bottom <= 0 || rect.right <= 0) {
            return false;
        }
        if (rect.top >= window.innerHeight || rect.left >= window.innerWidth) {
            return false;
        }
        return true;
    };
    const elementText = (element) => {
        if (!element) return "";
        const explicitText = normalize(element.innerText || element.textContent || "");
        if (explicitText) return explicitText;
        const ariaLabel = normalize(element.getAttribute("aria-label"));
        if (ariaLabel) return ariaLabel;
        const value = normalize(element.value);
        return value;
    };
    const visibleProgressBars = Array.from(
        document.querySelectorAll(
            "[role='progressbar'], progress, meter, [aria-valuenow], .progress-track, .progress-bar, [class*='progress-track'], [class*='progress-bar']"
        )
    )
        .filter(isVisible)
        .map((element) => {
            const rect = element.getBoundingClientRect();
            if (rect.width < 24 || rect.height < 4) {
                return null;
            }

            let fillRatio = null;
            const ariaNow = element.getAttribute("aria-valuenow");
            const ariaMin = element.getAttribute("aria-valuemin");
            const ariaMax = element.getAttribute("aria-valuemax");
            const min = ariaMin === null ? 0 : Number(ariaMin);
            const max = ariaMax === null ? 100 : Number(ariaMax);
            const now = ariaNow === null ? null : Number(ariaNow);
            if (now !== null && Number.isFinite(now) && Number.isFinite(min) && Number.isFinite(max) && max > min) {
                fillRatio = Math.max(0, Math.min(1, (now - min) / (max - min)));
            } else if (element instanceof HTMLProgressElement && Number.isFinite(element.max) && element.max > 0) {
                fillRatio = Math.max(0, Math.min(1, element.value / element.max));
            } else if (element instanceof HTMLMeterElement && Number.isFinite(element.max) && element.max > element.min) {
                fillRatio = Math.max(0, Math.min(1, (element.value - element.min) / (element.max - element.min)));
            } else {
                let maxChildWidth = 0;
                for (const child of Array.from(element.children)) {
                    if (!isVisible(child)) continue;
                    const childRect = child.getBoundingClientRect();
                    if (childRect.height < rect.height * 0.5) continue;
                    maxChildWidth = Math.max(maxChildWidth, Math.min(childRect.width, rect.width));
                }
                if (maxChildWidth > 0) {
                    fillRatio = Math.max(0, Math.min(1, maxChildWidth / rect.width));
                }
            }

            if (fillRatio === null) {
                return null;
            }

            const labels = [];
            const region = element.closest(
                "section, article, aside, form, [role='region'], [role='group'], .glass-card, .card, .panel"
            );
            const labelSelectors = "h1, h2, h3, h4, legend, label, .card-title, .panel-title, .section-title, .title";
            if (region) {
                for (const candidate of Array.from(region.querySelectorAll(labelSelectors))) {
                    if (!isVisible(candidate)) continue;
                    const text = elementText(candidate);
                    if (text) labels.push(text);
                }
            }
            for (let sibling = element.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
                if (!isVisible(sibling)) continue;
                const text = elementText(sibling);
                if (text) labels.push(text);
            }

            const label = labels.find(Boolean) || "";
            return { label, fillRatio };
        })
        .filter(Boolean);
    const visibleHeadings = Array.from(document.querySelectorAll("h1, h2, h3, h4, [role='heading']"))
        .filter(isVisible)
        .map(elementText)
        .filter(Boolean);
    const visibleButtons = Array.from(
        document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")
    )
        .filter(isVisible)
        .map(elementText)
        .filter(Boolean);
    const buttonStates = Array.from(
        document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")
    )
        .filter(isVisible)
        .map((element) => {
            const text = elementText(element);
            if (!text) return null;
            const rect = element.getBoundingClientRect();
            let fullyVisible =
                rect.top >= 0 &&
                rect.left >= 0 &&
                rect.bottom <= window.innerHeight &&
                rect.right <= window.innerWidth;
            for (let ancestor = element.parentElement; ancestor && fullyVisible; ancestor = ancestor.parentElement) {
                const style = window.getComputedStyle(ancestor);
                const clips =
                    ["hidden", "clip", "scroll", "auto"].includes(style.overflow) ||
                    ["hidden", "clip", "scroll", "auto"].includes(style.overflowX) ||
                    ["hidden", "clip", "scroll", "auto"].includes(style.overflowY);
                if (!clips) continue;
                const ancestorRect = ancestor.getBoundingClientRect();
                if (
                    rect.top < ancestorRect.top ||
                    rect.left < ancestorRect.left ||
                    rect.bottom > ancestorRect.bottom ||
                    rect.right > ancestorRect.right
                ) {
                    fullyVisible = false;
                }
            }
            return { text, fullyVisible };
        })
        .filter(Boolean);
    const dialogTitles = Array.from(document.querySelectorAll("[role='dialog'], dialog, [aria-modal='true']"))
        .filter(isVisible)
        .flatMap((dialog) => {
            const titles = [];
            const labelledBy = dialog.getAttribute("aria-labelledby");
            if (labelledBy) {
                const labelElement = document.getElementById(labelledBy);
                if (isVisible(labelElement)) {
                    const text = elementText(labelElement);
                    if (text) titles.push(text);
                }
            }
            for (const heading of dialog.querySelectorAll("h1, h2, h3, h4, [role='heading']")) {
                if (!isVisible(heading)) continue;
                const text = elementText(heading);
                if (text) titles.push(text);
            }
            return titles;
        })
        .filter(Boolean);
    return { visibleHeadings, visibleButtons, buttonStates, dialogTitles, progressBars: visibleProgressBars };
}
