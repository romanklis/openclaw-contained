# Flask + Gunicorn simulator API

Expose the refactored ANN2SNN engine as a small HTTP API so a dashboard (or curl) can run the
four controllers interactively. Runs on the **ann2snn** image (`flask`, `flask-cors`,
`gunicorn` baked in).

## Rules

- Build the app under `/workspace/<node_id>/`; **do not start the server** — test with
  Flask's test client, then emit `DEPLOYMENT_REQUEST:<app>:<port>:<entrypoint>`.
- CPU-only, deterministic runs; cap work per request (steps/epochs) to keep responses fast.
- No secrets; CORS limited to the dashboard origins (configurable, default localhost).

## App

```python
# app.py
from flask import Flask, jsonify, request
from flask_cors import CORS
from ann2snn.simulate import run_benchmark
from ann2snn.config import DT, RADIUS, FREQ

app = Flask(__name__)
CORS(app)

CONTROLLERS = ["pid", "dense_ann", "connectome_ann", "snn"]

@app.get("/api/health")
def health(): return jsonify(status="ok")

@app.get("/api/controllers")
def controllers():
    return jsonify(controllers=CONTROLLERS, dt=DT, radius=RADIUS, freq=FREQ)

@app.post("/api/simulate")
def simulate():
    body = request.get_json(force=True, silent=True) or {}
    controller = body.get("controller", "pid")
    if controller not in CONTROLLERS:
        return jsonify(error=f"unknown controller '{controller}'"), 400
    steps = min(int(body.get("steps", 250)), 2000)
    result = run_benchmark(controller=controller, steps=steps, seed=int(body.get("seed", 42)))
    return jsonify(result)   # {trajectory, reference, error_cm, spikes?, meta}
```

## Deployment

Test first:

```bash
python3 -c "from app import app; c=app.test_client(); print(c.get('/api/health').json); \
print(c.post('/api/simulate', json={'controller':'snn','steps':50,'seed':42}).status_code)"
```

Then emit (do **not** run it):

```
DEPLOYMENT_REQUEST:ann2snn-ui:8080:gunicorn -b 0.0.0.0:8080 --workers 2 --timeout 120 app:app
```

## Deliverables

- `app.py` (+ `wsgi.py` if useful), `requirements` notes (all libs already in the image),
  OpenAPI-ish `API.md` documenting endpoints and payloads, and a curl example per controller.
