"""Browser-test fixtures: the composed site plus a temp evals data dir, on a free port.

Run with ``uv sync --extra e2e && uv run playwright install chromium`` then
``uv run pytest -q -m browser tests/e2e``. Pass ``--update-evidence`` to rewrite the
screenshots under ``docs/design/evidence/``.
"""

from __future__ import annotations

import copy
import json
import mimetypes
import os
import shutil
import threading
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest

from league_site.evals import export
from league_site.web.http import site_app

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "evals"
EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "design" / "evidence"
EVAL_ID = "live-evals-prototype"
LIVE_TAG = "thief-a4"
FINISHED_TAG = "hard-a"
PER_MATCH_ONLY = ("eval", "reason", "generated")
DATA_PREFIX = "/evals/data/"


def pytest_addoption(parser):
    parser.addoption(
        "--update-evidence",
        action="store_true",
        default=False,
        help="write screenshots to docs/design/evidence/",
    )


def pytest_collection_modifyitems(items):
    for item in items:
        if "e2e" in Path(str(item.fspath)).parts:
            item.add_marker(pytest.mark.browser)


def _bundle_match(tag):
    m = json.loads((FIX / f"{tag}.json").read_text())
    for key in PER_MATCH_ONLY:
        m.pop(key)
    m["teams"] = {s: {"name": t["name"], "model": t["model"]} for s, t in m["teams"].items()}
    m["pending"] = []
    return m


def build_data(root: Path) -> Path:
    """Export hard-a (finished) and thief-a4 (marked live) into ``root``; returns the data dir."""
    bundle = {
        "generated": "2026-11-08T17:31Z",
        "matches": [_bundle_match(FINISHED_TAG), _bundle_match(LIVE_TAG)],
    }
    cfg = json.loads((FIX / "eval-config.json").read_text())
    cfg["matches"][LIVE_TAG]["status"] = "live"
    cfg["matches"][LIVE_TAG]["reason"] = "Running now."
    tdir = root / "_thinking"
    tdir.mkdir(parents=True)
    for tag in (FINISHED_TAG, LIVE_TAG):
        for turn in range(1, 41):
            entries = [{"agent_id": f"blue-{i}", "reasoning": f"T{turn} plan {i}"} for i in (1, 2)]
            (tdir / f"{tag}-t{turn}.json").write_text(json.dumps(entries))
    (root / "bundle.json").write_text(json.dumps(bundle))
    (root / "config.json").write_text(json.dumps(cfg))
    export.export_eval(
        root / "bundle.json",
        root / "config.json",
        tdir,
        root / "data",
        now="2026-11-09T12:00:00Z",
    )
    return root / "data"


class _Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        """Silence request logging."""


def _static(data_dir: Path):
    def app(environ, start_response):
        rel = environ["PATH_INFO"][len(DATA_PREFIX) :]
        target = (data_dir / rel).resolve()
        if data_dir.resolve() not in target.parents or not target.is_file():
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"not found"]
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        start_response(
            "200 OK",
            [
                ("Content-Type", ctype),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [body]

    return app


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    return build_data(tmp_path_factory.mktemp("evals-e2e"))


@pytest.fixture(scope="session")
def base_url(data_dir):
    """Overrides pytest-playwright's ``base_url`` so ``page.goto('/evals/')`` just works."""
    os.environ["LEAGUE_EVALS_DATA_BASE"] = "/evals/data"
    site, static = site_app(), _static(data_dir)

    def app(environ, start_response):
        if environ["PATH_INFO"].startswith(DATA_PREFIX):
            return static(environ, start_response)
        return site(environ, start_response)

    server = make_server("127.0.0.1", 0, app, server_class=_Threaded, handler_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class Live:
    """Edits the live match's published files the way the exporter would."""

    def __init__(self, ev: Path):
        self.ev = ev

    def _manifest(self):
        man = json.loads((self.ev / "manifest.json").read_text())
        return man, next(e for e in man["matches"] if e["tag"] == LIVE_TAG)

    def append_turn(self) -> int:
        """Append a copy of the last turn (turn + 1) and bump the entry's etag."""
        man, entry = self._manifest()
        path = self.ev / entry["file"]
        m = json.loads(path.read_text())
        nxt = copy.deepcopy(m["turns"][-1])
        nxt["turn"] = len(m["turns"]) + 1
        m["turns"].append(nxt)
        path.write_text(json.dumps(m))
        entry["etag"] = f"e2e-{nxt['turn']}"
        (self.ev / "manifest.json").write_text(json.dumps(man))
        return nxt["turn"]

    def stream(self, seats, turn, text="probing the east gate"):
        d = self.ev / "live" / LIVE_TAG
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.json").write_text(json.dumps({"turn": turn, "answering": seats}))
        for seat in seats:
            frame = {
                "agent_id": seat,
                "turn": turn,
                "updated": "2026-11-09T12:00:05Z",
                "thinking_so_far": text,
                "response_so_far": "MOVE 3,4",
            }
            (d / f"{seat}.json").write_text(json.dumps(frame))


@pytest.fixture
def live(data_dir):
    """The live-match mutator; restores the whole eval dir afterwards."""
    ev = data_dir / EVAL_ID
    backup = data_dir.parent / "_backup"
    shutil.copytree(ev, backup)
    yield Live(ev)
    shutil.rmtree(ev)
    shutil.copytree(backup, ev)
    shutil.rmtree(backup)
