from __future__ import annotations

from collections.abc import Iterable
import sys
import types

from .models import SWEInstance


DATASET_NAME = "princeton-nlp/SWE-bench_Verified"


def _official_docker_image(row: dict) -> str:
    """Resolve the official prebuilt image without requiring Unix resource on Windows."""
    try:
        if sys.platform == "win32" and "resource" not in sys.modules:
            sys.modules["resource"] = types.ModuleType("resource")
        from swebench.harness.test_spec.test_spec import make_test_spec
        return str(make_test_spec(row, namespace="swebench").instance_image_key)
    except Exception:
        return ""


def _instance_from_row(row: dict) -> SWEInstance:
    enriched = dict(row)
    enriched["docker_image"] = _official_docker_image(enriched)
    return SWEInstance.from_row(enriched)


def load_verified_instances(
    *,
    limit: int = 5,
    instance_ids: Iterable[str] | None = None,
    dataset_name: str = DATASET_NAME,
) -> list[SWEInstance]:
    """Load a deterministic subset of up to ten instances unless IDs are supplied."""
    if limit < 1 or limit > 10:
        raise ValueError("Smoke runner limit must be between 1 and 10")
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError(
            'SWE-bench dependencies are missing; install with pip install -e ".[swebench]"'
        ) from exc

    selected_ids = [item.strip() for item in (instance_ids or []) if item.strip()]
    if len(selected_ids) > 10:
        raise ValueError("Smoke runner accepts at most 10 explicit instance IDs")
    rows = load_dataset(dataset_name, split="test")
    if selected_ids:
        wanted = set(selected_ids)
        by_id = {
            str(row["instance_id"]): row
            for row in rows
            if str(row.get("instance_id", "")) in wanted
        }
        missing = [item for item in selected_ids if item not in by_id]
        if missing:
            raise ValueError(f"Unknown SWE-bench instance IDs: {', '.join(missing)}")
        return [_instance_from_row(dict(by_id[item])) for item in selected_ids]
    return [_instance_from_row(dict(row)) for row in rows.select(range(limit))]
