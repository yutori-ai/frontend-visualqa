# Example claims

Claims tested against the example pages with `frontend-visualqa verify`.
Each claim was run 3 times. Results reflect n1 model behavior as of April 2026.

Serve the examples:

```bash
python3 -m http.server 8000 -d examples
```

## Original examples

These ship with the README and are reliable across repeated runs.

### ecommerce_store.html

```bash
# Self-correcting navigation — n1 starts on the catalog page, navigates to the product detail
frontend-visualqa verify http://localhost:8000/ecommerce_store.html \
  --claims 'The product detail page shows Wireless Headphones Pro priced at $149.99'
# → passed (3/3)

# Cart pricing bug — subtotal uses original prices instead of sale prices
frontend-visualqa verify 'http://localhost:8000/ecommerce_store.html#/cart' \
  --claims 'The displayed cart subtotal equals the sum of the visible sale prices'
# → failed: $279.98 displayed vs $229.98 expected (3/3)

# Navigation hint — add item then check badge
frontend-visualqa verify http://localhost:8000/ecommerce_store.html \
  --claims 'The cart badge shows 3 items' \
  --navigation-hint "Click 'Add to Cart' on the Mechanical Keyboard K7 product card."
# → passed (3/3)
```

### analytics_dashboard.html

```bash
# Mix of passing and failing claims
frontend-visualqa verify http://localhost:8000/analytics_dashboard.html \
  --claims \
  'The API status indicator shows Active' \
  'The monthly quota progress bar is completely filled'
# → first passed, second failed: label says 100% but bar is ~65% full (3/3)

# Scrolling to find off-screen content
frontend-visualqa verify http://localhost:8000/analytics_dashboard.html \
  --claims 'The /api/v1/webhooks endpoint returned a 200 OK status'
# → failed: endpoint shows 500 Error (3/3)
```

### booking_form.html

```bash
# Autonomous form filling — n1 fills the form and catches a timezone bug
frontend-visualqa verify 'http://localhost:8000/booking_form.html' \
  --max-steps-per-claim 25 \
  --claims 'The date on the confirmation page matches the date selected on the calendar' \
  --navigation-hint "Fill out the form with example data (grayed text is showing example format, not filled out values)"
# → failed: off-by-one date on confirmation page (3/3)
```

### yutori_login.html

```bash
# Login flow with claims file
frontend-visualqa verify http://localhost:8000/yutori_login.html \
  --no-reset-between-claims \
  --max-steps-per-claim 20 \
  --claims-file examples/login_flow_claims.md
# → first two passed, third failed: label says "100% used" but bar is ~40% (3/3)

# Form validation — trigger and verify error message
frontend-visualqa verify http://localhost:8000/yutori_login.html \
  --claims 'The email field shows "Please enter a valid email address" after submitting the empty form' \
  --navigation-hint 'The grayed text in the fields is placeholder, not real input. Click the Continue button immediately without typing anything.'
# → passed (3/3)
```

## Visual contradiction examples

These pages contain intentional bugs where text labels contradict the visual rendering.
They test whether n1 can detect discrepancies between what the text says and what the pixels show.

### device_dashboard.html — gauge fill, toggle state, camera feed

```bash
frontend-visualqa verify http://localhost:8000/device_dashboard.html \
  --claims \
  'The backup battery gauge fill matches the displayed 72% charge' \
  'The front door lock toggle position matches the Locked status' \
  'The garage camera shows a live feed matching its Online status'
# → all three failed (3/3): gauge is ~35%, toggle is OFF, camera shows "No Signal"
```

### pricing_plans.html — tab selection, disabled button, star rating

```bash
frontend-visualqa verify http://localhost:8000/pricing_plans.html \
  --claims \
  'The Annual billing tab is visually selected' \
  'The Enterprise plan Contact Sales button appears active and clickable'
# → both failed (3/3): Monthly tab has active styling, Contact Sales is grayed out
```

### team_settings.html — toggle state, theme selection, text clipping

```bash
frontend-visualqa verify http://localhost:8000/team_settings.html \
  --claims \
  'The Automatic backups toggle visually matches its Enabled label' \
  'The SSO migration warning banner is fully visible' \
  'The Audit log card shows 1,284 events this week'
# → first two failed, third passed (3/3): toggle is OFF, banner text is clipped
```

### campaign_editor.html — chip selection, disabled button, clipped preview

```bash
frontend-visualqa verify http://localhost:8000/campaign_editor.html \
  --claims \
  'The Power Users audience chip is visually selected' \
  'The Send test email button appears active and clickable' \
  'The mobile email preview is fully visible inside its frame' \
  'The subject line shows Launch Week starts now'
# → first three failed, fourth passed (3/3)
```

### calendar_scheduler.html — date selection, time slot, toggle state

```bash
frontend-visualqa verify http://localhost:8000/calendar_scheduler.html \
  --claims \
  'April 18 is visually selected on the calendar' \
  'The 2:30 PM time slot is visually selected' \
  'The Join by video toggle visually matches its Enabled label' \
  'The scheduler shows Pacific Time (PT) as the timezone'
# → all four correct (3/3): first three failed (bugs detected), fourth passed
```

## Known limitations

These claims exercise visual discrimination tasks where n1 is unreliable.

### Status dot color discrimination (service_status.html)

n1 cannot reliably distinguish small colored status dots (12px) from their text labels.
When the text says "Healthy" and the dot is red, n1 anchors on the text.

```bash
frontend-visualqa verify http://localhost:8000/service_status.html \
  --claims \
  'The API Gateway service shows a healthy status' \
  'The Authentication service indicator matches its degraded status label' \
  'The system health gauge fill matches the 98.7% label'
# → n1 incorrectly passes the first two (0/3 correct). Gauge fill is also missed (0/3).
```

### Badge clipping (notification_center.html)

n1 cannot detect that a notification badge's text is clipped by its container
(e.g., "24" rendered as "2" because the "4" is cut off).

```bash
frontend-visualqa verify http://localhost:8000/notification_center.html \
  --claims 'The notification badge displays the full count without clipping'
# → n1 incorrectly passes (0/3 correct)
```

### Star rating counting (pricing_plans.html)

n1 has difficulty counting filled vs empty stars in a rating display.

```bash
frontend-visualqa verify http://localhost:8000/pricing_plans.html \
  --claims 'The Pro plan star rating visually matches the 4.8 out of 5 text'
# → n1 incorrectly passes most of the time (1/3 correct)
```
