# One-brain rules (Axiom style)

How RedZone stays ONE connected system, modelled on Axiom's Lean Stacks project: every module
declares its inputs and outputs, is tagged to the spec line it serves, reuses one definition per
fact, has no escape hatches, and a checker (the proof kernel, `scripts/contract_check.py`)
verifies the whole graph on every change. The plan that gets us there:
[onebrain-migration.md](../onebrain-migration.md). Contracts and owners:
[architecture.md](../architecture.md). Where we stand: [northstar.md](../northstar.md).

These rules add to [CLAUDE.md](../../CLAUDE.md); they do not change a default or a gate (those
still need a line in [decisions.md](decisions.md)).

## 1. Claim with a blueprint before work

A claim is: id, branch, file list, blueprint, tests. Write it down before the first edit. No file
is in two open claims at once; a second claim on a file waits in the queue.

## 2. Core vs Frontier

- **Core** = code on the live decision path. It must be in the city map (`docs/citymap.md`) with
  an owner and a serves tag, have a typed contract (`redzone/contracts/`), add zero new kernel
  violations, and hold parity on the golden and dev-test nights.
- **Frontier** = `redzone/lab/`, `scripts/` and flags-off code. It may be rough. Core may not
  import it.
- **Promotion** = a PR that moves the code into Core, adds its contract and passes parity.

## 3. Tag every module

Every module names the North Star line or the playbook id ([playbooks.md](../playbooks.md)) it
serves. Untagged is a warning today and a failure once the baseline reaches 0.

## 4. Reuse, never parallel definitions

One definition per fact: league order, crunch, break lengths, dead ball, your team, timing. Import
it. A second copy is a kernel K1 violation.

## 5. No escape hatches

- No `getattr(x, "name", default)` on contract types.
- No inline "ignore" comments (lint, type or kernel).
- No `cfg.get` numeric fallbacks: the default lives in the config file, once.
- No private cross-module access (`other._name`).
- No lab import from Core.

Exceptions go in `docs/contract_baseline.json`, through a reviewed PR only.

## 6. The kernel runs on every change

`scripts/contract_check.py` runs locally and in CI on every PR. Report mode now. After each fix,
ratchet the baseline down in the same PR; the baseline never goes up.

## 7. Every migration step ships behind a flag

The flag ships OFF until parity holds and the 95% release gate passes (CLAUDE.md, Release gate).

## 8. Parity per PR, locked set per release

Parity is checked on the golden and dev-test nights in every PR (flag off vs on, diff the decision
stream). The locked set is scored once per release, never per PR (architecture.md section 8).
