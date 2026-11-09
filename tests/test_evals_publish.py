"""Tests for ``league-site evals publish`` / ``evals stream`` (league_site.evals.publish).

Every S3 interaction goes through :class:`FakeS3`; no real AWS, no credentials.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
from pathlib import Path

import pytest

from league_site.cli import main
from league_site.cli._commands import evals as evals_cmd
from league_site.evals import export, publish, schema

FIX = Path(__file__).parent / "fixtures" / "evals"
NOW = "2026-11-09T12:00:00Z"
BUCKET = "league-of-agents-test-evals-000000000000"
EVAL = "live-evals-prototype"
IMMUTABLE = "public, max-age=31536000, immutable"
RUNNING = "public, max-age=15, must-revalidate"
SEAT = "public, max-age=5"
CT = "application/json; charset=utf-8"


# --- fake S3 ----------------------------------------------------------------------------


class FakeClientError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    """Records every call; ``head_object`` on a missing key answers 403 like the real bucket
    does for an identity without ``s3:ListBucket``."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str]] = []
        self.snapshots: list[dict[str, bytes]] = []
        self.clock = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.timezone.utc)

    def _snap(self) -> None:
        self.snapshots.append({k: v["Body"] for k, v in self.objects.items()})

    def put_object(self, *, Bucket, Key, Body, **kw):  # noqa: N803
        assert Bucket == BUCKET
        self.calls.append(("put", Key))
        if kw.get("IfNoneMatch") == "*" and Key in self.objects:
            raise FakeClientError("PreconditionFailed", 412)
        self.writes.append(("put", Key))
        self.clock += dt.timedelta(seconds=1)
        self.objects[Key] = {
            "Body": bytes(Body),
            "ContentType": kw.get("ContentType"),
            "CacheControl": kw.get("CacheControl"),
            "Metadata": dict(kw.get("Metadata") or {}),
            "ETag": '"' + hashlib.md5(bytes(Body)).hexdigest() + '"',  # noqa: S324
            "LastModified": self.clock,
        }
        self._snap()
        return {}

    def head_object(self, *, Bucket, Key):  # noqa: N803
        self.calls.append(("head", Key))
        if Key not in self.objects:
            raise FakeClientError("403", 403)
        o = self.objects[Key]
        return {
            k: o[k] for k in ("ContentType", "CacheControl", "Metadata", "ETag", "LastModified")
        }

    def get_object(self, *, Bucket, Key):  # noqa: N803
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise FakeClientError("AccessDenied", 403)
        return {"Body": io.BytesIO(self.objects[Key]["Body"])}

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        self.calls.append(("delete", Key))
        self.writes.append(("delete", Key))
        self.objects.pop(Key, None)
        self._snap()
        return {}


# --- export fixture ---------------------------------------------------------------------


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _bundle_match(tag):
    m = json.loads((FIX / f"{tag}.json").read_text())
    for key in ("eval", "reason", "generated"):
        m.pop(key)
    m["teams"] = {s: {"name": t["name"], "model": t["model"]} for s, t in m["teams"].items()}
    m["pending"] = []
    return m


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    """A real export: hard-a finished (hashed name), thief-a4 live (``thief-a4.json``)."""
    cfg = json.loads((FIX / "eval-config.json").read_text())
    cfg["matches"]["thief-a4"]["status"] = "live"
    bundle = {"generated": NOW, "matches": [_bundle_match("hard-a"), _bundle_match("thief-a4")]}
    tdir = tmp_path / "thinking"
    _write(tdir / "hard-a-t1.json", [{"agent_id": "blue-1", "reasoning": "Move north."}])
    _write(tdir / "hard-a-t2.json", [{"agent_id": "blue-1", "reasoning": "Hold the point."}])
    _write(tdir / "thief-a4-t1.json", [{"agent_id": "blue-3", "reasoning": "Scout east."}])
    out = tmp_path / "out"
    export.export_eval(
        _write(tmp_path / "bundle.json", bundle),
        _write(tmp_path / "config.json", cfg),
        tdir,
        out,
        now=NOW,
    )
    return out


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    s3 = FakeS3()
    monkeypatch.setattr(evals_cmd, "_make_client", lambda profile=None: s3)
    return s3


def _publish_argv(export_dir: Path) -> list[str]:
    return ["evals", "publish", "--from", str(export_dir), "--eval", EVAL, "--bucket", BUCKET]


def _run_publish(export_dir, *extra):
    return main([*_publish_argv(export_dir), *extra])


# --- 1. order + reader consistency -----------------------------------------------------


def test_upload_order_thinking_match_summary_manifest_index(export_dir, fake, capsys):
    assert _run_publish(export_dir, "--apply", "--json") == 0
    capsys.readouterr()
    keys = [k for op, k in fake.writes if op == "put"]
    kinds = [
        (
            "index"
            if k == "evals/index.json"
            else {
                "thinking": "thinking",
                "match-final": "match",
                "match-running": "match",
                "summary": "summary",
                "manifest": "manifest",
            }[publish.classify(k[len(f"evals/{EVAL}/") :])]
        )
        for k in keys
    ]
    rank = {"thinking": 0, "match": 1, "summary": 2, "manifest": 3, "index": 4}
    assert [rank[x] for x in kinds] == sorted(rank[x] for x in kinds)
    assert kinds[-1] == "index" and kinds[-2] == "manifest" and kinds[-3] == "summary"
    assert "thinking" in kinds and "match" in kinds
    assert all(k.startswith("evals/") for k in keys)


def test_reader_snapshot_after_every_put_never_sees_dangling_reference(export_dir, fake, capsys):
    assert _run_publish(export_dir, "--apply") == 0
    capsys.readouterr()
    base = f"evals/{EVAL}/"
    local_thinking = sorted(
        p.name for p in (export_dir / EVAL / "thinking").glob("*.json")
    )  # e.g. hard-a-t1.json
    assert local_thinking
    for snap in fake.snapshots:
        for name in ("manifest.json", "summary.json"):
            if base + name in snap:
                doc = json.loads(snap[base + name])
                for entry in doc["matches"]:
                    assert base + entry["file"] in snap, (name, entry["file"])
        for key in snap:
            rel = key[len(base) :] if key.startswith(base) else ""
            if publish.classify(rel) in ("match-final", "match-running"):
                tag = json.loads(snap[key])["tag"]
                for t in local_thinking:
                    if t.startswith(f"{tag}-t"):
                        assert base + "thinking/" + t in snap
        if "evals/index.json" in snap:
            for e in json.loads(snap["evals/index.json"])["evals"]:
                assert f"evals/{e['id']}/manifest.json" in snap


# --- 2. scanner + validation refusals --------------------------------------------------


def test_thinking_file_failing_scan_aborts_with_nothing_uploaded(export_dir, fake, capsys):
    bad = export_dir / EVAL / "thinking" / "hard-a-t1.json"
    bad.write_text(json.dumps([{"agent_id": "blue-1", "reasoning": "see 10.0.0.12"}]))
    rc = _run_publish(export_dir, "--apply", "--json")
    out, err = capsys.readouterr()
    assert rc == 1
    assert fake.writes == [] and fake.calls == []
    assert "hard-a-t1.json" in err and "10.0.0.12" not in err


def test_invalid_match_file_or_manifest_aborts(export_dir, fake, capsys):
    man = json.loads((export_dir / EVAL / "manifest.json").read_text())
    del man["generated"]
    _write(export_dir / EVAL / "manifest.json", man)
    assert _run_publish(export_dir, "--apply") == 1
    assert fake.calls == []
    capsys.readouterr()


def test_invalid_match_file_aborts(export_dir, fake, capsys):
    p = export_dir / EVAL / "thief-a4.json"
    m = json.loads(p.read_text())
    m["status"] = "bogus"
    _write(p, m)
    assert _run_publish(export_dir, "--apply") == 1
    assert fake.calls == []
    capsys.readouterr()


def test_manifest_pointing_at_missing_file_aborts(export_dir, fake, capsys):
    (export_dir / EVAL / "thief-a4.json").unlink()
    assert _run_publish(export_dir, "--apply") == 1
    assert fake.calls == []
    capsys.readouterr()


def test_eval_mismatch_and_bad_ids_are_user_errors(export_dir, fake, capsys):
    rc = main(["evals", "publish", "--from", str(export_dir), "--eval", "../x", "--bucket", BUCKET])
    assert rc == 1
    rc = main(["evals", "publish", "--from", str(export_dir), "--eval", "nope", "--bucket", BUCKET])
    assert rc == 1
    assert fake.calls == []
    capsys.readouterr()


# --- 3. headers, immutability, skip-unchanged ------------------------------------------


def test_headers_per_kind(export_dir, fake, capsys):
    assert _run_publish(export_dir, "--apply") == 0
    capsys.readouterr()
    base = f"evals/{EVAL}/"
    seen = set()
    for key, obj in fake.objects.items():
        assert obj["ContentType"] == CT
        rel = key[len(base) :] if key.startswith(base) else key[len("evals/") :]
        kind = publish.classify(rel) if key.startswith(base) else "index"
        seen.add(kind)
        want = IMMUTABLE if kind in ("thinking", "match-final") else RUNNING
        assert obj["CacheControl"] == want, key
        assert obj["Metadata"]["sha256"] == hashlib.sha256(obj["Body"]).hexdigest()
    assert seen == {"thinking", "match-final", "match-running", "summary", "manifest", "index"}


def test_second_run_skips_unchanged(export_dir, fake, capsys):
    assert _run_publish(export_dir, "--apply") == 0
    n = len(fake.writes)
    capsys.readouterr()
    assert _run_publish(export_dir, "--apply", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(fake.writes) == n
    assert all(item["action"] == "skip" for item in payload["plan"])


def test_changed_running_file_uploads_but_immutable_never_overwritten(export_dir, fake, capsys):
    base = f"evals/{EVAL}/"
    tkey = base + "thinking/hard-a-t1.json"
    fake.objects[tkey] = {
        "Body": b"[]",
        "ContentType": CT,
        "CacheControl": IMMUTABLE,
        "Metadata": {"sha256": "0" * 64},
        "ETag": '"x"',
        "LastModified": fake.clock,
    }
    fake.objects[base + "thief-a4.json"] = dict(fake.objects[tkey], CacheControl=RUNNING)
    assert _run_publish(export_dir, "--apply", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert fake.objects[tkey]["Body"] == b"[]"
    assert ("put", tkey) not in fake.writes
    assert ("put", base + "thief-a4.json") in fake.writes
    item = next(i for i in payload["plan"] if i["key"] == tkey)
    assert item["action"] == "skip" and "immutable" in item["reason"]


def test_immutable_put_uses_if_none_match(export_dir, fake, capsys, monkeypatch):
    seen = []
    orig = fake.put_object

    def spy(**kw):
        seen.append((kw["Key"], kw.get("IfNoneMatch")))
        return orig(**kw)

    monkeypatch.setattr(fake, "put_object", spy)
    assert _run_publish(export_dir, "--apply") == 0
    capsys.readouterr()
    for key, inm in seen:
        rel = key.split(f"{EVAL}/", 1)[-1]
        if publish.classify(rel) in ("thinking", "match-final"):
            assert inm == "*"
        else:
            assert inm is None


def test_index_merges_remote_evals(export_dir, fake, capsys):
    fake.objects["evals/index.json"] = {
        "Body": json.dumps({"evals": [{"id": "older-eval", "title": "Older"}]}).encode(),
        "ContentType": CT,
        "CacheControl": RUNNING,
        "Metadata": {},
        "ETag": '"y"',
        "LastModified": fake.clock,
    }
    assert _run_publish(export_dir, "--apply") == 0
    capsys.readouterr()
    ids = [e["id"] for e in json.loads(fake.objects["evals/index.json"]["Body"])["evals"]]
    assert ids == ["older-eval", EVAL]


# --- 4. dry run + explain --------------------------------------------------------------


def test_dry_run_default_makes_no_client_calls_and_prints_json_plan(export_dir, fake, capsys):
    assert _run_publish(export_dir, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert fake.calls == [] and fake.writes == []
    assert payload["apply"] is False
    plan = payload["plan"]
    assert plan[-1]["key"] == "evals/index.json"
    assert plan[-2]["key"] == f"evals/{EVAL}/manifest.json"
    for item in plan:
        assert set(item) >= {"key", "kind", "size", "headers", "action", "reason"}
        assert item["headers"]["Content-Type"] == CT


def test_dry_run_text_table(export_dir, fake, capsys):
    assert _run_publish(export_dir) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and "manifest.json" in out and "--apply" in out
    assert fake.calls == []


def test_dry_run_compare_reads_but_never_writes(export_dir, fake, capsys):
    assert _run_publish(export_dir, "--compare", "--json") == 0
    capsys.readouterr()
    assert fake.writes == []
    assert {op for op, _ in fake.calls} <= {"head", "get"}


def test_custom_prefix(export_dir, fake, capsys):
    assert _run_publish(export_dir, "--prefix", "evals/staging", "--apply") == 0
    capsys.readouterr()
    assert all(k.startswith("evals/staging/") for _, k in fake.writes)


def test_explain_evals_publish_and_stream(capsys):
    assert main(["explain", "evals", "publish"]) == 0
    out = capsys.readouterr().out
    assert "evals publish" in out and "--apply" in out
    assert main(["explain", "evals", "stream"]) == 0
    assert "evals stream" in capsys.readouterr().out
    assert main(["explain", "evals"]) == 0
    capsys.readouterr()


def test_no_key_flags_and_profile_flag(export_dir, capsys):
    from league_site.cli import _build_parser

    p = _build_parser()
    args = p.parse_args([*_publish_argv(export_dir), "--profile", "league-evals"])
    assert args.profile == "league-evals" and args.apply is False
    for flag in ("--access-key", "--secret-key", "--aws-access-key-id"):
        with pytest.raises(SystemExit):
            p.parse_args([*_publish_argv(export_dir), flag, "x"])
    capsys.readouterr()


def test_aws_failure_is_env_error(export_dir, monkeypatch, capsys):
    class Boom(FakeS3):
        def head_object(self, **kw):
            raise RuntimeError("Unable to locate credentials")

    monkeypatch.setattr(evals_cmd, "_make_client", lambda profile=None: Boom())
    assert _run_publish(export_dir, "--apply") == 2
    capsys.readouterr()


# --- stream ----------------------------------------------------------------------------


TAG = "thief-a4"
LIVE = f"evals/{EVAL}/live/{TAG}/"


def _seat(live: Path, agent: str, turn: int, thinking: str, response: str = "", done=False):
    return _write(
        live / f"{agent}.json",
        {
            "turn": turn,
            "agent_id": agent,
            "thinking_so_far": thinking,
            "response_so_far": response,
            "updated": "2026-11-09T12:00:05Z",
            "done": done,
        },
    )


def _stream_argv(live: Path, *extra: str) -> list[str]:
    return [
        "evals",
        "stream",
        "--eval",
        EVAL,
        "--match",
        TAG,
        "--live-dir",
        str(live),
        "--bucket",
        BUCKET,
        "--once",
        *extra,
    ]


def _index(fake: FakeS3) -> dict:
    return json.loads(fake.objects[LIVE + "index.json"]["Body"])


def test_stream_uploads_seat_files_then_index_with_short_cache(tmp_path, fake, capsys):
    live = tmp_path / "live"
    _seat(live, "blue-1", 3, "I will move north.")
    _seat(live, "blue-2", 3, "Holding.", "ok")
    assert main(_stream_argv(live, "--apply")) == 0
    capsys.readouterr()
    puts = [k for op, k in fake.writes if op == "put"]
    assert puts[-1] == LIVE + "index.json"
    assert set(puts[:-1]) == {LIVE + "blue-1.json", LIVE + "blue-2.json"}
    for k in puts:
        assert fake.objects[k]["CacheControl"] == SEAT
        assert fake.objects[k]["ContentType"] == CT
    body = json.loads(fake.objects[LIVE + "blue-1.json"]["Body"])
    assert body["tag"] == TAG and body["turn"] == 3 and body["thinking_so_far"].startswith("I")
    idx = _index(fake)
    assert (
        idx["tag"] == TAG
        and idx["turn"] == 3
        and sorted(idx["answering"])
        == [
            "blue-1",
            "blue-2",
        ]
    )


def test_stream_unchanged_seat_not_reuploaded(tmp_path, fake, capsys):
    live = tmp_path / "live"
    _seat(live, "blue-1", 3, "I will move north.")
    main(_stream_argv(live, "--apply"))
    main(_stream_argv(live, "--apply"))
    capsys.readouterr()
    assert [k for _, k in fake.writes].count(LIVE + "blue-1.json") == 1


def test_stream_leak_never_uploaded_and_seat_stops_for_turn(tmp_path, fake, capsys):
    live = tmp_path / "live"
    _seat(live, "blue-1", 3, "Thinking...")
    _seat(live, "blue-2", 3, "Fine.")
    assert main(_stream_argv(live, "--apply")) == 0
    capsys.readouterr()
    _seat(live, "blue-1", 3, "Thinking... ssh to 10.1.2.3 now")
    assert main(_stream_argv(live, "--apply")) == 0
    err = capsys.readouterr().err
    assert "blue-1" in err and "ipv4" in err and "10.1.2.3" not in err
    uploaded = [
        json.loads(s[LIVE + "blue-1.json"]) for s in fake.snapshots if LIVE + "blue-1.json" in s
    ]
    assert all("10.1.2.3" not in u["thinking_so_far"] for u in uploaded)
    assert "blue-1" not in _index(fake)["answering"]
    n = [k for _, k in fake.writes].count(LIVE + "blue-1.json")
    # a later clean version for the same turn stays blocked
    _seat(live, "blue-1", 3, "All clean again.")
    assert main(_stream_argv(live, "--apply")) == 0
    capsys.readouterr()
    assert [k for _, k in fake.writes].count(LIVE + "blue-1.json") == n
    assert "blue-1" not in _index(fake)["answering"]
    # the next turn streams again
    _seat(live, "blue-1", 4, "New turn, clean.")
    assert main(_stream_argv(live, "--apply")) == 0
    capsys.readouterr()
    assert json.loads(fake.objects[LIVE + "blue-1.json"]["Body"])["turn"] == 4


def test_stream_first_chunk_leak_never_uploaded_never_listed(tmp_path, fake, capsys):
    live = tmp_path / "live"
    _seat(live, "blue-1", 3, "mail ops@example.com")
    assert main(_stream_argv(live, "--apply")) == 0
    capsys.readouterr()
    assert LIVE + "blue-1.json" not in fake.objects
    assert _index(fake)["answering"] == []


def test_stream_index_never_lists_a_seat_not_uploaded(tmp_path, fake, capsys):
    live = tmp_path / "live"
    _seat(live, "blue-1", 3, "a")
    _seat(live, "blue-2", 3, "b", "leak /home/user/x")
    _seat(live, "red-1", 3, "c")
    for _ in range(2):
        main(_stream_argv(live, "--apply"))
    _seat(live, "red-1", 3, "c", "final", done=True)
    main(_stream_argv(live, "--apply"))
    capsys.readouterr()
    for snap in fake.snapshots:
        if LIVE + "index.json" in snap:
            for agent in json.loads(snap[LIVE + "index.json"])["answering"]:
                assert LIVE + f"{agent}.json" in snap
                assert agent != "blue-2"
    assert _index(fake)["answering"] == ["blue-1"]


def test_stream_done_uploads_once_more_then_deletes_after_two_minutes(
    tmp_path, fake, capsys, monkeypatch
):
    live = tmp_path / "live"
    clock = [1000.0]
    monkeypatch.setattr(publish, "_now", lambda: clock[0])
    _seat(live, "blue-1", 3, "partial")
    main(_stream_argv(live, "--apply"))
    _seat(live, "blue-1", 3, "partial and final", "orders", done=True)
    main(_stream_argv(live, "--apply"))
    assert json.loads(fake.objects[LIVE + "blue-1.json"]["Body"])["done"] is True
    assert _index(fake)["answering"] == []
    clock[0] += 60
    main(_stream_argv(live, "--apply"))
    assert LIVE + "blue-1.json" in fake.objects
    clock[0] += 61
    main(_stream_argv(live, "--apply"))
    assert LIVE + "blue-1.json" not in fake.objects
    n = len(fake.writes)
    clock[0] += 10
    main(_stream_argv(live, "--apply"))
    assert [w for w in fake.writes[n:] if w[1] != LIVE + "index.json"] == []
    capsys.readouterr()


def test_stream_done_deleted_after_next_publish(tmp_path, fake, capsys, monkeypatch):
    live = tmp_path / "live"
    clock = [1000.0]
    monkeypatch.setattr(publish, "_now", lambda: clock[0])
    _seat(live, "blue-1", 3, "final", done=True)
    main(_stream_argv(live, "--apply"))
    assert LIVE + "blue-1.json" in fake.objects
    clock[0] += 5
    main(_stream_argv(live, "--apply"))
    assert LIVE + "blue-1.json" in fake.objects  # no publish yet
    # a publish run lands a new manifest after the done upload
    fake.put_object(
        Bucket=BUCKET, Key=f"evals/{EVAL}/manifest.json", Body=b"{}", CacheControl=RUNNING
    )
    clock[0] += 5
    main(_stream_argv(live, "--apply"))
    assert LIVE + "blue-1.json" not in fake.objects
    capsys.readouterr()


def test_stream_dry_run_no_client_calls(tmp_path, fake, capsys):
    live = tmp_path / "live"
    _seat(live, "blue-1", 3, "partial")
    _seat(live, "blue-2", 3, "leak 192.168.1.1")
    assert main(_stream_argv(live, "--json")) == 0
    out, err = capsys.readouterr()
    payload = json.loads(out.strip().splitlines()[-1])
    assert fake.calls == []
    keys = [u["key"] for u in payload["uploads"]]
    assert keys == [LIVE + "blue-1.json", LIVE + "index.json"]
    assert payload["apply"] is False
    assert "blue-2" in err
    # dry run leaves no state behind: the next apply still uploads
    assert main(_stream_argv(live, "--apply")) == 0
    capsys.readouterr()
    assert ("put", LIVE + "blue-1.json") in fake.writes


def test_stream_ignores_bad_names_and_mismatched_agent(tmp_path, fake, capsys):
    live = tmp_path / "live"
    _write(
        live / "blue-9.json",
        {
            "turn": 1,
            "agent_id": "red-1",
            "thinking_so_far": "x",
            "response_so_far": "",
            "updated": "t",
            "done": False,
        },
    )
    (live / "partial.json").write_text("{not json")
    assert main(_stream_argv(live, "--apply")) == 0
    capsys.readouterr()
    assert [k for _, k in fake.writes] == [LIVE + "index.json"]


def test_stream_seat_key_layout_matches_schema():
    assert LIVE + "blue-1.json" == f"evals/{EVAL}/" + schema.live_file_name(TAG, "blue-1")
