# stack evolution

This is the canonical topology and phase map for `hermes-node`.
Use it to orient future work without re-deriving the stack boundary.

## Current topology

- Upstream Hermes: runtime substrate
- Hermes-node: constitutional membrane
- Operator: resident conversational foreman and wedge into the substrate
- Terminal and Telegram: current ingresses to the same operator layer
- Mini App: later ingress to the same operator layer, not a replacement stack
- Temporal: later durable orchestrator and scheduler above Hermes-node

## Design rules

- the operator is substrate-native, not surface-native
- the operator thinks through Hermes-node, not around it
- governance propagates downward before autonomy does
- durable state belongs in workspaces, receipts, plans, todos, scratchpads, and session artifacts, not in the live process
- broader upstream Hermes capabilities should become autonomous behavior only after Hermes-node policy wraps them

## Phase ladder

1. v0.5 Telegram wedge
2. v0.6 Deep-Agents-style operator harness
3. later callable specialist suboperators
4. later selectively autonomized specialist suboperators
5. later Temporal above Hermes-node
6. later Mini App on top of the same operator surface

## What belongs where

### Upstream Hermes

- model runtime substrate
- canonical Hermes CLI and provider integrations
- execution capabilities Hermes-node may call through

### Hermes-node

- policy membrane
- ledger, routing, approvals, workspaces, receipts
- bounded task submission and execution supervision

### Operator

- resident foreman
- conversational ingress for humans
- reads state, plans work, uses tools, submits bounded tasks, reports back

### Deep Agents harness later

- planning, decomposition, scratchpads, and bounded specialist suboperators inside the operator layer
- improves operator cognition without becoming a second control plane

### Temporal later

- durable orchestration and scheduling above Hermes-node
- calls into Hermes-node, does not bypass its policy membrane
