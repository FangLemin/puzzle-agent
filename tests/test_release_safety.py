import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_release_preflight_script_exists_and_documents_secret_patterns():
    script = ROOT / "scripts" / "release_preflight.py"

    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "sk-" in content
    assert "QWEN_API_KEY" in content
    assert "FEISHU_APP_SECRET" in content
    assert "git ls-files" in content
    assert ".env" in content


def test_release_preflight_blocks_tracked_env_files_and_secret_patterns(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / ".env.example").write_text("QWEN_API_KEY=\n", encoding="utf-8")
    (fixture / "README.md").write_text("bad key sk-1234567890abcdef1234567890abcdef\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_preflight.py"), "--root", str(fixture), "--tracked-file", ".env"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "tracked forbidden file: .env" in result.stdout
    assert "secret-like pattern" in result.stdout


def test_release_safety_checklist_covers_public_github_risks():
    doc = ROOT / "docs" / "SECURITY_RELEASE_CHECKLIST.md"

    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    for needle in (
        ".env 不提交",
        "真实 API Key",
        "飞书",
        "真实业务图片",
        "绝对路径",
        "PUZZLEOPS_API_TOKENS",
        "scripts/release_preflight.py",
        "GitHub",
    ):
        assert needle in content
