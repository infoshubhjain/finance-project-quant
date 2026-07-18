"""Execution layer: turn signals into (paper or live) broker orders.

This is the ONE part of the engine that can touch money. Everything upstream is
research; this is where a research view optionally becomes a trade. Because of
that it carries the project's strictest safety rules:

- Paper-first: orders are simulated and logged unless LIVE_TRADING=1 is set.
- Owner-only: live trading acts on the owner's own broker account, never anyone
  else's. This does not make the project advisory — it executes the owner's own
  decisions, it does not sell them to others.
- Deterministic upstream: the SIGNAL is still deterministic. Execution adds no
  new decision-bearing number; it only maps an existing signal to an order.
"""
