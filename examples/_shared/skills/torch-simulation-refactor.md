# Torch simulation refactor (ball-and-plate / ANN2SNN)

Turn a monolithic PyTorch prototype into a clean, importable package, preserving the exact
numerical behaviour. Run on the **ann2snn** image (CPU `torch`, `numpy`, `matplotlib` Agg,
`pytest`).

## Rules

- Work only under `/workspace/<node_id>/`; write deliverables there (repo, tests, reports).
- Reproduce the prototype exactly: same constants, seeds, architecture sizes and training
  loop. Do not "improve" dynamics or swap algorithms.
- Never work around a missing tool: this image already has the stack; if something truly is
  missing, emit `CAPABILITY_REQUEST` and STOP.
- No secrets in code/artifacts (tokens come from env). Do not start servers.

## Reference prototype (bundled in the objective)

Monolithic script with: rolling-ball physics, a PD controller, a dense 1000-neuron ANN, a
sparse recurrent connectome ANN (Dale's law), and a lossless micro-stepping SNN; 100 epochs of
behavioural distillation; a 250-step dynamic-orbit benchmark; matplotlib plots.

Canonical constants (keep identical):

```python
GRAVITY = 9.81
C_CONST = (5.0 / 7.0) * GRAVITY   # rolling sphere
DT = 0.02                          # 50 Hz
MAX_TILT = 0.25
N_IN, N_NEURONS, N_OUT = 4, 1000, 2
SYNAPSES_PER_NEURON = 40
MICRO_STEPS = 10
EPOCHS = 100
STEPS, RADIUS, FREQ = 250, 0.15, 0.5
torch.manual_seed(42); np.random.seed(42)
if hasattr(torch.sparse, "check_sparse_tensor_invariants"):
    torch.sparse.check_sparse_tensor_invariants.disable()
```

## Suggested package layout

```
ann2snn/
  __init__.py
  config.py          # constants above
  physics.py         # step_physics(state, tilt) -> torch.Tensor(4)
  controllers/
    __init__.py
    pd.py            # ClassicalPDController.compute(...)
    dense_ann.py     # DenseNNController(nn.Module)
    connectome_ann.py# ConnectomeANNController(nn.Module) + sparse matrix builder
    snn.py           # LosslessConnectomeSNN(ann_model, micro_steps=10)
  train.py           # behavioral distillation -> state_dicts
  simulate.py        # closed-loop benchmark -> trajectories + mean radial error (cm)
  plots.py           # Agg savefig helpers (trajectory, error-vs-time, spike raster)
```

## Deliverables

1. The refactored package (importable: `from ann2snn.simulate import run_benchmark`).
2. A short `REFACTOR_NOTES.md`: module map, public API, exact constants retained, and a
   side-by-side proof that outputs match the prototype (seeded run, same errors to 3+ dp).
3. `python3 -c "import ann2snn; print(ann2snn.__version__)"` must succeed, and a seeded
   benchmark smoke run must emit finite mean radial errors for all four controllers.

## Then

Hand the package to the tests step (`control-benchmark-tests`) and the API step
(`flask-gunicorn-sim-api`); the API imports these modules directly.
