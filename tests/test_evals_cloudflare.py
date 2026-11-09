"""Dry-run contract of scripts/evals-cloudflare.sh and the runbook's evals section."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "evals-cloudflare.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "cloudflare-league-of-agents-ai.md"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    # PATH holds only symlinks to the coreutils the script needs, plus recording
    # stubs for the network tools: any call to them is logged and fails the test.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("dirname", "awk", "sed", "mktemp", "cat"):
        found = shutil.which(tool)
        assert found
        (bin_dir / tool).symlink_to(found)
    log = tmp_path / "calls.log"
    for tool in ("cultureflare", "curl", "aws"):
        stub = bin_dir / tool
        stub.write_text(f"#!/bin/sh\necho {tool} >> {log}\nexit 9\n")
        stub.chmod(0o755)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    proc = subprocess.run(
        ["/bin/bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30
    )
    assert not log.exists(), log.read_text()
    return proc


def test_dry_run_default_prints_plan_without_tools(tmp_path: Path) -> None:
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "dry run" in proc.stdout
    assert "league-of-agents.ai/evals/data/*" in proc.stdout
    assert "[vars]" in proc.stdout


def test_apply_without_wrangler_refuses(tmp_path: Path) -> None:
    proc = _run(tmp_path, "--apply")
    assert proc.returncode == 2
    assert "wrangler" in proc.stderr


def test_unknown_flag_rejected(tmp_path: Path) -> None:
    assert _run(tmp_path, "--bogus").returncode == 2


def test_runbook_covers_route_cache_and_unpublish() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "## Live evals data" in text
    for needle in (
        "/evals/data/*",
        "Origin Rule",
        "max-age=31536000, immutable",
        "max-age=15, must-revalidate",
        "cf-cache-status",
        "### Unpublish",
        "purge_cache",
        "s3:GetObjectVersion",
    ):
        assert needle in text, needle
