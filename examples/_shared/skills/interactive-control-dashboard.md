# Interactive control dashboard

Build the browser UI for the ANN2SNN simulator: pick a controller, run the benchmark and show
the trajectory, tracking error and (for the SNN) the spike raster. Runs on the **ann2snn**
image (Node.js + npm are available; the API is built by the `flask-gunicorn-sim-api` step).

## Rules

- Choose the simplest stack that works: a small **vanilla-JS + Plotly/Chart** page served as
  static files by the Flask app is fine; use React/Vite only if the repo already does.
- The UI must call the API (`GET /api/controllers`, `POST /api/simulate`), never re-implement
  the math in JS.
- Build assets in the node workspace (`npm ci && npm run build` when using a bundler); do not
  start a dev server. If a separate static server is needed, emit `DEPLOYMENT_REQUEST`.
- Show a clear error state when the API is unreachable; no secrets in the bundle.

## Panels

1. **Controls** — controller selector (PID / dense ANN / connectome ANN / SNN), seed input,
   steps slider, optional SNN `micro_steps` (read-only display), Run button.
2. **Plate trajectory** — target orbit vs controller path (equal-aspect scatter/line).
3. **Tracking error vs time** — radial error in cm, with the run's mean error.
4. **SNN spike raster** — first 100 neurons vs time (only when controller is `snn`).
5. **Run metadata** — controller, seed, steps, mean error (cm), wall time, torch version.

## Deliverables

- Built assets (or single `index.html` + JS module) under the repo's `web/`/`static/` folder,
  wired so the Flask app serves them at `/`.
- `web/README.md`: how to build, where assets go, and the exact `npm` commands used.
- Screenshot (`dashboard.png`) produced from a seeded run for the report/DAG deliverables.
