import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jobkit.execute import execute


def test_data_transform_square():
    out = execute("data.transform", {"items": [1, 2, 3], "op": "square"})
    assert out["results"] == [1, 4, 9]
    assert out["yielded"] is False


def test_data_transform_upper_and_sha256():
    assert execute("data.transform", {"items": ["a"], "op": "upper"})["results"] == ["A"]
    sha = execute("data.transform", {"items": ["x"], "op": "sha256"})["results"][0]
    assert isinstance(sha, str) and len(sha) == 64


def test_unknown_op_raises():
    with pytest.raises(ValueError):
        execute("data.transform", {"items": [1], "op": "nope"})


def test_challenge_is_exact():
    assert execute("challenge", {"x": 7}) == {"y": 50}


def test_yield_between_chunks():
    out = execute("data.transform", {"items": list(range(100)), "op": "square"},
                  should_yield=lambda: True)
    assert out["yielded"] is True
    assert len(out["results"]) == 0  # yielded before the first item


def test_ai_fallback_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = execute("ai.batch_infer", {"prompts": ["hello world"], "max_tokens": 8})
    assert out["backend"] == "fallback"
    assert out["results"][0]["tokens"] == 8


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        execute("does.not.exist", {})


def test_subprocess_entrypoint(tmp_path):
    src = Path(__file__).resolve().parents[2] / "src"
    spec = tmp_path / "in.json"
    out = tmp_path / "out.json"
    spec.write_text(json.dumps({"kind": "challenge", "input": {"x": 4}}))
    rc = subprocess.run(
        [sys.executable, "-m", "jobkit", str(spec), str(out)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(src)},
    )
    assert rc.returncode == 0, rc.stderr
    assert json.loads(out.read_text()) == {"y": 17}
