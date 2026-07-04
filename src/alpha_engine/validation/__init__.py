"""Validation layer: the trust engine. Records every emitted signal immutably,
scores recorded signals against what the market actually did, and backtests
analyzers over history with an explicit no-lookahead guarantee.

This layer is what separates the project from a tip-seller: it accumulates a
dataset of predictions vs. realized outcomes and reports the honest hit rate,
including the losers. Nothing here calls the network or an LLM; it reads the
cache and the signal log, and computes.
"""
