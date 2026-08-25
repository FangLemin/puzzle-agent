from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_github_ci_runs_tests_and_release_preflight_without_remote_calls():
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.is_file()
    content = workflow.read_text(encoding="utf-8")

    for needle in (
        "python-version: '3.11'",
        "pytest tests -q",
        "python scripts/release_preflight.py",
        "ANALYSIS_LLM_ENABLE_REMOTE_CALLS: '0'",
        "RAG_ENABLE_REMOTE_CALLS: 'false'",
        "VISUAL_EMBEDDING_ENABLE_REMOTE_CALLS: 'false'",
        "QWEN_API_KEY: ''",
    ):
        assert needle in content


def test_public_security_policy_and_pull_request_checklist_exist():
    security = ROOT / "SECURITY.md"
    template = ROOT / ".github" / "pull_request_template.md"
    assert security.is_file()
    assert template.is_file()

    security_content = security.read_text(encoding="utf-8")
    template_content = template.read_text(encoding="utf-8")
    assert "Do not open a public issue" in security_content
    assert "scripts/release_preflight.py" in template_content
    assert "synthetic" in template_content
    assert "VERSION" in template_content
    assert "CHANGELOG.md" in template_content
