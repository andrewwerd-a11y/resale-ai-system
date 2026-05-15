# Resale AI System Roadmap

This roadmap keeps the near-term product direction practical: stabilize a reliable eBay income engine first, then expand into richer workbench, provider, marketplace, product, and delight layers. Provider output remains draft/proposal-only unless a human explicitly approves a separate mutation path.

## 1. Immediate Income Stabilization

- Single-SKU publish readiness that clearly explains blockers before any publish attempt.
- Stale offer recovery with read-only diagnostics, approval packets, and a rollback/runbook path.
- Sold sync and reporting so listed inventory, sold items, and financial state do not drift.
- Publish cockpit focused on operator review, safety flags, and small manually approved batches.
- Small batch publish dry-runs before any live action.
- Rollback/runbook documentation for stale live state, condition mismatch, and sync issues.

## 2. Bulk Reintake + Evidence Workflow

- Bulk reintake preview for current SKUs without provider calls or item overwrites by default.
- Photo evidence summaries that show missing photo types and deep-analysis image metadata when available.
- Missing-photo reports that bias toward holds and operator context instead of overconfident listings.
- Correction-report-v2 as the operator-facing read-only evidence package.
- Operator next-action lanes for listed/sync review, live remediation, image hosting, publish prep, and manual review.

## 3. Intake Workbench

- Visual item page with photo grid, item state, and evidence-oriented confidence display.
- User context field plus context chips for missing details, provenance, defects, measurements, and authenticity.
- Tab-to-accept suggestions and manual edits that remain separate from provider output.
- Reanalysis preview for proposed edits before canonical record changes.
- Price impact preview for category, condition, and evidence changes.
- Clear confidence/evidence display so low evidence creates holds rather than guesses.

## 4. AI Provider Router

- Local/Ollama triage for cheap first-pass classification and low-risk cleanup.
- Claude enrichment for higher-value evidence synthesis when explicitly enabled.
- OpenAI/Gemini later, after the income engine and workbench are stable.
- Cost controls, result cache, retry policy, and provider-call audit trail.
- Provider disagreement detection and evidence comparison.
- No provider output auto-overwrites canonical item records.

## 5. Marketplace Compatibility Engine

- eBay required-field checks tied to category templates and readiness gates.
- eBay category/aspect checks that explain missing or invalid specifics.
- eBay condition policy checks for local/live condition compatibility.
- Cross-platform field map for marketplace-specific drafts.
- Platform-specific blockers and draft states that do not mutate canonical records automatically.

## 6. Sold Sync + Profit Layer

- Sold sync from marketplace state into local records.
- SaleRecord creation with listing, sold, fee, shipping, ad spend, and refund context.
- Net profit and margin calculations by SKU, batch, category, and sourcing channel.
- Stale listing repricing recommendations after the listing/sold state is trustworthy.
- Later integration with the financial automation project for bookkeeping and cash-flow reporting.

## 7. Public Product Readiness

- Users/auth, subscriptions, AI credits, and tenant isolation.
- Secure storage for secrets, photos, reports, exports, and provider artifacts.
- Privacy, export, and delete flows.
- Demo mode and onboarding paths that do not expose real seller data.
- Security tests for auth boundaries, route guards, upload handling, and tenant data isolation.

## 8. Gamified/Delight Layer

- Optional only, after the business workflow is trustworthy.
- Sale celebrations and earnings dashboard.
- Listing streaks, intake themes, bonus credits/promos, and achievement badges.
- Delight features must never corrupt accounting, approval state, listing state, or business logic.
