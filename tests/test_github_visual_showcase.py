from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ASSET_DIR = ROOT / "docs" / "assets" / "readme"


def test_readme_has_visual_first_screen_and_real_demo_assets():
    content = README.read_text(encoding="utf-8")

    required_assets = (
        "puzzleops-hero.png",
        "demo-workflow.gif",
        "ui-trial.png",
        "ui-value-master.png",
        "ui-harness.png",
        "ui-api-metrics.png",
    )
    for filename in required_assets:
        assert f"docs/assets/readme/{filename}" in content
        assert (ASSET_DIR / filename).is_file()

    first_screen = "\n".join(content.splitlines()[:45])
    assert "puzzleops-hero.png" in first_screen
    assert "demo-workflow.gif" in first_screen
    assert "Agent Workflow" in content
    assert "Evaluation" in content


def test_readme_links_rendered_architecture_and_eval_visuals():
    content = README.read_text(encoding="utf-8")

    required_assets = (
        "architecture-overview.svg",
        "rag-pipeline.svg",
        "memory-lifecycle.svg",
        "eval-loop.svg",
        "evaluation-snapshot.svg",
    )
    for filename in required_assets:
        assert f"docs/assets/readme/{filename}" in content
        asset = ASSET_DIR / filename
        assert asset.is_file()
        assert asset.stat().st_size > 500


def test_public_metrics_are_unambiguous_and_match_release_reports():
    content = README.read_text(encoding="utf-8")

    assert "45 real samples" in content
    assert "Japan 25" in content
    assert "France 20" in content
    assert "45/50" not in content
    assert "SA high-potential binary accuracy" in content
    assert "60%" in content
    assert "Metric-derived grade baseline" in content
    assert "18%" in content
    assert "Precision@5" in content
    assert "20%" in content


def test_visual_assets_do_not_embed_private_runtime_strings():
    forbidden = (
        "/Users/fanglemin",
        "open.feishu.cn",
        "QWEN_API_KEY=sk-",
        "FEISHU_APP_SECRET=",
    )
    for path in ASSET_DIR.glob("*") if ASSET_DIR.exists() else ():
        if path.suffix.lower() not in {".svg", ".md", ".txt"}:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for text in forbidden:
            assert text not in content
