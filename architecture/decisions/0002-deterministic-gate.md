# ADR-0002: The decision gate is deterministic and separate from classification

**Status:** Accepted

## Context

Classifying a free-text request benefits from a model. Deciding whether that request may execute does not.

A governance decision has to be defensible to an auditor, reproducible on demand, and identical for identical inputs. A model in the decision path makes none of those properties hold: the same request can produce different outcomes on different days, and the reasoning cannot be re-derived.

## Decision

Classification and decision are separate stages. The classifier may use a model. The Policy Decision Point may not — it is a pure function of the classification result, the resolved context, and the pinned policy bundle.

The classifier itself runs deterministic rules first, and the model-assisted pass applies only to what those rules left unclassified. The model-assisted pass may raise sensitivity but never lower it, and a result below the confidence threshold becomes `unclassified`, which scores as sensitive.

## Consequences

Decisions are reproducible and explainable from the matched rule IDs plus the bundle version. Model behavior can change without changing governance outcomes for anything the deterministic rules already cover.

The cost is that deterministic rules have to be maintained as request patterns drift, and the framework carries an explicit health signal for this — a rising classifier fallback rate means the rules are falling behind reality.
