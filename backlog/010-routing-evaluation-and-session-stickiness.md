# Improve routing with evaluation data and session stickiness

## Outcome

Reduce expensive or disruptive wrong-model switches while preserving explicit user control.

## Scope

- Versioned routing evaluation set
- False-positive/false-negative scoring
- Session/profile stickiness
- Mixed-intent handling
- User-visible route explanation/override
- Optional learned classifier only if justified

## Tasks

- [ ] Collect at least 100 redacted representative prompts.
- [ ] Label required/preferred model and whether explicit selection is necessary.
- [ ] Measure current deterministic router precision/recall and switch cost.
- [ ] Add session stickiness with explicit reset and route-override semantics.
- [ ] Add ambiguous/mixed technical-story test cases.
- [ ] Tune rules and thresholds without overfitting.
- [ ] Evaluate a small CPU classifier only if it materially beats the baseline.

## Acceptance criteria

- Routing evaluation is reproducible in CI.
- Story false positives on technical prompts are below the agreed threshold.
- An active campaign session does not switch models because of one technical sentence unless
  explicitly requested.
- Every automatic decision remains inspectable and overrideable.
- Any learned router demonstrates a measured improvement before adoption.

## Dependencies

- Open WebUI and SillyTavern baseline usage data.
