"""Node-run tests for infra/cloudflare/evals-worker.js (stubbed fetch)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_WORKER = Path(__file__).resolve().parent.parent / "infra" / "cloudflare" / "evals-worker.js"

_HARNESS = r"""
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[1]).href);
const worker = mod.default;
const calls = [];
let originStatus = 200, originCC = null;
globalThis.fetch = async (url, init) => {
  calls.push(String(url));
  const h = originCC ? { "Cache-Control": originCC } : {};
  const body = originStatus === 200 ? "{}" : "denied";
  return new Response(body, { status: originStatus, headers: h });
};
const env = { EVALS_ORIGIN: "bkt.s3.us-east-1.amazonaws.com" };
const out = {};
async function get(path, status = 200, cc = null, method = "GET") {
  originStatus = status; originCC = cc;
  const req = new Request("https://league-of-agents.ai" + path, { method });
  const r = await worker.fetch(req, env);
  return { status: r.status, cc: r.headers.get("Cache-Control") };
}
const D = "/evals/data/hard-a/";
out.map = await get(D + "manifest.json");
out.calls = calls.slice();
out.missing = await get(D + "nope.json", 403);
out.missing404 = await get(D + "nope.json", 404);
out.manifest = await get(D + "manifest.json");
out.running = await get(D + "m1/data.json");
out.finished = await get(D + "m1/data.json", 200, "public, max-age=31536000, immutable");
out.hashed = await get(D + "m1/data.0123abcd.json");
out.thinking = await get(D + "m1/thinking/blue-t3.json");
out.seat = await get(D + "m1/live/blue.json");
out.traversal = await get("/evals/data/../secret");
out.outside = await get("/evals/other.json");
out.post = await get("/evals/data/x.json", 200, null, "POST");
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory) -> dict:
    if _NODE is None:
        pytest.skip("node not available")
    d = tmp_path_factory.mktemp("worker")
    mjs = d / "worker.mjs"
    mjs.write_text(_WORKER.read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run(
        [_NODE, "--input-type=module", "-e", _HARNESS, str(mjs)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(proc.stdout)


def test_worker_is_small() -> None:
    assert len(_WORKER.read_text(encoding="utf-8").splitlines()) < 60


def test_path_mapping(result) -> None:
    assert result["calls"] == ["https://bkt.s3.us-east-1.amazonaws.com/evals/hard-a/manifest.json"]


def test_missing_object_is_404(result) -> None:
    assert result["missing"]["status"] == 404
    assert result["missing404"]["status"] == 404
    assert result["traversal"]["status"] == 404
    assert result["outside"]["status"] == 404
    assert result["post"]["status"] == 405


def test_cache_control_per_kind(result) -> None:
    imm = "public, max-age=31536000, immutable"
    run = "public, max-age=15, must-revalidate"
    assert result["manifest"]["cc"] == run
    assert result["running"]["cc"] == run
    assert result["finished"]["cc"] == imm
    assert result["hashed"]["cc"] == imm
    assert result["thinking"]["cc"] == imm
    assert result["seat"]["cc"] == "public, max-age=5"
