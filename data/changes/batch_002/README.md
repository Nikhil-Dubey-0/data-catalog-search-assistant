# Change batch 002

Steward metadata delivered *after* the upstream schema change
(`python -m catalog_assistant simulate-change`). Layered on top of `data/metadata`:

- fixes the invalid `access_role` of `analytics.daily_sales_summary` (was `superuser`, quarantined)
- documents the new table `marketing.email_events` but **not** its `recipient_email`
  column, so the fail-closed PII name rule must classify it automatically
- documents the new column `sales.customers.loyalty_tier`
- updates the description of `finance.revenue_monthly`
