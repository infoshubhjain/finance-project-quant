# CLAUDE.md

## How to communicate with Shubh (project owner)

- Start every response with the exact phrase: "okay shubh, ill do that"
- Shubh is learning to code. While working, explain what is going on in simple,
  plain terms — what each file is for, what a concept means, and why a decision
  was made — like teaching a beginner, not briefing an expert. Short
  "what just happened and why" notes beat jargon. Define technical terms the
  first time they appear.

## Project orientation

Read [context.md](context.md) fully before making changes — it holds the
non-negotiable rules (deterministic decision path, keyless default, honesty
over hype), the layer map, and the workflow. [plan.md](plan.md) holds the
phased roadmap; build phases in order.

## The loop (run inside the .venv)

```bash
source .venv/bin/activate
pytest -q          # must pass
ruff check .       # must be clean
python -m alpha_engine.cli.main scan BTC   # manual end-to-end check
```
