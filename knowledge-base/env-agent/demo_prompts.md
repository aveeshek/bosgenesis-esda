# ENV Agent Demo Prompts

Use these prompts from the ENV Agent page after selecting the target namespace, normally `agent-testing` for demos.

## Diagnostic Only

1. `Tell me how many pods have issues in this namespace.`
2. `Summarize pod health, recent restarts, and namespace events.`
3. `Which services or ingress objects look unhealthy in this namespace?`
4. `Check Helm release status and recent history for this namespace.`

## Restart-Loop Diagnosis

1. `My pod is getting restarted, can you diagnose the likely cause?`
2. `Find restart loops and correlate them with events and deployment readiness.`
3. `Explain what evidence is missing before a safe remediation can be selected.`

## Approval-Gated Remediation

1. Select mode `Approval-gated remediation`.
2. Ask: `Please restart deployment api in this namespace.`
3. Review the approval card: action, target, impact, rollback note, and verification plan.
4. Approve only if the namespace and resource target are correct.
5. Confirm the final report shows typed MCP execution and read-only verification.

## Safety Notes

- ENV Agent must not execute raw shell commands.
- Secret reads, cluster-wide changes, namespace deletion, and destructive requests must be blocked.
- High-risk actions must produce an approval gate before execution.
- Safe reasoning summaries may persist; hidden chain-of-thought must never be stored.
