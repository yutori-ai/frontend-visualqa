# QA Protocol

## Preflight

Before running claim verification, confirm:

- the local URL is reachable
- the viewport matches the scenario you care about
- the page does not require an unavailable login
- the claims are concrete and short

If any of that is unclear, call `take_screenshot` first.

## Recommended Loop

1. Use the app's normal tooling, the browser's current session, or a `navigation_hint` to reach the relevant state.
2. Capture a screenshot baseline if route or state is uncertain.
3. Run `verify_visual_claims` on a small batch of related claims.
4. Read the structured result for each claim.
5. Inspect the saved screenshots when a claim fails or is inconclusive.
6. Fix the frontend and rerun the same claims.

If only some claims in a batch need setup, split them into separate warm-session `verify_visual_claims` calls or move to a CLI claims file with per-claim metadata. Do not expect one `navigation_hint` to target only part of a batch.

## Proof Artifacts

`verify_visual_claims` returns proof for every claim:

- `results[].proof.screenshot_path` — the screenshot that decided the verdict. Read this file to see what the page actually rendered.
- `results[].proof.text` — a textual description of the evidence.
- `results[].trace.screenshot_paths` — all screenshots captured during the claim's evaluation, including intermediate steps.
- `artifacts_dir` — the directory containing all screenshots and trace files for the run.

`take_screenshot` returns a single `screenshot_path`.

When reporting results, read the proof screenshot for any failed or inconclusive claim and present it to the user. If the client supports inline images, display it; otherwise print the absolute path.

## Status Meanings

- `passed`: visual evidence matched the claim
- `failed`: the page rendered something different from the claim
- `inconclusive`: the runner explored but could not determine the claim confidently
- `not_testable`: the environment blocked verification, usually because the page was unreachable, crashed, or required unavailable auth

## When To Reset Browser State

Use `manage_browser` if:

- the runner is clearly on the wrong page
- stale cookies or local storage are affecting the result
- a previous run left the browser in an unexpected state
- the viewport needs to be changed without restarting the whole client session
- the page is auth-gated and the user needs to log in interactively (use `action="login"` with the login URL)

For auth-gated flows, bootstrap auth with `manage_browser(action="login", url=...)` before running claims, or use persistent mode to reuse an existing session.

## Scope Boundaries

This skill is for targeted, claim-based visual QA during development. It is not meant to replace:

- end-to-end functional coverage
- CI-wide regression suites
- open-ended design critique without explicit claims
