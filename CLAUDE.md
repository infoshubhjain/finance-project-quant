# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to communicate with Shubh (project owner)

- Start every response with the exact phrase: "okay shubh, ill do that"
- Shubh is learning to code. While working, explain what is going on in simple,
  plain terms — what each file is for, what a concept means, and why a decision
  was made — like teaching a beginner, not briefing an expert. Short
  "what just happened and why" notes beat jargon. Define technical terms the
  first time they appear.

## The cardinal rule (never violate)

Decision-bearing numbers (`direction`, `confidence`, `invalidation_level`, source
weights) come only from deterministic, tested pure Python. The LLM lives only in
`narrative/`, is optional and key-gated, and may write only the `thesis` prose —
never a number. No network calls or randomness in `analyzers/` or `synthesis/`.
The default path stays keyless. Never weaken the research-only disclaimer. If a
request would break this, flag it and propose the correct layer instead.

## Commands

```bash
# System Python is externally managed (Homebrew) — always use the venv
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# A change is "done" only when all three pass:
pytest -q                                  # network-free suite
ruff check . && ruff format --check .      # CI gates lint AND format
python -m alpha_engine.cli.main scan BTC   # manual end-to-end check

pytest tests/test_core.py::test_name -q    # single test
./start.sh <cmd>                           # zero-setup wrapper (venv + install + run)
```

## Everything else

Read [AGENTS.md](AGENTS.md) — it holds the full command list, architecture,
extension patterns, and gotchas. For deeper background, [context.md](context.md)
has the layer table; [FUTURE_WORK.md](FUTURE_WORK.md) holds the roadmap;
[README.md](README.md) has the full capability matrix.
