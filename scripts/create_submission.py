import subprocess
import sys
from pathlib import Path

# Resolve path so we can import modules in this project
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from submission_pipeline import (  # noqa: E402
    SubmissionConfig,
    build_submission,
    run_quality_gates,
    verify_package,
)


def main(config: SubmissionConfig | None = None) -> None:
    if config is None:
        config = SubmissionConfig(PROJECT_ROOT)

    commit_before, this_script_sha, clean_before = build_submission(config)

    quality_gates = run_quality_gates(config)

    print("Checking Git status after build...")
    try:
        commit_after = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        status_after_raw = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
        clean_after = len(status_after_raw) == 0
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to run Git post-build check: {e}", file=sys.stderr)
        from submission_pipeline import cleanup_failed_submission

        cleanup_failed_submission(config)
        sys.exit(1)

    if commit_before != commit_after:
        print(
            f"ERROR: Git HEAD changed during build execution. Before: {commit_before}, After: {commit_after}",
            file=sys.stderr,
        )
        from submission_pipeline import cleanup_failed_submission

        cleanup_failed_submission(config)
        sys.exit(1)

    if not clean_after:
        print(
            f"ERROR: Working tree became dirty during build execution:\n{status_after_raw}",
            file=sys.stderr,
        )
        from submission_pipeline import cleanup_failed_submission

        cleanup_failed_submission(config)
        sys.exit(1)

    verify_package(
        config,
        commit_before,
        commit_after,
        clean_before,
        clean_after,
        this_script_sha,
        quality_gates,
    )


if __name__ == "__main__":
    main()
