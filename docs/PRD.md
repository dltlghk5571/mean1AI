# Product requirements — MVP

## Problem

Municipal complaints arrive as unstructured Korean text through multiple channels. Staff spend time
finding the issue type, routing owner, missing information, related guidance, and a consistent first
response. Wrong routing causes transfers and delay.

## MVP user stories

- A citizen can submit a complaint without knowing the responsible department.
- A triage officer sees redacted text, urgency, category, candidate work groups, confidence, missing
  information, evidence sources, and a draft response.
- A reviewer can approve or change the routing and draft.
- An auditor can reconstruct which provider, rule, source, and human action produced the outcome.

## Explicit non-goals

- No automatic rejection, closure, legal disposition, benefit eligibility, fine, permit, or compensation.
- No live integration with Seongnam City, 국민신문고, SMS, maps, emergency services, or identity systems.
- No claim that demo organization data is official or current.
- No attachment OCR or image analysis in this milestone.

## Success criteria for the demo

- The service starts with one command and works without an API key.
- All direct identifiers in the supplied redaction fixtures are masked before provider invocation.
- Urgent examples never enter an auto-routed normal queue.
- Sensitive categories always require human review.
- Every submitted complaint has at least one audit event.
