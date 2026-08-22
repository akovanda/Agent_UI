# Build tool policy, approval, sandboxing, and audit controls

## Outcome

Permit selected agent mutations without allowing model text to become authorization.

## Scope

- Central tool registry and policy
- Argument validation
- User approval workflow
- Credential scoping
- Container sandbox and resource limits
- Audit records and redaction
- Emergency disable switch

## Tasks

- [ ] Classify tools by read, reversible write, external communication, destructive, and physical.
- [ ] Define allowlists for paths, commands, hosts, repositories, and APIs.
- [ ] Validate structured arguments independently of model prose.
- [ ] Require approval for all mutations and sensitive reads.
- [ ] Use dedicated least-privilege identities/credentials per integration.
- [ ] Add time, CPU, memory, output, turn, and network limits.
- [ ] Store redacted audit records with user/session/model/tool/result.
- [ ] Add one-command global tool disable.
- [ ] Build prompt-injection and confused-deputy tests.

## Acceptance criteria

- A model cannot perform a mutation without an independently enforced approval.
- Denied paths/hosts/commands remain inaccessible even when arguments are obfuscated.
- Credentials cannot be returned in model/tool output.
- Every attempted tool call has an auditable result without prompt/secret leakage.
- Emergency disable prevents new tool execution immediately.

## Dependencies

- Deploy Hermes Agent with a read-only capability baseline.
