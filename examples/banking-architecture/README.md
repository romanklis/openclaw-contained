# Banking Architecture — showcase example

Demonstrates the platform orchestrating a **bank landscape** DAG: agents query a
mock bank API/database, produce account/transaction artifacts, and run an
approval loop (accept / reject / cancel) before finalizing a report.

## What it shows
- Project-scoped DAG templates + skills (`banking-architecture` project).
- Example infrastructure (`bank-mock`, `bank-db`) joined to the platform network.
- A closed-loop approval (accept / reject → rework loop / cancel).
- Skill learning isolated to this project.

## Run
```bash
make example-up NAME=banking-architecture
# open the frontend, pick project "Banking Architecture", instantiate a template
make example-down NAME=banking-architecture
```

## Files
- `example.yaml` — manifest (infra, dags, skills, seed).
- `docker-compose.yml` — extra infra (mock bank + db), on the platform network.
- `config/dags/*.json` — DAG templates imported into the `banking-architecture` project.
- `config/skills/*.md` — bank-specific skills.
- `data/` — seed data (loaded into `bank-db` on first start).
