from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_interview_notes_cover_resume_ready_agent_topics():
    doc = ROOT / "docs" / "INTERVIEW_NOTES.md"

    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    for needle in (
        "为什么做这个项目",
        "一句话介绍",
        "系统架构",
        "RAG",
        "Memory",
        "Agent Harness",
        "FastAPI",
        "Qwen",
        "Milvus",
        "飞书",
        "HITL",
        "评测结果",
        "不足",
        "面试追问",
    ):
        assert needle in content


def test_resume_project_brief_is_concise_and_honest():
    doc = ROOT / "docs" / "RESUME_PROJECT_BRIEF.md"

    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    assert "大规模线上稳定预测" in content
    assert "不要写" in content
    assert "45 条真实拼图小样本" in content
    assert "610 passed" in content
    assert "多模态 Agent Harness" in content
    assert len(content.splitlines()) < 180


def test_readme_links_interview_materials():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/INTERVIEW_NOTES.md" in readme
    assert "docs/RESUME_PROJECT_BRIEF.md" in readme
