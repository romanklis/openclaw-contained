"""Deterministic block registry.

Blocks are typed, tested, deterministic units (no LLM loop) that DAG flows
compose. Agents derive new blocks from successful exploratory runs; a human
reviews/activates them.
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Block
from typing import Dict, List, Any, Optional
from datetime import datetime
import json
import re

logger = __import__("logging").getLogger(__name__)
router = APIRouter(prefix="/api/blocks", tags=["blocks"])


def _slug(name: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return slug or "block"


def _serialize(b: Block) -> Dict[str, Any]:
    code_obj = b.code or {}
    code_meta = code_obj if isinstance(code_obj, dict) else {}
    return {
        "id": b.id,
        "name": b.name,
        "description": b.description or "",
        "runtime": b.runtime,
        "exec_runtime": code_meta.get("runtime") or "python",
        "runtime_image": code_meta.get("runtime_image"),
        "entrypoint": b.entrypoint,
        "inputs_schema": b.inputs_schema or {},
        "outputs_schema": b.outputs_schema or {},
        "code": b.code,
        "entry_file": code_meta.get("entry_file"),
        "conformance": b.conformance,
        "status": b.status,
        "version": b.version,
        "author": b.author or "",
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.get("")
async def list_blocks(status_filter: Optional[str] = None, db: AsyncSession = Depends(get_db)) -> List[Dict[str, Any]]:
    q = select(Block).order_by(Block.created_at.desc())
    if status_filter:
        q = q.where(Block.status == status_filter)
    rows = (await db.execute(q)).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/{name}")
async def get_block(name: str, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    b = await db.get(Block, name)
    if not b:
        raise HTTPException(status_code=404, detail=f"Block '{name}' not found")
    return _serialize(b)


def _normalize_handover(handover: dict, inputs_schema) -> dict:
    """Validate/normalize a block handover contract.

    Accepted keys: ``requires_any_of`` (list of input names, at least one
    required) and ``input_sources`` ({input_name: producer-relative/leaf path})."""
    if not isinstance(handover, dict) or not handover:
        raise ValueError("handover must be a non-empty object")
    props = (inputs_schema or {}).get("properties") if isinstance(inputs_schema, dict) else {}
    prop_keys = set(props.keys()) if isinstance(props, dict) else set()
    out: Dict[str, Any] = {}
    anyof = handover.get("requires_any_of")
    if anyof is not None:
        if not isinstance(anyof, list) or not anyof:
            raise ValueError("handover.requires_any_of must be a non-empty list of input names")
        bad = [str(n) for n in anyof if n not in prop_keys]
        if bad:
            raise ValueError(f"handover.requires_any_of names missing from inputs_schema.properties: {bad}")
        out["requires_any_of"] = [str(n) for n in anyof]
    insrc = handover.get("input_sources")
    if insrc is not None:
        if not isinstance(insrc, dict) or not insrc:
            raise ValueError("handover.input_sources must be a non-empty {input_name: source_path} object")
        bad = [str(n) for n in insrc if n not in prop_keys]
        if bad:
            raise ValueError(f"handover.input_sources names missing from inputs_schema.properties: {bad}")
        badv = [str(v) for v in insrc.values() if not isinstance(v, str) or not v]
        if badv:
            raise ValueError("handover.input_sources values must be non-empty source paths")
        out["input_sources"] = {str(k): str(v) for k, v in insrc.items()}
    if not out:
        raise ValueError("handover must include requires_any_of and/or input_sources")
    return out


def _apply_runtime_meta(code_payload: Dict[str, Any], code_src, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Merge exec runtime (python|image) + runtime_image into a code_json payload.

    Accepts the keys on the ``code`` dict and/or the top-level payload."""
    rt = rim = None
    if isinstance(code_src, dict):
        rt = code_src.get("runtime")
        rim = code_src.get("runtime_image")
    if rt is None:
        rt = payload.get("runtime")
    if rim is None:
        rim = payload.get("runtime_image")
    if rt is None and rim:
        rt = "image"
    if rt is not None:
        if rt not in ("python", "image"):
            raise HTTPException(status_code=400, detail="runtime must be 'python' or 'image'")
        if rt == "image" and not rim:
            raise HTTPException(status_code=400, detail="runtime 'image' requires runtime_image")
        code_payload["runtime"] = rt
        if rim:
            normalized = str(rim).replace("localhost:5000/", "registry:5000/")
            code_payload["runtime_image"] = normalized
        elif "runtime_image" in code_payload:
            code_payload.pop("runtime_image", None)
    return code_payload


def _pick_block_entry_file(files: dict, meta: str = "") -> str:
    """Choose the .py file that holds the block entrypoint function."""
    for candidate in ([meta, "block_main.py", "main.py"] if meta else ["block_main.py", "main.py"]):
        if candidate and candidate in files:
            return candidate
    if len(files) == 1:
        return list(files)[0]
    import re
    for fname, src in files.items():
        if re.search(r"^def\s+main\s*\(", str(src), re.MULTILINE):
            return fname
    raise HTTPException(status_code=400, detail=f"could not determine block entry file among: {list(files)}")


@router.post("", status_code=201)
async def register_block(payload: dict = Body(...), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    bid = str(payload.get("id") or _slug(name))
    existing = await db.get(Block, bid)
    if existing:
        raise HTTPException(status_code=409, detail=f"Block '{bid}' already exists")
    code = payload.get("code") or {}
    files = (code.get("files") or {}) if isinstance(code, dict) else {}
    if not isinstance(files, dict) or not files:
        raise HTTPException(status_code=400, detail="code.files with at least one source file is required")
    entry_file = _pick_block_entry_file(files, code.get("entry_file") if isinstance(code, dict) else "")
    code_payload: Dict[str, Any] = {"files": files, "entry_file": entry_file}
    if isinstance(code, dict) and code.get("handover"):
        try:
            code_payload["handover"] = _normalize_handover(code["handover"], payload.get("inputs_schema") or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    _apply_runtime_meta(code_payload, code, payload)
    b = Block(
        id=bid,
        name=name,
        description=str(payload.get("description") or ""),
        runtime=str(payload.get("runtime") or "worker-python"),
        entrypoint=str(payload.get("entrypoint") or "main"),
        inputs_schema=payload.get("inputs_schema") or {},
        outputs_schema=payload.get("outputs_schema") or {},
        code_json=code_payload,
        conformance_json=payload.get("conformance") or {},
        status=str(payload.get("status") or "draft"),
        version=int(payload.get("version") or 1),
        author=str(payload.get("author") or ""),
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    logger.info(f"🧩 Block registered: {bid} (runtime {b.runtime}, entry {b.entrypoint})")
    return _serialize(b)


@router.put("/{name}")
async def update_block(name: str, payload: dict = Body(...), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    b = await db.get(Block, name)
    if not b:
        raise HTTPException(status_code=404, detail=f"Block '{name}' not found")
    if payload.get("description") is not None:
        b.description = str(payload["description"])
    if payload.get("entrypoint") is not None:
        b.entrypoint = str(payload["entrypoint"])
    if payload.get("inputs_schema") is not None:
        b.inputs_schema = payload["inputs_schema"]
    if payload.get("outputs_schema") is not None:
        b.outputs_schema = payload["outputs_schema"]
    if payload.get("conformance") is not None:
        b.conformance_json = payload["conformance"]
    code_replaced = False
    if payload.get("code") is not None:
        files = (payload["code"].get("files") or {}) if isinstance(payload["code"], dict) else {}
        if isinstance(files, dict) and files:
            meta = payload["code"].get("entry_file") if isinstance(payload["code"], dict) else ""
            code_payload: Dict[str, Any] = {"files": files, "entry_file": _pick_block_entry_file(files, meta)}
            pcode = payload["code"]
            if isinstance(pcode, dict) and pcode.get("handover"):
                schema_for_handover = payload.get("inputs_schema") if payload.get("inputs_schema") is not None else (b.inputs_schema or {})
                try:
                    code_payload["handover"] = _normalize_handover(pcode["handover"], schema_for_handover)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
            _apply_runtime_meta(code_payload, pcode, payload)
            b.code_json = code_payload
            code_replaced = True
    has_runtime_meta = (
        "runtime" in payload or "runtime_image" in payload
        or (isinstance(payload.get("code"), dict)
            and ("runtime" in payload["code"] or "runtime_image" in payload["code"]))
    )
    has_handover_meta = (
        not code_replaced
        and isinstance(payload.get("code"), dict)
        and isinstance(payload["code"].get("handover"), dict)
    )
    if has_runtime_meta or has_handover_meta:
        # meta-only update (e.g. image-backed backfill / handover alignment):
        # preserve existing code
        cur = dict(b.code_json or {})
        if has_handover_meta:
            try:
                cur["handover"] = _normalize_handover(payload["code"]["handover"], b.inputs_schema or {})
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        if has_runtime_meta:
            _apply_runtime_meta(cur, payload.get("code"), payload)
        b.code_json = cur
    if payload.get("status") is not None:
        b.status = str(payload["status"])
    if payload.get("version") is not None:
        b.version = int(payload["version"])
    await db.commit()
    await db.refresh(b)
    return _serialize(b)


@router.delete("/{name}")
async def delete_block(name: str, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    b = await db.get(Block, name)
    if not b:
        raise HTTPException(status_code=404, detail=f"Block '{name}' not found")
    await db.delete(b)
    await db.commit()
    return {"ok": True, "name": name}


_BLOCK_RUNNER_SRC = r'''"""Deterministic block runner (subprocess) — used for conformance tests."""
import importlib.util
import json
import os
import sys


def _main() -> None:
    inputs_file, result_file = sys.argv[1], sys.argv[2]
    tmpdir = os.environ["BLOCK_TMPDIR"]
    entry_file = os.environ["BLOCK_ENTRY_FILE"]
    entry_name = os.environ["BLOCK_ENTRY_NAME"]
    sys.path.insert(0, tmpdir)
    with open(inputs_file, "r", encoding="utf-8") as fh:
        inputs = json.load(fh)
    modname = "block_entry_" + os.path.splitext(entry_file)[0].replace("-", "_")
    path = os.path.join(tmpdir, os.path.basename(entry_file))
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load entry module {entry_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    fn = getattr(module, entry_name, None)
    if fn is None or not callable(fn):
        raise AttributeError(f"entrypoint function '{entry_name}' not found in {entry_file}")
    result = fn(inputs)
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if not isinstance(result, dict):
        result = {"result": result}
    with open(result_file, "w", encoding="utf-8") as fh:
        json.dump(result, fh, default=str)


if __name__ == "__main__":
    try:
        _main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.exit(1)
'''


def _execute_python_module(files: dict, entry: str, entry_file: str, inputs: dict,
                           timeout_seconds: int = 290) -> Any:
    """Run a worker-python block by materializing files and executing the entry
    module in a subprocess — real imports work, matching the temporal worker."""
    import os as _os
    import shutil
    import subprocess
    import sys
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="block-conform-")
    try:
        for fname, src in files.items():
            if not fname.endswith(".py"):
                continue
            safe = _os.path.basename(fname)
            with open(_os.path.join(tmpdir, safe), "w", encoding="utf-8") as fh:
                fh.write(str(src))
        runner = _os.path.join(tmpdir, "_block_runner.py")
        with open(runner, "w", encoding="utf-8") as fh:
            fh.write(_BLOCK_RUNNER_SRC)
        inputs_file = _os.path.join(tmpdir, "_inputs.json")
        result_file = _os.path.join(tmpdir, "_result.json")
        with open(inputs_file, "w", encoding="utf-8") as fh:
            import json as _json
            _json.dump(inputs, fh, default=str)
        env = _os.environ.copy()
        env.update({
            "BLOCK_TMPDIR": tmpdir,
            "BLOCK_ENTRY_FILE": _os.path.basename(entry_file),
            "BLOCK_ENTRY_NAME": entry,
        })
        proc = subprocess.run(
            [sys.executable, runner, inputs_file, result_file],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip() or proc.stdout or "unknown error"
            raise RuntimeError(f"block execution failed ({err[-1500:]})")
        if not _os.path.isfile(result_file):
            raise RuntimeError("block runner produced no result file")
        with open(result_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"block execution timed out after {timeout_seconds}s: {(exc.stderr or '')[-800:]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _execute_python_block(block: Dict[str, Any], inputs: dict) -> Any:
    """Run a worker-python block (conformance tests) via the module subprocess runner."""
    import os

    code_obj = block.get("code") or {}
    files = (code_obj.get("files") or {}) if isinstance(code_obj, dict) else {}
    if not files:
        raise HTTPException(status_code=400, detail="block has no code files")
    os.environ.setdefault("CREDENTIAL_GATEWAY_URL", os.getenv("CREDENTIAL_GATEWAY_URL", "http://credential-gateway:8083"))
    entry = block.get("entrypoint") or "main"
    meta = code_obj.get("entry_file") if isinstance(code_obj, dict) else ""
    entry_file = _pick_block_entry_file(files, meta)
    return _execute_python_module(files, entry, entry_file, inputs)


@router.post("/{name}/test")
async def test_block(name: str, payload: dict = Body(default={}), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Run a block against inputs (default = conformance.sample_input) and check
    conformance.expect (deep-equality) if present."""
    import os

    b = await db.get(Block, name)
    if not b:
        raise HTTPException(status_code=404, detail=f"Block '{name}' not found")
    block = _serialize(b)
    if block.get("exec_runtime") == "image":
        return {
            "ok": False,
            "name": name,
            "error": (
                "image-backed block — conformance runs in the worker; test by running "
                "the block inside a DAG node."
            ),
            "runtime_image": block.get("runtime_image"),
        }
    inputs = payload.get("inputs")
    if inputs is None:
        inputs = (block.get("conformance") or {}).get("sample_input") or {}
    if payload.get("agent_token"):
        os.environ["TASK_ID"] = str(payload["agent_token"]).removeprefix("task:")
    try:
        result = _execute_python_block(block, inputs)
    except Exception as exc:
        return {"ok": False, "name": name, "error": str(exc)}
    expect = (block.get("conformance") or {}).get("expect")
    passed = True if expect is None else (result == expect)
    return {"ok": passed, "name": name, "result": result, "expect": expect, "passed": passed}


# ---------------------------------------------------------------------------
# LLM-driven "learn a block from this run" (mirrors skill-learning analyze)
# ---------------------------------------------------------------------------

_BLOCK_DERIVE_SYSTEM_PROMPT = """\
You are a senior deterministic-block engineer on an autonomous agent platform. A run has \
already proven an integration. You convert that proven run into a reusable deterministic \
BLOCK: pure code with a typed input contract, executed with NO LLM loop.

Engine contract (MUST be followed exactly):
- The block is executed as  entrypoint(inputs) -> dict  (entrypoint function is always "main").
- `inputs` is a dict. Reserved injected keys: `__node_dir__` (write all file outputs here; \
files written there are auto-collected as the block's deliverables), `__workspace_dir__`. \
`main()` MUST tolerate `__node_dir__` being absent (e.g. conformance tests) and fall back to \
a temporary/cwd directory.
- Runtime environment = the agent runtime image the run proved (e.g. a browser_v4-derived \
image). All packages that were available in that run are available to the block — copy the \
proven driver verbatim, including its imports (`agent_web`, `curl_cffi`, `httpx`, stdlib, \
etc.). If you need an import to make a proven call, use exactly the module the run used; do \
not invent new dependencies.
- The block never starts subprocesses/containers, never calls an LLM, and performs no \
re-discovery: reproduce exactly the behavior the run demonstrated.
- The block may call the credential gateway exactly like the driver does (env-provided \
CREDENTIAL_GATEWAY_URL / tokens) — copy that logic verbatim, do not rewrite it.
- Return a small JSON-serializable dict (like the run's manifest). Do not embed file blobs \
in the return value.
- Do NOT write any file to the block's own working directory other than under \
`__node_dir__`; never delete sibling files.

Input contract:
- `inputs_schema` must be a JSON Schema `{"type":"object","properties":{...},"required":[...]}` \
that ONLY reflects data the step actually received in the evidence (e.g. `urls` (array of \
strings) when the step downloaded a URL list handed over by a predecessor resolve node, or \
`album_url` when the step was given an album URL). Never invent parameters that would require \
new upstream wiring. Keys starting with `__` are reserved and must NOT be in the schema.
- Handover/typing: `upstream_outputs` lists the leaf fields (path + type + sample) actually \
produced by the step's predecessor node(s). Name required inputs after those leaf fields \
(e.g. `urls`, NOT the upstream node id), so the engine can inject them by name+type when the \
block is placed after the same kind of producer.
- If the step can also run standalone from a different source (e.g. an album link), keep that \
input optional and set `"handover": {"requires_any_of": ["urls", "album_url"]}` so at least \
one source is required.

Output contract:
- `outputs_schema` is REQUIRED: a JSON Schema `{"type":"object","properties":{...}}` for the \
dict `main` returns (e.g. a manifest: downloaded/skipped/files). File outputs written into \
`__node_dir__` become deliverables; describe them in the schema descriptions, not as fields.

Source material:
- The proven driver code the run used is provided under `sources.files`. COPY that code \
verbatim into the block's `code.files` (same filenames where possible) and add ONE extra file \
`block_main.py` containing `def main(inputs): ...` that calls the copied functions.
- EXECUTION MODEL: all provided `.py` files are materialized as real modules in one directory \
and `block_main.py` is imported and executed normally. Use STANDARD python imports to call the \
copied driver, e.g. `from google_photos import download_album` — this works. Put ALL logic \
needed by `main` into `block_main.py` (helpers included), importing provided files as modules. \
`main` must be defined in `block_main.py`.
Only fix genuine bugs or adapt the download client as stated above. Do NOT re-derive a fresh driver from the logs.

Respond with ONLY valid JSON (no markdown, no commentary), exactly this shape:
{
  "name": "<kebab-case block id seed, e.g. google-photos-album-download>",
  "description": "<one-line description>",
  "entrypoint": "main",
  "inputs_schema": {"type": "object", "properties": {...}, "required": [...]},
  "outputs_schema": {"type": "object", "properties": {...}},
  "handover": {"requires_any_of": ["urls", "album_url"]} | omit when inputs_schema.required is sufficient,
  "code": {"files": {"<filename>.py": "<full source>", "block_main.py": "<full source>"}},
  "rationale": "<short reasoning>",
  "warnings": ["<quality/determinism concerns, if any>"]
}
"""


def _unwrap_block_spec(parsed: dict) -> dict:
    """Unwrap LLM envelopes ({"block": {...}}, {"result": {...}}, ...) around a block spec."""
    if not isinstance(parsed, dict):
        return {}
    wrappers = ("block", "spec", "code", "data", "result", "analysis", "output", "payload")
    for _ in range(6):
        unwrapped = False
        for w in wrappers:
            if w in parsed and isinstance(parsed[w], dict):
                inner = parsed[w]
                if "code" in inner or "files" in inner or ("name" in inner and "inputs_schema" in inner):
                    parsed = inner
                    unwrapped = True
                    break
        if not unwrapped:
            break
    return parsed


def _safe_py_value(fname: Any, value: Any) -> Optional[str]:
    if not isinstance(fname, str) or not fname.endswith(".py"):
        return None
    text = value if isinstance(value, str) else str(value)
    if text.startswith("base64:"):
        try:
            import base64
            text = base64.b64decode(text[7:]).decode("utf-8", errors="replace")
        except Exception:
            text = ""
    if not text or len(text) > 300_000:
        return None
    return text


async def _harvest_driver_sources(db: AsyncSession, task, node, dag) -> Dict[str, Any]:
    """Collect the .py code the run actually used, with provenance.

    Precedence: recorded task deliverables -> the node's selected skill driver
    (SkillV2.code_json) -> workspace snapshot .skills/*.py (DAG level, then node
    level). Files are deduped by content (the skill code and the .skills snapshot
    are the same bytes in practice)."""
    import os
    from models import SkillV2, TaskOutput

    files: Dict[str, str] = {}
    origins: Dict[str, str] = {}
    seen: set = set()

    def _add(fname: str, text: str, origin: str) -> None:
        if len(files) >= 5 or fname in files or text in seen:
            return
        files[fname] = text
        seen.add(text)
        origins[fname] = origin

    rows = (
        await db.execute(
            select(TaskOutput).where(TaskOutput.task_id == task.id).order_by(TaskOutput.iteration.desc())
        )
    ).scalars().all()
    for out in rows:
        for fname, value in (out.deliverables or {}).items():
            text = _safe_py_value(fname, value)
            if text:
                _add(fname, text, f"recorded deliverable (task {task.id})")
        if files:
            break

    if node:
        svid = getattr(node, "selected_skill_v2_id", None)
        if svid:
            sv = await db.get(SkillV2, svid)
            if sv and getattr(sv, "code_json", None):
                code = sv.code_json or {}
                sf = code.get("files") if isinstance(code, dict) else None
                for fname, value in (sf or {}).items():
                    text = _safe_py_value(fname, value)
                    if text:
                        _add(fname, text, f"selected skill {sv.id} code")

    base = None
    if dag:
        wid = getattr(dag, "workspace_id", None) or f"workspace-{getattr(dag, 'id', '')}"
        if wid:
            base = f"/workspaces/{wid}"
    if base and os.path.isdir(base):
        node_id = getattr(node, "node_id", None) if node else None
        search_dirs = [
            os.path.join(base, ".skills"),
            os.path.join(base, node_id, ".skills") if node_id else None,
        ]
        for d in search_dirs:
            if not d or not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".py"):
                    continue
                try:
                    raw = open(os.path.join(d, fn), "r", encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                if 0 < len(raw) <= 300_000:
                    rel = os.path.relpath(os.path.join(d, fn), base)
                    _add(fn, raw, f"workspace file {rel}")

    return {"files": files, "origins": origins}


async def _call_block_derive_llm(context: dict) -> Dict[str, Any]:
    """Call the configured review/learning model to derive the block spec."""
    import httpx
    from routers.skill_learning import _deep_review_model, _extract_json_object

    model = _deep_review_model()
    text = json.dumps(context, default=str)
    async with httpx.AsyncClient(timeout=240) as client:
        for strict in (False, True):
            if strict:
                system = (
                    "Output ONLY a single valid JSON object. No markdown, no code fences, "
                    "no commentary, no trailing text. Begin with { and end with }."
                )
                user = f"Return the deterministic block spec as valid JSON only.\n\n{text}"
            else:
                system = _BLOCK_DERIVE_SYSTEM_PROMPT
                user = text
            payload = {
                "model": model,
                "max_tokens": 30000,
                "thinking": {"type": "disabled"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            try:
                resp = await client.post("http://localhost:8000/api/llm/v1/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                logger.warning("Block derive LLM request failed (%s): %s", model, exc)
                continue
            if resp.status_code != 200:
                logger.warning("Block derive LLM returned %s for %s: %s", resp.status_code, model, resp.text[:500])
                break
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _extract_json_object(content)
            if parsed is not None:
                parsed = _unwrap_block_spec(parsed)
                parsed["model"] = model
                parsed["_raw"] = content[:2000]
                return parsed
            logger.warning("Could not parse block derive JSON for %s (strict=%s)", model, strict)
    return {
        "error": f"LLM returned no parseable JSON block spec (model: {model}).",
        "model": model,
    }


def _infer_inputs_schema(files: Dict[str, str]) -> Dict[str, Any]:
    """Best-effort JSON Schema for main(inputs) when the LLM omits inputs_schema.

    Scans the provided sources for literal-key reads of the inputs dict
    (`inputs.get("album_url")` / `inputs["album_url"]`), excluding reserved `__`
    keys, and builds a JSON Schema object contract."""
    import ast

    found: List[str] = []
    for src in files.values():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr != "get" or not isinstance(node.func.value, ast.Name):
                    continue
                if node.func.value.id not in ("inputs",):
                    continue
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    key = node.args[0].value
                    if not key.startswith("__") and key not in found:
                        found.append(key)
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                if isinstance(node.value, ast.Name) and node.value.id == "inputs":
                    key = node.slice.value
                    if not key.startswith("__") and key not in found:
                        found.append(key)
    found.sort()
    if not found:
        return {}
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for key in found:
        t = "array" if key.endswith(("urls", "files", "items")) else "string"
        properties[key] = {"type": t, "description": key}
        if key not in required:
            required.append(key)
    return {"type": "object", "properties": properties, "required": required}


def _coerce_block_spec(spec: dict, fallback_name: str) -> Dict[str, Any]:
    """Validate/normalize the LLM block spec into code files + schema; raise ValueError."""
    import re

    code = spec.get("code") or {}
    if isinstance(code, dict):
        files_src = code.get("files")
    else:
        files_src = None
    if not isinstance(files_src, dict):
        files_src = spec.get("files")
    if not isinstance(files_src, dict) or not files_src:
        raise ValueError("LLM block spec has no code.files")

    files: Dict[str, str] = {}
    for fname, value in files_src.items():
        if len(files) >= 8:
            break
        if not isinstance(fname, str) or not fname.endswith(".py"):
            continue
        text = value if isinstance(value, str) else str(value)
        if not text or len(text) > 300_000:
            continue
        try:
            compile(text, fname, "exec")
        except SyntaxError:
            continue
        files[fname] = text
    if not files:
        raise ValueError("LLM block spec produced no valid .py code files")

    has_main = any(re.search(r"^def\s+main\s*\(", src, re.MULTILINE) for src in files.values())
    if not has_main:
        raise ValueError("LLM block spec defines no main(inputs) entrypoint")
    if "block_main.py" not in files:
        raise ValueError("LLM block spec must define main in a file named block_main.py")
    entry_file = "block_main.py"

    inputs_schema = spec.get("inputs_schema") or {}
    if not isinstance(inputs_schema, dict) or not inputs_schema:
        inputs_schema = _infer_inputs_schema(files)
    outputs_schema = spec.get("outputs_schema") or {}
    if not isinstance(outputs_schema, dict):
        outputs_schema = {}
    handover = spec.get("handover") or {}
    if not isinstance(handover, dict):
        handover = {}
    if handover:
        handover = _normalize_handover(handover, inputs_schema)
    name = str(spec.get("name") or fallback_name).strip().lower() or fallback_name
    description = str(spec.get("description") or "").strip()
    return {
        "name": name,
        "description": description,
        "entrypoint": "main",
        "files": files,
        "entry_file": entry_file,
        "inputs_schema": inputs_schema,
        "outputs_schema": outputs_schema,
        "handover": handover,
        "rationale": str(spec.get("rationale") or ""),
        "warnings": spec.get("warnings") or [],
        "model": spec.get("model") or "",
    }


def _short_value(v: Any, limit: int = 80) -> Any:
    s = str(v)
    return s if len(s) <= limit else s[:limit] + "…"


def _walk_leaf_fields(v: Any, path: str, out: List[Dict[str, Any]], depth: int = 0,
                      cap: int = 40) -> None:
    import numbers

    if depth > 4 or len(out) >= cap:
        return
    if isinstance(v, dict):
        for k, val in v.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(val, dict):
                _walk_leaf_fields(val, p, out, depth + 1, cap)
            elif isinstance(val, (list, tuple)):
                out.append({"path": p, "type": "array", "value": _short_value(val, 120)})
            else:
                t = "number" if isinstance(val, numbers.Number) else ("boolean" if isinstance(val, bool) else "string")
                out.append({"path": p, "type": t, "value": _short_value(val)})
    elif isinstance(v, (list, tuple)):
        for item in v:
            if isinstance(item, (dict, list, tuple)):
                _walk_leaf_fields(item, path, out, depth + 1, cap)
            else:
                break


async def _summarize_upstream_outputs(db: AsyncSession, node) -> Dict[str, Any]:
    """Summarize each predecessor node's stored output_data (leaf fields) as evidence."""
    from models import DAGNode

    deps = node.depends_on or []
    if not deps:
        return {}
    rows = (
        await db.execute(
            select(DAGNode).where(DAGNode.dag_id == node.dag_id, DAGNode.node_id.in_(list(deps)))
        )
    ).scalars().all()
    by_id = {r.node_id: r for r in rows}
    summary: Dict[str, Any] = {}
    for dep in deps:
        r = by_id.get(dep)
        if not r or not getattr(r, "output_data", None):
            continue
        od = r.output_data or {}
        leaves: List[Dict[str, Any]] = []
        _walk_leaf_fields(od, "", leaves)
        summary[dep] = {
            "node_type": getattr(r, "node_type", None),
            "block": (od.get("block") if isinstance(od, dict) else None),
            "leaf_fields": leaves,
        }
    return summary


@router.post("/learn-from-run", status_code=201)
async def learn_block_from_run(payload: dict = Body(...), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """LLM-derive a draft Block from a run, using the driver code the run actually used.

    Body: {task_id, dag_id?, node_id?, name?, description?, created_by?}. The captured
    driver source (recorded .py deliverables, the node's selected skill driver, and the
    DAG workspace .skills snapshot) plus the trimmed execution summary are given to the
    LLM, which produces the block code (verbatim driver + a main(inputs) entry) and its
    input schema. The result is stored as a draft Block for review/activation.
    """
    from models import DAGNode, MasterDAG, Task, TaskOutput

    task_id = str(payload.get("task_id") or "")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    node = None
    dag = None
    node_id = str(payload.get("node_id") or "").strip() or None
    if task.dag_id:
        dag = await db.get(MasterDAG, task.dag_id)
        if node_id:
            nr = await db.execute(
                select(DAGNode).where(DAGNode.dag_id == task.dag_id, DAGNode.node_id == node_id)
            )
            node = nr.scalars().first()

    result = await db.execute(
        select(TaskOutput).where(TaskOutput.task_id == task_id).order_by(TaskOutput.iteration)
    )
    outputs = list(result.scalars().all())
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for task '{task_id}'")

    from routers.skill_learning import _build_execution_summary, _trim_execution_summary_for_llm

    summary = _build_execution_summary(task, outputs, node=node)
    trimmed = _trim_execution_summary_for_llm(summary)
    harvested = await _harvest_driver_sources(db, task, node, dag)
    if not harvested["files"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "No .py driver source found for this run (checked recorded deliverables, the "
                "node's selected skill driver, and the DAG workspace .skills/ folder). Nothing "
                "to introspect into a block."
            ),
        )

    node_ctx = {}
    upstream_outputs = {}
    if node:
        node_ctx = {
            "node_id": node.node_id,
            "node_type": getattr(node, "node_type", None),
            "depends_on": node.depends_on or [],
            "config": node.config or {},
        }
        upstream_outputs = await _summarize_upstream_outputs(db, node)
    context = {
        "task_meta": {
            "task_id": task.id,
            "name": task.name,
            "description": task.description or "",
            "dag_id": task.dag_id,
            "node_id": node_id,
        },
        "node": node_ctx,
        "upstream_outputs": upstream_outputs,
        "sources": {
            "note": "Driver code this step actually used (verbatim source to copy).",
            "provenance": harvested["origins"],
            "files": harvested["files"],
        },
        "execution_summary": trimmed,
    }

    spec = await _call_block_derive_llm(context)
    if spec.get("error"):
        raise HTTPException(status_code=502, detail=spec["error"])
    fallback_name = f"{node.node_id}-block" if node else f"block-from-{task_id}"
    try:
        coerced = _coerce_block_spec(spec, fallback_name=fallback_name)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"{exc}. Raw LLM excerpt: {spec.get('_raw','')[:1500]}")

    payload_name = str(payload.get("name") or "").strip()
    display_name = payload_name or coerced["name"]
    requested_name = display_name.lower()
    bid = _slug(requested_name)
    n = 2
    while await db.get(Block, bid):
        bid = _slug(requested_name) + f"-{n}"
        n += 1

    origins = harvested["origins"]
    source_list = "; ".join(sorted(set(origins.values())))
    desc = coerced["description"]
    provenance_note = (
        f"Learned from task {task.id}"
        + (f", node {node.node_id}" if node else "")
        + (f", dag {task.dag_id}" if task.dag_id else "")
        + f". Source: {source_list}."
        + (f" Model: {coerced['model']}." if coerced["model"] else "")
    )
    if not desc:
        seed = (node.node_id if node else "") or (task.name or "")
        phrase = re.sub(r"[-_]+", " ", str(seed)).strip()
        if phrase:
            desc = phrase[0].upper() + phrase[1:] + "."
    description = f"{desc}\n{provenance_note}"[:500] if desc else provenance_note[:500]

    code_payload: Dict[str, Any] = {"files": coerced["files"], "entry_file": coerced["entry_file"]}
    if coerced.get("handover"):
        code_payload["handover"] = coerced["handover"]
    run_image = getattr(task, "current_image", None) or getattr(task, "current_image_tag", None) or ""
    if isinstance(run_image, str) and run_image.strip():
        run_image = run_image.strip()
        if ":" in run_image or "registry" in run_image or "localhost:5000" in run_image:
            code_payload["runtime"] = "image"
            code_payload["runtime_image"] = run_image.replace("localhost:5000/", "registry:5000/")
    b = Block(
        id=bid,
        name=display_name,
        description=description,
        runtime="worker-python",
        entrypoint="main",
        inputs_schema=coerced["inputs_schema"],
        outputs_schema=coerced["outputs_schema"],
        code_json=code_payload,
        conformance_json={},
        status="draft",
        version=1,
        author=str(payload.get("created_by") or "llm-learned"),
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    logger.info(
        "🧩 LLM-learned block %s from task %s (model %s, sources: %s)",
        bid,
        task.id,
        coerced["model"] or "?",
        source_list or "?",
    )
    return {
        "block": _serialize(b),
        "model": coerced["model"] or None,
        "provenance": origins,
        "warnings": coerced["warnings"],
        "rationale": coerced["rationale"],
    }


@router.post("/promote-from-task", status_code=201)
async def promote_task_to_block(payload: dict = Body(...), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Promote a successful task's .py driver deliverables into a draft Block."""
    import base64
    from models import Task, TaskOutput

    task_id = str(payload.get("task_id") or "")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    rows = (await db.execute(
        select(TaskOutput).where(TaskOutput.task_id == task_id).order_by(TaskOutput.iteration.desc())
    )).scalars().all()
    files = {}
    for out in rows:
        for fname, value in (out.deliverables or {}).items():
            if not isinstance(fname, str) or not fname.endswith(".py"):
                continue
            text = value if isinstance(value, str) else str(value)
            if text.startswith("base64:"):
                text = base64.b64decode(text[7:]).decode("utf-8", errors="replace")
            if text and len(text) <= 300_000:
                files[fname] = text
            if len(files) >= 5:
                break
        if files:
            break
    if not files:
        raise HTTPException(status_code=400, detail="Task has no .py driver deliverables to promote.")

    name = str(payload.get("name") or "").strip() or f"Driver from {task_id}"
    bid = str(payload.get("id") or _slug(name))
    if await db.get(Block, bid):
        raise HTTPException(status_code=409, detail=f"Block '{bid}' already exists")
    b = Block(
        id=bid,
        name=name,
        description=str(payload.get("description") or f"Deterministic block learned from task {task_id}."),
        runtime="worker-python",
        entrypoint=str(payload.get("entrypoint") or "main"),
        inputs_schema=payload.get("inputs_schema") or {},
        outputs_schema=payload.get("outputs_schema") or {},
        code_json={"files": files, "entry_file": _pick_block_entry_file(files)},
        conformance_json=payload.get("conformance") or {},
        status="draft",
        author=str(payload.get("author") or "task-promote"),
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return _serialize(b)
