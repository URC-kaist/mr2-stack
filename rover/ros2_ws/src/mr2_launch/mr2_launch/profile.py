"""Persistent, checkout-local launch defaults. Explicit launch arguments win."""

from functools import lru_cache
import json
from pathlib import Path

from launch.actions import DeclareLaunchArgument as LaunchArgument


def repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "rover.bash").is_file():
            return parent
    return None


def read_profile(root):
    selection = root / ".mr2-rover-profile" if root else None
    name = selection.read_text(encoding="utf-8").strip() if selection and selection.exists() else "original"
    if name not in {"original", "flat"}:
        raise RuntimeError(f"Invalid rover profile: {name!r}; use scripts/rover.bash start {{flat|original}}")
    from ament_index_python.packages import get_package_share_directory

    config = Path(get_package_share_directory("mr2_launch")) / "config"
    data = json.loads((config / f"profile_{name}.json").read_text(encoding="utf-8"))
    return name, config, data


@lru_cache(maxsize=1)
def selected_profile():
    # A running launch keeps its selection even if the file changes afterward.
    return read_profile(repo_root())


def profile_arguments(entrypoint):
    name, config, data = selected_profile()
    defaults = {**data.get("common", {}), **data.get(entrypoint, {})}
    print(f"[mr2_launch] rover profile: {name} ({entrypoint})")

    def declare(argument, *, default_value=None, **kwargs):
        value = defaults.get(argument, default_value)
        if argument == "video_config" and argument in defaults:
            value = str(config / value)
        return LaunchArgument(argument, default_value=value, **kwargs)

    return declare
