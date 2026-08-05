# Deal Scoring and Model Governance

## Overview

This document describes how opportunity scoring is constructed, calibrated, and governed within the [Deal Intelligence Pipeline](DEAL_INTELLIGENCE_PIPELINE.md).

Scoring is the component most likely to be misunderstood as a decision mechanism. It is not one. A score is a structured summary of evidence, produced consistently, presented to a human who decides what to do with it.

The governance controls in this document exist so that the basis of any score can be examined, challenged, reproduced, and corrected.

## Scoring Objectives

A scoring model in this context is designed to achieve three things:

1. **Consistency** — comparable opportunities receive comparable treatment regardless of when they arrive or who reviews them
2. **Explicitness** — the criteria applied to an opportunity are stated rather than implicit
3. **Traceability** — any score resolves to the inputs and weights that produced it

It is not designed to predict returns, and its accuracy is not evaluated on that basis.

## Score Structure

Each opportunity carries a profile of dimension scores rather than a single composite number.

| Dimension | Primary inputs | Sensitivity |
|-----------|---------------|-------------|
| Market | Population and employment growth, permits, absorption, rent trends | Medium |
| Financial | NOI, expense ratios, basis versus comparables, tax and insurance trajectory | High |
| Property condition | Age of major systems, deferred maintenance, capital expenditure history | Medium |
| Sponsor and operator | Realized performance against prior projections, alignment, capability | High |
| Macroeconomic | Rate sensitivity, employer concentration, regional exposure | Medium |
| Liquidity | Transaction volume, buyer depth, financing availability | Medium |
| Exit | Exit path optionality, cap rate sensitivity, hold flexibility | High |

A composite score may be presented for ranking, but the dimension profile is always shown with it. A composite alone hides the distinction between an opportunity that is uniformly acceptable and one that is excellent in most dimensions and unacceptable in one.

Dimension scores are never averaged into a decision. A single dimension scoring at the low end of its range is a routing signal for specialist review, not a proportional reduction in an aggregate.

## Portfolio-Conditional Scoring

For a diversified fund strategy, opportunity quality is partly a function of what the fund already holds.

Each opportunity is scored twice:

| Score | Question |
|-------|----------|
| Standalone | How good is this opportunity on its own merits? |
| Portfolio-marginal | What does adding this opportunity do to the current fund? |

The portfolio-marginal score accounts for concentration across:

- Geography and submarket
- Asset type and class
- Operating partner
- Vintage and acquisition timing
- Debt maturity distribution
- Business plan type

Two opportunities with identical standalone scores can carry materially different portfolio-marginal scores. Making that difference visible at screening — rather than at committee, after diligence has been spent — is a primary purpose of the scoring layer.

## Confidence and Data Quality

Every score carries a confidence indicator derived from the completeness and provenance of its inputs.

| Confidence | Condition |
|-----------|-----------|
| High | Complete inputs from verified sources, current data vintage |
| Medium | Minor gaps filled by documented defaults or proxies |
| Low | Material gaps, stale data, or unverified extractions |

A low-confidence score is not converted into a worse score. It is presented as a low-confidence score, because those are different statements and conflating them hides missing information behind an apparently precise number.

Defaults and proxies used to fill gaps are recorded with the score and reported alongside it.

## Calibration

Scoring weights are not set once and left in place. They are calibrated against outcomes the firm can observe.

**Initial calibration.** Weights are set from documented investment criteria and reviewed by investment professionals before first use. The starting point is the firm's stated strategy, not a fitted model — there is insufficient labeled data at the outset to fit anything defensible.

**Ongoing calibration.** As the assumption ledger accumulates, weights are reviewed against realized results:

- Which dimensions have distinguished outcomes, and which have not
- Which assumptions have been systematically optimistic or conservative
- Whether scoring outcomes vary in ways strategy does not explain

**Calibration constraints.** Calibration is bounded by the data available. A firm acquiring one to two assets per month accumulates outcome data slowly, and holding periods are long. Any weight change justified by a small number of realized outcomes is overfitting. Calibration cycles are therefore annual rather than continuous, and changes require documented rationale rather than statistical improvement alone.

This constraint is stated explicitly because it is the most likely source of misplaced confidence in the system.

## Model Risk Controls

Scoring models are treated as governed assets. The controls below follow established model risk management practice for financial institutions.

| Control | Requirement |
|---------|-------------|
| Version control | Every model version is recorded with its weights, inputs, and effective date |
| Change approval | Weight or logic changes are classified as governance changes and require human approval before deployment |
| Reproducibility | Any historical score can be regenerated with the model version and data vintage that produced it |
| Independent review | Model logic is reviewed by someone other than its author before deployment |
| Backtesting | Model behavior is reviewed against realized outcomes on a defined cycle |
| Drift monitoring | Input distribution and score distribution shifts are monitored and reported |
| Documented limitations | Known weaknesses and inapplicable conditions are recorded with the model |
| Override logging | Human decisions that diverge from model output are recorded with rationale |

Override logging deserves emphasis. Divergence between human judgment and model output is the most informative signal the system generates. Consistent divergence in one direction indicates either a miscalibrated model or an unstated criterion that belongs in it. Either finding is valuable, and both are lost if overrides are not recorded.

## Boundaries

The scoring layer operates under fixed constraints.

**A score never removes an opportunity from the pipeline.** Ranking determines the order in which opportunities receive attention. Only a human removes one from consideration.

**A score is never presented as a recommendation.** Output is a profile of evidence with confidence indicators, not an instruction.

**A score is never presented without provenance.** Data source and vintage accompany every dimension.

**A score is never used outside its documented scope.** A model calibrated for stabilized multifamily assets does not evaluate ground-up development, and applying it there is a governance exception requiring approval.

## Failure Modes

The failure modes below are the ones most likely to occur in practice and are monitored explicitly.

| Failure mode | Mechanism | Control |
|-------------|-----------|---------|
| False precision | A composite score implies accuracy the inputs do not support | Confidence indicators and mandatory dimension profile display |
| Anchoring | Reviewers defer to the score rather than evaluating independently | Score presented after the evidence summary; override logging |
| Historical bias | Weights encode past preferences and suppress unfamiliar opportunities | Recall-favoring screening; no autonomous rejection; periodic review of low-ranked outcomes |
| Data staleness | Inputs age without being flagged | Vintage recorded on every input; staleness reduces confidence |
| Silent degradation | Model behavior shifts as market conditions change | Drift monitoring and scheduled backtesting |
| Scope creep | A model is applied to opportunity types it was not built for | Documented scope; exceptions require approval |

Anchoring is the most consequential of these, because it undermines the design principle the pipeline is built on. If reviewers defer to scores rather than evaluate opportunities, the system has transferred judgment rather than supported it — and it will have done so without any explicit decision to allow that. Presentation order, override logging, and periodic review of what the system ranked low exist specifically to counter it.

## Review Cadence

| Activity | Frequency |
|----------|----------|
| Score reproducibility spot check | Monthly |
| Override review | Quarterly |
| Drift monitoring report | Quarterly |
| Backtesting against realized outcomes | Annual |
| Weight calibration review | Annual |
| Model scope and limitations review | Annual |
| Full model governance review | Annual |

## Related Documentation

- [Deal Intelligence Pipeline](DEAL_INTELLIGENCE_PIPELINE.md)
- [PAIOS Control Plane](PAIOS_CONTROL_PLANE.md)
- [AI Neural Governance Overview](AI_NEURAL_GOVERNANCE_OVERVIEW.md)
