# PR Delivery

Dormant unless explicit `CREATE_PR` authorization and successful final gates are both current. Maximum mode: `CREATE_PR`; preferred tier: BALANCED/STRONG. Baseline: `github-pr-workflow`.

Validate current base/head/diff/commits and supplied verified references, perform only the authorized push/create/update, then read back URL/ID/head/base/state. Never repair code, initiate causal history, invent provenance, merge, deploy, rewrite history/metadata, or change credentials.
