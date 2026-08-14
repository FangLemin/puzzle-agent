from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
from tempfile import gettempdir
import threading


DEFAULT_PRODUCTION_RUNTIME_DIR = Path.home() / "Desktop" / "puzzle_ops_runtime_prod"
LEGACY_TEMP_RUNTIME_DIR = Path(gettempdir()) / "puzzle_ops_agent_runtime"
DEFAULT_PRODUCTION_WRITE_COUNTRIES = ("日本", "法国")


def is_truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on", "prod", "production"}


def is_pytest_process() -> bool:
    import sys

    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in Path(sys.argv[0]).name


def resolve_runtime_dir() -> Path:
    configured = os.getenv("PUZZLEOPS_RUNTIME_DIR", "").strip()
    if configured:
        runtime_dir = Path(configured).expanduser()
    elif is_pytest_process():
        runtime_dir = LEGACY_TEMP_RUNTIME_DIR
    else:
        runtime_dir = DEFAULT_PRODUCTION_RUNTIME_DIR
    runtime_dir = runtime_dir.resolve()
    if is_truthy_env("PUZZLEOPS_PRODUCTION_MODE") and _is_under_temp(runtime_dir):
        raise RuntimeError(f"生产模式不能使用临时运行目录：{runtime_dir}")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir


def configured_runtime_dir() -> str:
    return os.getenv("PUZZLEOPS_RUNTIME_DIR", "").strip()


def production_write_countries() -> tuple[str, ...]:
    configured = os.getenv("PUZZLEOPS_WRITE_COUNTRIES", "").strip()
    if not configured:
        return DEFAULT_PRODUCTION_WRITE_COUNTRIES
    return tuple(country.strip() for country in configured.split(",") if country.strip())


def is_production_write_country(country: str) -> bool:
    return country in production_write_countries()


def create_runtime_backup(runtime_dir: Path, *, label: str = "") -> dict[str, object]:
    runtime_dir = runtime_dir.expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = _safe_label(label)
    backup_name = f"{timestamp}_{safe_label}" if safe_label else timestamp
    backup_root = runtime_dir / "backups"
    backup_dir = backup_root / backup_name
    backup_dir.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    for item in runtime_dir.iterdir():
        if item.name == "backups":
            continue
        target = backup_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        copied.append(item.name)
    manifest = {
        "status": "created",
        "runtime_dir": str(runtime_dir),
        "backup_dir": str(backup_dir),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "copied_items": copied,
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def maybe_create_daily_runtime_backup(runtime_dir: Path) -> dict[str, object]:
    today = datetime.now().strftime("%Y%m%d")
    backup_root = runtime_dir.expanduser().resolve() / "backups"
    marker = backup_root / f"daily_{today}.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"status": "exists", "marker": str(marker)}
    result = create_runtime_backup(runtime_dir, label=f"daily_{today}")
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def start_daily_runtime_backup(runtime_dir: Path) -> dict[str, object]:
    today = datetime.now().strftime("%Y%m%d")
    backup_root = runtime_dir.expanduser().resolve() / "backups"
    marker = backup_root / f"daily_{today}.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"status": "exists", "marker": str(marker)}
    backup_root.mkdir(parents=True, exist_ok=True)
    pending = {
        "status": "pending",
        "runtime_dir": str(runtime_dir.expanduser().resolve()),
        "marker": str(marker),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    marker.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")

    def worker() -> None:
        try:
            result = create_runtime_backup(runtime_dir, label=f"daily_{today}")
            marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            failed = {**pending, "status": "failed", "error": str(exc)}
            marker.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")

    threading.Thread(target=worker, name=f"puzzleops-daily-backup-{today}", daemon=True).start()
    return pending


def _is_under_temp(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(gettempdir()).resolve())
        return True
    except ValueError:
        return False


def _safe_label(label: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label.strip())[:40]
