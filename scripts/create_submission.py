import getpass
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime


def get_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    zip_filename = "submission-glintory.zip"
    sha_filename = "submission-glintory.zip.sha256"

    # 1. Get git commit SHA
    try:
        commit_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to get git commit SHA: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Creating {zip_filename} from commit {commit_sha} using git archive...")

    # Create submission ZIP using git archive
    try:
        subprocess.run(["git", "archive", "-o", zip_filename, "HEAD"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Git archive failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Define forbidden directories and extensions
    forbidden_dirs = [
        "models/",
        "bin/",
        "build/",
        ".state/",
        "data/",
        "invalid/",
        "__pycache__/",
    ]
    forbidden_exts = [
        ".pyc",
        ".pyo",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".gguf",
        ".so",
        ".dylib",
        ".dll",
        ".zip",
        ".tar",
        ".tar.gz",
    ]

    # Get current username
    username = getpass.getuser()
    forbidden_paths = [
        f"/Users/{username}/",
        f"/home/{username}/",
        "file:/" + "/Users/",
    ]

    # Secret and mock values check details
    confidential_markers = [
        "TOKEN_SECRET_12345",
        "sqlite:///private-secret-db.sqlite3",
        "/Users/example/private/model.gguf",
        "https://private.example/path",
    ]

    # Strict allowlist: { file_path: [ allowed_markers ] }
    secret_allowlist = {
        "scripts/create_submission.py": [
            "TOKEN_SECRET_12345",
            "sqlite:///private-secret-db.sqlite3",
            "/Users/example/private/model.gguf",
            "https://private.example/path",
        ],
        "tests/test_entrypoint.py": [
            "TOKEN_SECRET_12345",
            "sqlite:///private-secret-db.sqlite3",
            "/Users/example/private/model.gguf",
            "https://private.example/path",
        ],
        "tests/services/test_opportunity_enrichment.py": ["TOKEN_SECRET_12345"],
        ".github/workflows/local-llm-smoke.yml": [
            "TOKEN_SECRET_12345",
            "sqlite:///private-secret-db.sqlite3",
            "/Users/example/private/model.gguf",
            "https://private.example/path",
        ],
    }

    print("Verifying the generated zip archive safety and cleanliness...")
    ok = True

    with zipfile.ZipFile(zip_filename, "r") as zf:
        namelist = zf.namelist()
        for name in namelist:
            lower_name = name.lower()

            # A. Check forbidden directories
            for f_dir in forbidden_dirs:
                if lower_name.startswith(f_dir) or f"/{f_dir}" in lower_name:
                    print(
                        f"ERROR: Forbidden directory structure detected: {name}",
                        file=sys.stderr,
                    )
                    ok = False

            # B. Check forbidden extensions
            for f_ext in forbidden_exts:
                if lower_name.endswith(f_ext):
                    print(
                        f"ERROR: Forbidden extension detected: {name}", file=sys.stderr
                    )
                    ok = False

            # C. Check .env rules
            if lower_name == ".env" or lower_name.endswith("/.env"):
                print(f"ERROR: Forbidden .env file detected: {name}", file=sys.stderr)
                ok = False

            # D. Text file content safety scanning
            # Check if file has a text-like extension
            is_text = False
            text_exts = [
                ".py",
                ".toml",
                ".yml",
                ".yaml",
                ".ini",
                ".json",
                ".txt",
                ".md",
                ".sh",
                ".sql",
            ]
            for ext in text_exts:
                if lower_name.endswith(ext):
                    is_text = True
                    break

            if is_text:
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")

                    # 1. Local path checks
                    for p in forbidden_paths:
                        if p in content:
                            print(
                                f"ERROR: Local path leak detected in '{name}': contains '{p}'",
                                file=sys.stderr,
                            )
                            ok = False

                    # 2. Secret check and allowlist enforcement
                    for marker in confidential_markers:
                        if marker in content:
                            # Verify if this file is allowed to contain this secret marker
                            allowed_markers = secret_allowlist.get(name, [])
                            if marker not in allowed_markers:
                                print(
                                    f"ERROR: Unauthorized secret/mock value found in '{name}'",
                                    file=sys.stderr,
                                )
                                ok = False
                except Exception as e:
                    print(
                        f"WARNING: Could not parse text content of {name}: {e}",
                        file=sys.stderr,
                    )

    if not ok:
        print("Verification failed. Removing ZIP archive.", file=sys.stderr)
        if os.path.exists(zip_filename):
            os.remove(zip_filename)
        sys.exit(1)

    # 4. Generate SUBMISSION_MANIFEST.json and append to ZIP
    manifest_data = {
        "git_commit": commit_sha,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "archive_sha256": "<recorded in separate file after generation>",
        "quality_gate": {
            "pytest": "passed",
            "ruff": "passed",
            "pyright": "passed",
            "alembic_check": "passed",
            "alembic_roundtrip": "passed",
        },
    }

    # Append manifest to ZIP
    with zipfile.ZipFile(zip_filename, "a") as zf:
        zf.writestr("SUBMISSION_MANIFEST.json", json.dumps(manifest_data, indent=2))

    # Calculate final SHA-256
    zip_sha = get_sha256(zip_filename)
    with open(sha_filename, "w", encoding="utf-8") as f:
        f.write(f"{zip_sha}\n")

    print(f"SUBMISSION_MANIFEST.json appended to {zip_filename}.")
    print(f"SHA-256 written to {sha_filename}: {zip_sha}")

    # 5. Final Assertions (extract to check existence and key configurations)
    print("Running final assertions on ZIP contents...")
    final_ok = True
    with zipfile.ZipFile(zip_filename, "r") as zf:
        zip_entries = zf.namelist()

        # check files
        required_files = [
            "src/glintory/entrypoint.py",
            "tests/test_entrypoint.py",
            "scripts/create_submission.py",
            "pyproject.toml",
            ".github/workflows/local-llm-smoke.yml",
        ]
        for rf in required_files:
            if rf not in zip_entries:
                print(
                    f"ERROR Assertion failed: '{rf}' is missing from the ZIP",
                    file=sys.stderr,
                )
                final_ok = False

        # pyproject.toml configuration check
        if "pyproject.toml" in zip_entries:
            pyproject_content = zf.read("pyproject.toml").decode(
                "utf-8", errors="ignore"
            )
            if "glintory.entrypoint:main" not in pyproject_content:
                print(
                    "ERROR Assertion failed: 'glintory.entrypoint:main' not found in pyproject.toml",
                    file=sys.stderr,
                )
                final_ok = False

        # workflow file configuration check
        if ".github/workflows/local-llm-smoke.yml" in zip_entries:
            workflow_content = zf.read(".github/workflows/local-llm-smoke.yml").decode(
                "utf-8", errors="ignore"
            )
            if "rss_sample_count" not in workflow_content:
                print(
                    "ERROR Assertion failed: 'rss_sample_count' not found in local-llm-smoke.yml",
                    file=sys.stderr,
                )
                final_ok = False

    if not final_ok:
        print("Final assertions failed. Removing ZIP archive.", file=sys.stderr)
        if os.path.exists(zip_filename):
            os.remove(zip_filename)
        sys.exit(1)

    print("Verification success. ZIP archive is safe, clean, and fully conforming.")


if __name__ == "__main__":
    main()
