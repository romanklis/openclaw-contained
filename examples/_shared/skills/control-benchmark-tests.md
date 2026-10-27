# Control benchmark tests (pytest)

Write and run the test suite for the refactored ANN2SNN simulation engine on the **ann2snn**
image (`pytest` is baked in).

## Rules

- Tests live in the repo (`tests/`), deterministic and fast: small epochs/steps in CI mode,
  full benchmark available behind a marker (`-m slow`).
- Never weaken assertions to make tests pass; fix the code.
- No network access, no server start, no secrets.

## What to assert

1. **Physics** — `step_physics` integrates `v += -C·θ·dt`, `x += v·dt`, clamps `θ` to
   `±0.25`; invariants hold for random states.
2. **Controllers** — all four produce `θ ∈ [-MAX_TILT, MAX_TILT]`, finite outputs; the dense
   ANN has 1000 hidden units; the connectome ANN's sparse matrix shape is
   `(1000, 1000)` and obeys Dale's law signs (80% `+`, 20% `-`); the SNN performs exactly
   `micro_steps=10` spikes-driven updates per step and stays non-negative.
3. **Determinism** — with `torch.manual_seed(42)`/`np.random.seed(42)`, two benchmark runs
   produce identical mean radial errors.
4. **Convergence/quality** — seeded short benchmark: PD and all three learned controllers
   track the orbit (finite, bounded error; PD best/among best); no NaN/Inf spikes.
5. **API surface** — importing `ann2snn`, `from ann2snn.simulate import run_benchmark`, and
   constructing every controller works without side effects at import time.

## Commands

```bash
pytest -q                      # fast suite
pytest -q -m slow              # full 100-epoch / 250-step benchmark (when needed)
```

## Deliverables

- `tests/` suite + `pytest.ini`/markers.
- `TEST_REPORT.md`: command, environment (CPU torch version), pass/fail counts, and the
  measured mean radial errors per controller (cm) for the reference run.
