from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER_SOURCE = ROOT / "scripts" / "tikal-java17.sh"
LOCAL_OKAPI = ROOT / ".runtime" / "okapi-1.48.0"
LOCAL_JAVA_HOME = ROOT / ".runtime" / "java17" / "jdk-17.0.20+8-jre" / "Contents" / "Home"


def _copy_wrapper_to_temp_okapi(tmp_path: Path) -> Path:
    if not (LOCAL_OKAPI / "lib").is_dir():
        pytest.skip("local Okapi 1.48.0 lib directory unavailable")
    if not (LOCAL_JAVA_HOME / "bin" / "java").is_file():
        pytest.skip("local Java 17 runtime unavailable")
    okapi_dir = tmp_path / "okapi-1.48.0"
    okapi_dir.mkdir()
    (okapi_dir / "lib").symlink_to(LOCAL_OKAPI / "lib", target_is_directory=True)
    wrapper = okapi_dir / "tikal-java17.sh"
    shutil.copy2(WRAPPER_SOURCE, wrapper)
    wrapper.chmod(0o755)
    return wrapper


def test_tikal_java17_wrapper_executes_real_okapi(tmp_path):
    wrapper = _copy_wrapper_to_temp_okapi(tmp_path)
    env = {**os.environ, "JAVA_HOME": str(LOCAL_JAVA_HOME)}

    result = subprocess.run(
        [str(wrapper)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env=env,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0
    assert "Version: 2.1.48.0" in output or "1.48.0" in output
    assert "NoClassDefFoundError" not in output
    assert "ClassNotFoundException" not in output


def test_tikal_java17_wrapper_requires_valid_java_home(tmp_path):
    wrapper = _copy_wrapper_to_temp_okapi(tmp_path)
    invalid_java_home = tmp_path / "private-user-path" / "missing-jdk"
    env = {**os.environ, "JAVA_HOME": str(invalid_java_home)}

    result = subprocess.run(
        [str(wrapper)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env=env,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "Java 17 runtime is unavailable." in output
    assert str(invalid_java_home) not in output
    assert "Version: 2.1.48.0" not in output
