# ADR-0005: Agent constraints are enforced at the binder, not in prompts

**Status:** Accepted

## Context

The obvious way to constrain an agent is to tell it what it may not do. That makes the constraint advisory: it depends on the model following instructions, and it degrades under adversarial or merely unusual input.

A governance model whose boundaries can be talked out of is not enforcing boundaries — it is requesting them.

## Decision

Permitted data sources, prohibited actions, and output destinations are applied by the persona binder as execution-time restrictions on the adapter. An action absent from the permitted set is not available to be called, whatever the generated text asks for.

Model output is treated as untrusted content throughout. Output that names a destination, requests an action, or asserts an approval carries no authority — destinations and actions come from the decision obligations, and the knowledge writer writes to the decision's `output_target` or fails.

## Consequences

Constraints hold regardless of model behavior, prompt injection through retrieved content, or model substitution — swapping the model behind a role does not change what the role can reach.

The cost is that every constrainable capability must be expressible as an adapter-level restriction. Capabilities that exist only as model behavior cannot be governed this way, and the design treats that as a reason not to expose them rather than a reason to relax the rule.
