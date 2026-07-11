import os
import subprocess
import sys


def test_entrypoint_validation_error_leak():
    env = os.environ.copy()
    env["GLINTORY_GITHUB_TOKEN"] = "TOKEN_SECRET_12345"
    env["GLINTORY_DATABASE_URL"] = "sqlite:///private-secret-db.sqlite3"
    env["GLINTORY_LOCAL_LLM_ENABLED"] = "true"
    env["GLINTORY_LOCAL_LLM_MODEL_REVISION"] = ""  # Trigger ValidationError since revision is empty

    # 1. Non-JSON mode
    res_non_json = subprocess.run(
        [sys.executable, "-m", "glintory.entrypoint"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_non_json.returncode != 0
    assert "LLM_CONFIGURATION_INVALID" in res_non_json.stderr
    assert "TOKEN_SECRET_12345" not in res_non_json.stderr
    assert "TOKEN_SECRET_12345" not in res_non_json.stdout
    assert "private-secret-db.sqlite3" not in res_non_json.stderr
    assert "private-secret-db.sqlite3" not in res_non_json.stdout
    assert "Traceback" not in res_non_json.stderr
    assert "input_value" not in res_non_json.stderr
    assert "Users" not in res_non_json.stderr
    assert "home" not in res_non_json.stderr
    assert "tmp" not in res_non_json.stderr
    assert res_non_json.stdout.strip() == ""

    # 2. JSON mode
    res_json = subprocess.run(
        [sys.executable, "-m", "glintory.entrypoint", "--json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_json.returncode != 0
    assert "TOKEN_SECRET_12345" not in res_json.stderr
    assert "TOKEN_SECRET_12345" not in res_json.stdout
    assert "private-secret-db.sqlite3" not in res_json.stderr
    assert "private-secret-db.sqlite3" not in res_json.stdout
    assert "Traceback" not in res_json.stderr
    assert "input_value" not in res_json.stderr
    assert "Users" not in res_json.stderr
    assert "home" not in res_json.stderr
    assert "tmp" not in res_json.stderr
    assert res_json.stderr.strip() == ""

    import json

    data = json.loads(res_json.stdout.strip())
    assert data["operational_status"] == "failed"
    assert data["error_code"] == "LLM_CONFIGURATION_INVALID"
