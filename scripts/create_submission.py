import os
import subprocess
import sys
import zipfile


def main() -> None:
    zip_filename = "submission-glintory.zip"
    print(f"Creating {zip_filename} using git archive...")

    # Create submission ZIP using git archive
    try:
        subprocess.run(["git", "archive", "-o", zip_filename, "HEAD"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Git archive failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("Verifying the generated zip archive entries...")

    # Forbidden terms for directory/file paths
    forbidden_terms = [
        "__pycache__",
        ".pyc",
        ".pyo",
        ".sqlite3",
        "invalid/",
        "/Users/",
        "TOKEN_SECRET_12345",
        "private-secret-db.sqlite3",
    ]

    ok = True
    with zipfile.ZipFile(zip_filename, "r") as zf:
        for name in zf.namelist():
            lower_name = name.lower()

            # 1. Exact or suffix match for .env to avoid blocking .env.example
            if lower_name == ".env" or lower_name.endswith("/.env"):
                print(
                    f"ERROR: Forbidden entry/path detected: {name} (matched '.env')",
                    file=sys.stderr,
                )
                ok = False

            # 2. Directory/File path matching forbidden patterns
            for term in forbidden_terms:
                if term.lower() in lower_name:
                    print(
                        f"ERROR: Forbidden entry/path detected: {name} (matched '{term}')",
                        file=sys.stderr,
                    )
                    ok = False

            # 3. Strict checks on databases, model files, and cache directories
            if (
                lower_name.endswith(".db")
                or lower_name.endswith(".sqlite")
                or lower_name.endswith(".gguf")
            ):
                print(
                    f"ERROR: Database/Model extension detected: {name}",
                    file=sys.stderr,
                )
                ok = False

            if "llama-server" in lower_name:
                print(
                    f"ERROR: llama-server executable file path detected: {name}",
                    file=sys.stderr,
                )
                ok = False

    if not ok:
        print(
            "Verification failed. The generated ZIP contains forbidden files or directories. Deleting the invalid ZIP.",
            file=sys.stderr,
        )
        if os.path.exists(zip_filename):
            os.remove(zip_filename)
        sys.exit(1)

    print("Verification success. ZIP archive is safe and clean.")


if __name__ == "__main__":
    main()
