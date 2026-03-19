# Claim Writing

Claims should describe one visible fact each.

Good claims:

- `The modal title reads "Invite teammate"`
- `The Save button is visible without scrolling`
- `The selected tab has a blue underline`
- `At 375px width, the sidebar is replaced by a menu button`
- `After form submit, a success toast appears in the top right corner`

Weak claims:

- `The modal works`
- `The page looks good`
- `The layout is cleaner now`
- `The interaction feels smooth`

## Rules

1. Write claims that can be proven from the final screenshot.
2. Keep each claim to a single user-visible fact.
3. Split compound assertions into separate claims.
4. Use exact visible copy for headings, labels, and button text when text matters.
5. Always set an explicit viewport when responsive behavior matters.

## Navigation Hints

If the page needs interaction before the claim can be judged, keep the claim short and put the setup steps in `navigation_hint`.

Good:

- Claim: `The delete confirmation dialog is visible`
- Navigation hint: `Open the first row action menu and click Delete before judging the claim.`

Bad:

- Claim: `Open the first row action menu, click Delete, and confirm the delete dialog is visible`

## Claim Batching

Prefer 1-5 related claims per run.

Good batch:

- `The modal title reads "Edit Task"`
- `The Save button is visible without scrolling`
- `The Cancel button is left of Save`

Bad batch:

- 20 unrelated claims across multiple pages and viewports

Small batches make failures easier to diagnose and keep the agent from conflating unrelated states.
