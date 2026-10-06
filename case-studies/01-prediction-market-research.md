# Prediction market research infrastructure

I built a Python research workflow for Polymarket data, from collection and evidence checks to paper evaluation. The main challenge was checking whether historical data supported a credible test: which inputs were available at the decision time, whether quotes were usable, and how settlement was verified afterward.

## What I built

- A bounded collection workflow with retry limits, storage budgets and content hashes, so a run can be inspected against retained evidence.
- Validation checks for quote timing, instrument identity, crossed books, available size and confirmed settlement.
- Research tables that join candidate observations to their supporting quote and outcome evidence, while leaving incomplete observations out of evaluation.
- Chronological evaluation and capital-replay components that address shared parent events, overlapping exposure and cash tied up until settlement.

These are selected components of an evolving research codebase. Legacy analyses still require separate review. No validated out-of-sample edge or live trading profitability has been established.

## Research decisions

I treated data validity, statistical evidence and execution as separate questions. A quote can pass a format check and still be unsuitable for a replay. A paper fill can be mechanically consistent and still overstate real execution. Related markets can also share the same underlying event, making a random row split misleading.

The workflow retains inputs, records rejection reasons, separates related events across evaluation splits and tracks cash tied up until settlement. Further validation would require a frozen prospective evaluation with verified source timing and realistic execution assumptions.

## Public demonstration

The [synthetic demo](../README.md#run-the-demo) is a new, small implementation of those general engineering ideas. It uses invented observations and hand-specified order attempts. It contains no market selection rules, research hypotheses, private datasets or trading signals.

## Relevance to power markets

The transferable work is in Python research infrastructure, information timing and constrained decision-making. Short-term power research adds market-specific delivery, balancing, grid and asset constraints that this demonstration does not model.
