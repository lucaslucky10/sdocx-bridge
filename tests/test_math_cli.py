import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from handwriting_engine.compose import TEX2SVG, tex_layout
from sdocx_bridge.handwriting import compose_document


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(),
        subprocess.TimeoutExpired("node", 30),
        subprocess.CalledProcessError(1, "node"),
    ],
)
def test_optional_math_failure_is_actionable(lib, error):
    before = set((lib.root / "exports").iterdir())
    with patch("handwriting_engine.compose.subprocess.run", side_effect=error):
        with pytest.raises(ValueError, match="LaTeX"):
            compose_document(lib, "$A+B$", synthesize=False)
    assert set((lib.root / "exports").iterdir()) == before


@pytest.mark.skipif(
    not shutil.which("node")
    or not (Path(TEX2SVG).parent / "node_modules/mathjax-full").exists(),
    reason="optional MathJax installation",
)
def test_actual_mathjax_layout(lib):
    result = compose_document(lib, r"$\frac{A+B}{123}=A$", synthesize=False)
    assert result["strokes"] > 10 and not result["missing"]
    with pytest.raises(ValueError, match="Invalid LaTeX"):
        tex_layout(r"\notAnActualCommand")
    with pytest.raises(ValueError, match="unsupported text"):
        tex_layout(r"\text{한글}")


def test_cli_commands_and_config(lib, tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    def run(*args):
        p = subprocess.run(
            [sys.executable, "-m", "sdocx_bridge", "--library", str(lib.root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)

    config = run("claude-config")["mcpServers"]["sdocx-bridge"]
    assert Path(config["command"]).is_file()
    assert str(lib.root.resolve()) in config["args"]
    result = run("write", "AB가", "--seed", "9")
    note = run(
        "import", str(lib.root / "exports" / result["export_id"] / "handwriting.sdocx")
    )[0]
    assert any(n["id"] == note["id"] for n in run("list"))
    assert run("export", note["id"], "json")["files"] == ["ink.json"]
    profile = run(
        "profile", str(lib.root / "profiles/my-handwriting.json"), "--name", "cli-copy"
    )
    assert profile["characters"] == 9
