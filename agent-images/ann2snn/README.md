# openclaw-agent:ann2snn

Capability agent image for PyTorch ANN/SNN simulation, control benchmarks and interactive
dashboards (derived from DAG `dag-309c6cac` / the ANN2SNN ball-and-plate task).

## What is baked in

| Layer | Contents |
|---|---|
| Python | `torch==2.4.1` (**CPU build**), `numpy==1.26.4`, `matplotlib==3.9.2` (Agg backend) |
| Testing | `pytest==8.3.3` |
| Web | `flask==3.0.3`, `flask-cors==5.0.0`, `gunicorn==23.0.0` |
| Media | `ffmpeg` (record/encode demo videos; MP4/H.264, X11/Xvfb capture) |
| Base | Node.js 20 + npm, git, curl, gcc/g++/make (from `openclaw-agent:openclaw`) |
| System | `libgomp1` (PyTorch CPU runtime) |
| Env | `MPLBACKEND=Agg`, `TORCH_HOME`/`XDG_CACHE_HOME` under `/home/agent/.cache` |

Not baked (by design): CUDA/GPU libs, browsers, secrets. Credentials come from env/credential
gateway; long-running servers must be requested with `DEPLOYMENT_REQUEST`.

## Build & verify

```bash
make build-ann2snn          # builds inside DinD and pushes registry:5000/openclaw-agent:ann2snn

docker exec openclaw-docker-dind docker run --rm --entrypoint python3 \
  registry:5000/openclaw-agent:ann2snn \
  -c "import torch,numpy,matplotlib,flask,gunicorn;print(torch.__version__,numpy.__version__)"

docker exec openclaw-docker-dind docker run --rm --entrypoint sh \
  registry:5000/openclaw-agent:ann2snn -lc "node --version && npm --version && pytest --version"
```

The profile is registered automatically from `agent-images/agent_profiles.yaml`
(`POST /api/agent-images/sync-from-yaml`, also run by `make example-up`/`make skills-import`).

## Skills (image `ann2snn`, ACTIVE)

| Skill | id | Use |
|---|---|---|
| `torch-simulation-refactor` | `skv2-02368a22` | Refactor the monolith into an importable package with identical numerics. |
| `control-benchmark-tests` | `skv2-222a3a32` | pytest suite (physics, controllers, determinism, convergence) + TEST_REPORT. |
| `flask-gunicorn-sim-api` | `skv2-e090abd5` | Flask API for the controllers; emit a Gunicorn `DEPLOYMENT_REQUEST`. |
| `interactive-control-dashboard` | `skv2-fde0d7f7` | Browser dashboard (trajectory, error, spike raster) built with Node/npm. |
| `github-push-with-token` | `skv2-9c50842e` | Commit/push with a token from env; scrub remote, no secrets. |

Bind from a DAG node:

```json
"config": {
  "base_image": "ann2snn",
  "selected_skill_name": "torch-simulation-refactor",
  "node_objective": "…"
}
```

## Validated

`torch 2.4.1+cpu`, `numpy 1.26.4`, `matplotlib 3.9.2`, `flask 3.0.3`, `gunicorn 23.0.0`,
node `v20.20.2`, npm `10.8.2`, pytest `8.3.3` all import/run inside the pushed image.
