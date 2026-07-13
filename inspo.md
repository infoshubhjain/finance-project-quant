# inspo.md — Prior Art

Who else is doing this, what to steal from each, and what killed the ones that
died. Split by which half of [FUTURE_WORK.md](FUTURE_WORK.md) each maps to.

Read this before building a phase, not after. Most of what you need has been
built by someone already; the interesting question is always *what they got
wrong*.

---

## PART A — The engine and AlphaX

### Alphalens — the spec for Phase 7

<https://github.com/quantopian/alphalens>

**What it is.** Quantopian's factor-analysis library. Takes a factor's values plus
forward returns and produces IC, rank IC, IC decay by horizon, quantile-spread
returns, and turnover.

**Why it matters to us.** This is *exactly* what Phase 7 (factor ranking) needs to
produce. Read `performance.py` before writing a line of `quant/ranking.py`.

**Steal:** the metric vocabulary and the report layout. Every quant in the world
already knows how to read an Alphalens tearsheet, so producing one means our
output needs no explanation.

**Don't steal:** the dependency. It's unmaintained (Quantopian is dead), and it's
built around pandas MultiIndex conventions and Zipline. Our engine's virtue is
being dependency-light and deterministic — importing a dead pandas-heavy library
would cost us that for metrics we can compute in 40 lines.

---

### AlphaPurify — the modern redo

<https://github.com/eliasswu/Alphapurify>

**What it is.** Polars-based factor construction, preprocessing, backtesting, and
attribution. `FactorAnalyzer` does IC / rank-IC testing and quantile backtests;
`AlphaPurifier` has 40+ preprocessing methods.

**Why it matters.** The **preprocessing catalogue** is the part we currently skip
entirely. Our 53 features are raw. Real factor research winsorizes (clips
outliers), standardizes, and neutralizes (strips out the part of a factor that's
just market beta or sector exposure) *before* measuring IC. Skipping this is a
big reason raw factor ICs come out as noise.

**Steal:** the preprocessing method list. Implement the three that matter
(winsorize, z-score, neutralize-vs-benchmark) as deterministic pure functions in
Phase 7 or 10.

---

### Microsoft Qlib — what this project grows into

<https://github.com/microsoft/qlib>

**What it is.** A full AI-oriented quant platform: data → factor → model →
backtest → portfolio → execution. Covers alpha seeking, risk modeling, portfolio
optimization, order execution.

**The one idea to steal, and it changes Phase 10 completely:** Qlib has a **factor
expression engine**. You don't write a Python function per factor — you write
`Ref($close, 5) / $close - 1` and it evaluates it across a whole universe.

This is how you get to 500+ factors without writing 500 functions. You define the
*operators* (rolling mean, rank, delta, correlation, std, ts_max…) once, and
factors become compositions of them. Phase 10's target count goes from
"exhausting" to "a config file."

Read `qlib/data/ops.py` specifically.

**Don't steal:** Qlib itself. It's heavy, opinionated, and assumes its own data
format. Adopting it would mean giving up the keyless zero-setup property that is
the whole reason this repo is credible.

---

### RD-Agent — the honest way to put an LLM in quant research

<https://github.com/microsoft/RD-Agent>

**What it is.** An LLM-based multi-agent framework that autonomously proposes
factors and models, tests them, and iterates. Reported: ~2× ARR vs. benchmark
factor libraries with 70% fewer factors, for under $10 of compute.

**Why it matters to Phase 13.** This is the architecturally *correct* use of an
LLM in a quant system, and it is the same rule this repo already lives by, applied
one level up:

> The LLM **proposes hypotheses**. Deterministic code **tests them**. The measured
> results **decide**. The model never sets a number.

That's the cardinal rule at the research level rather than the signal level. If
we ever put an LLM near factor generation, this is the shape it takes — not
"ask the model what the confidence should be."

**Read skeptically.** The performance claims are from the authors. The *design* is
what's valuable, not the benchmark.

---

### WorldQuant BRAIN — this is literally AlphaX, productized

<https://www.worldquant.com/brain/>

**What it is.** Not open source. A web platform where users compose alphas from a
fixed vocabulary of operators and data fields, and the platform simulates and
scores them on IC, Sharpe, turnover, and "fitness."

**Why it matters.** It is Part-2 of the original plan, already built and running at
scale. **Go make a free account and submit one alpha.** Two hours there teaches
more about what AlphaX should feel like than a week of design.

**Steal the discipline, not the code.** BRAIN forces every submitted alpha through
**hard quality gates** before it counts: maximum turnover, minimum Sharpe, and —
crucially — **low correlation to alphas that already exist.** That last one is the
insight. A factor that's 0.95-correlated with one you already have adds nothing,
no matter how good its IC. Our Phase 7 `factor_correlation()` and the `low_signal`
flag are weak versions of this; BRAIN's gates are the strong version.

**It's also our competitor** — see Part B.

---

### Also worth a look

| Project | Why |
| ------- | --- |
| [Spectre](https://github.com/heartquake/spectre) | GPU-accelerated factor analysis + backtester. Relevant only if Phase 10 gets slow. |
| [AlphaGen](https://github.com/RL-MLDM/alphagen) | RL that generates formulaic alphas. A more aggressive Phase 13. |
| [QuantaAlpha](https://github.com/QuantaAlpha/QuantaAlpha) | LLM + evolutionary factor mining. Same space as RD-Agent. |
| [ML for Trading](https://github.com/stefan-jansen/machine-learning-for-trading) | **Chapter 4 is the best single tutorial for Phase 7.** Start here if any of the IC concepts feel shaky. |
| [awesome-quant](https://github.com/wilsonfreitas/awesome-quant) | The index. Skim when you need a specific piece; don't read end to end. |

---

## PART B — QuantHQ

### QuantConnect — proof the thesis works, and the gap we exploit

<https://www.quantconnect.com/>

**What it is.** The largest open algo-trading platform: 300k+ users, ~15,000
backtests run **on their servers** daily, public community strategies, live
deployment.

**What this proves.** Server-side verified backtesting at scale is a real,
sustainable business. That de-risks the core QuantHQ bet.

**The gap — and it's the whole opening.** *Nobody gets hired off a QuantConnect
profile.* They built the runtime and stopped there. There is no identity layer, no
reputation score, no recruiter product. The backtests are verified but they don't
*mean* anything to a third party.

QuantHQ's bet is precisely that missing half: take the verified-backtest primitive
QuantConnect already proved works, and turn it into an **identity and hiring
layer.**

---

### Numerai — the purest "verified on hidden data"

<https://numerai.fund/>

**What it is.** A hedge fund where users never see the real data. They get
obfuscated, encrypted features, submit predictions, and stake NMR (their token) on
those predictions. The fund trades the aggregate. Good predictions earn; bad ones
burn the stake.

**The idea to steal: staking.** It elegantly solves a problem I flagged in
FUTURE_WORK Phase A — the user who quietly ran 50 strategies and publishes only
the best one. My proposed fix was "count the attempts." Numerai's is better:
**make them put something at risk.** A prediction someone has staked on is a
categorically different claim from one they haven't.

**The second idea: the leaderboard is credible because the data is hidden.** You
cannot overfit what you cannot see. Any QuantHQ competition or score that runs on
data participants can access is ranking overfitting skill, and everyone
sophisticated will know it.

---

### WorldQuant BRAIN (again) — as the hiring funnel

<https://www.worldquant.com/brain/> · [International Quant Championship](https://www.worldquant.com/brain/iqc/)

**What it really is.** BRAIN is WorldQuant's **recruiting pipeline** wearing a
research platform's clothes. Consultants earn real money (Grandmaster tier:
$8,000+/quarter). The IQC runs university teams through a multi-stage competition,
and WorldQuant hires out of it.

**Why this matters enormously.** It is your "Quant University / apprenticeship"
idea (idea #2 in the business doc), already operating at global scale, and proving
the model converts.

**And it's the thing you beat.** WorldQuant is a *fund*. Every quant it discovers,
it keeps. It will never be a neutral referee, because it's a competitor to every
other firm that might want to hire from it.

**That is QuantHQ's entire strategic position:** be the neutral evaluator that
*every* firm can hire from. WorldQuant structurally cannot be that. Neither can
Citadel, Jane Street, or any other fund that tries. The referee cannot also be a
player — and right now the only referees are players.

---

### Quantiacs

<https://quantiacs.com/>

Competitions + backtesting + capital allocation to winners. Smaller than
QuantConnect. Worth studying the **competition mechanics** specifically —
submission format, evaluation windows, payout structure.

---

### Kaggle — steal the tiers

<https://www.kaggle.com/>

Obviously relevant, but steal one specific thing: **the tier system** (Novice →
Contributor → Expert → Master → Grandmaster).

That's QuantScore, and it works for two reasons worth copying exactly:

1. **It's earned on hidden test sets.** Same lesson as Numerai.
2. **It's categorical, not a number.** "Master" is far harder to game, argue with,
   or feel cheated by than "QuantScore: 847/1000." A number invites litigation
   over every point; a tier invites you to go earn the next one.

---

## The cautionary tale — read this before writing another line of business plan

### Quantopian is dead

<https://www.quantopian.com/> (shut down 2020)

It had **everything** QuantHQ wants: a large, genuinely beloved community; a
server-side backtester; competitions; and a great open-source stack (Zipline,
Alphalens, Pyfolio — half of Part A above is *their code*).

**It died anyway.** The business model was: crowdsource strategies from the
community, run a hedge fund on them, monetize the returns. The fund
underperformed. The community was real; the business wasn't.

### The lesson, stated as sharply as possible

**Do not monetize the alpha. Monetize the trust.**

- **Quantopian** monetized the alpha → the alpha didn't work → dead.
- **Numerai** made users stake on their own alpha → skin in the game → alive.
- **QuantConnect** sold infrastructure, not returns → alive.
- **WorldQuant** monetizes the *talent it finds*, not the strategies → very alive.

Recruiters paying for verified talent is a **far better business than running a
fund on community strategies**, for one structural reason: *it does not require
the strategies to make money.* It only requires the evaluation to be credible. The
revenue is decoupled from market performance, which is the single most volatile
thing in existence.

This is the strongest argument that the QuantHQ business doc's instinct —
recruiting and reputation, not signal-selling — is correct. Hold that line even
when a "let's run a small fund off the top strategies" idea appears. It is the
idea that killed the last company that got this far.

---

## What's actually unclaimed

Two gaps nothing above fills:

1. **Neutrality.** Every serious evaluator of quant talent today is a fund that
   hires the talent it evaluates. There is no Moody's, no ETS, no neutral referee.
   That's the QuantScore/StrategyScore opening, and it's real.

2. **India.** None of these have meaningful NSE/BSE F&O depth — no PCR, no max
   pain, no OI structure, no promoter-holding or FII/DII data. Our
   `analyzers/fno_oi.py` is genuinely differentiated in a way the trend analyzers
   are not. In a landscape this crowded, that is not a small thing — it may be the
   only thing here that nobody else has.
