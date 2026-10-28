# Product

## What it is
Document Intelligence Engine: extracts validated fields from financial documents (W-2s, pay stubs, bank statements, invoices) and routes low-confidence fields to a human reviewer. Corrections feed retraining.

## Platform
web

## Users and scene (inferred; owner asked for no interview)
- **Reviewer / operations analyst** at a desk, in long sessions, clearing a queue. Works mostly from the keyboard, with eyes moving between the page image and the field list.
- **Hiring manager / portfolio visitor**, who runs the demo once to see the human-in-the-loop flow work end to end.

## Surfaces
- **Review console** (Operate): queue, document page with source boxes, editable fields with calibrated confidence, keyboard shortcuts, reviewer stats.

## Constraints
- Synthetic documents only in the public demo; never real personal data (PRD F12).
- No build step: a single static HTML file served by FastAPI.
- Success is measured by review rate, seconds per document and correction accuracy, not by visual flourish.
