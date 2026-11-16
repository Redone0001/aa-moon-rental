# Changelog

## Unreleased

- Show current structure types and names from optional aa-structures data in the moon catalogue, details, selectors and rental assignments, matching moon IDs and respecting Structures visibility permissions.
- Copy fixed 30-day uncompressed ore quantities from auction cards, with a gross value estimate using snapshotted Moon Mining volume and pricing.
- Add a separate day-first rent start date above the bidding deadline; award scheduled rentals at close and start the lease term and billing on the advertised start. Preserve legacy auction starts on upgrade.

- Restrict the moon catalogue, individual catalogue details and CSV export to rental managers and admins; members retain auction bidding, personal rentals and invoices.
- Export the moon catalogue to CSV, including all search results, survey composition, exact estimated values and rental/auction status.
- Add combined region, constellation, rarity and survey-ore filters and moon/system search to auction creation, direct assignment and bidding.
- Paginate moon choices, retain selections and terms across filters/pages, and support bulk page selection.
- Index survey ore membership and backfill existing snapshots and locally known constellations.
- Show compact ISK input previews and highest/winning bids while preserving exact submitted amounts.

## 0.1.0

Initial implementation: moonmining survey sync, competitive auctions, automatic lease awards, direct assignments, monthly/single-payment invoices, SSO wallet integration, partial-payment matching, audit events, theme-aware UI, retention and protected contribution workflows.
