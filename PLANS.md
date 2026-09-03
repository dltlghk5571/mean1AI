# Execution plan template for Codex

Use this file for changes expected to span multiple modules or more than one review cycle.

## Goal

State the user-visible outcome in one paragraph.

## Safety and scope

List affected safety invariants from `AGENTS.md`, explicit non-goals, and external systems that must not
be mutated.

## Current behavior

Record the relevant files, data flow, tests, and observed limitations.

## Implementation slices

For each slice, name the files, behavior, tests, and rollback point. Keep slices independently
reviewable where possible.

## Verification

Include exact commands, expected outputs, evaluation metrics, and manual checks.

## Decisions and surprises

Append architectural decisions, failed approaches, and newly discovered risks while executing.
