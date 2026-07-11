#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime


class GitHubAPIError(Exception):
    pass


def run_gh(args: list[str]) -> str:
    cmd = ["gh"] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return res.stdout
    except subprocess.CalledProcessError:
        raise GitHubAPIError("gh command execution failed") from None


def get_open_failure_issues() -> list[dict]:
    try:
        out = run_gh(
            [
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                "automation-failure",
                "--json",
                "number,title",
            ]
        )
        if not out.strip():
            return []
        return json.loads(out)
    except Exception:
        return []


def ensure_label_exists() -> None:
    print("Ensuring automation-failure label exists...")
    try:
        run_gh(
            [
                "label",
                "create",
                "automation-failure",
                "--description",
                "Glintory scheduled automation failures",
                "--color",
                "B60205",
                "--force",
            ]
        )
    except Exception:
        sys.stderr.write("LABEL_CREATION_FAILED\n")


def handle_success(automation_result: str, deploy_pages_result: str) -> None:
    issues = get_open_failure_issues()
    target_title = "[Glintory Automation] Failure"
    matching = [i for i in issues if i.get("title") == target_title]

    if not matching:
        print("No open failure issues to recover. Doing nothing.")
        return

    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    repo = os.environ.get("GITHUB_REPOSITORY", "unknown")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

    body = (
        f"### Glintory Automation Recovery\n\n"
        f"The automation run completed successfully, and the system has recovered.\n\n"
        f"- **Run ID:** {run_id}\n"
        f"- **Attempt:** {run_attempt}\n"
        f"- **Run URL:** {run_url}\n"
        f"- **Automation Job Result:** {automation_result}\n"
        f"- **Deploy Pages Job Result:** {deploy_pages_result}\n"
        f"- **UTC Timestamp:** {datetime.now(UTC).isoformat()}\n"
    )

    for issue in matching:
        issue_num = issue["number"]
        print(f"Adding recovery comment and closing issue #{issue_num}...")
        run_gh(["issue", "comment", str(issue_num), "--body", body])
        run_gh(["issue", "close", str(issue_num)])


def handle_failure(automation_result: str, deploy_pages_result: str) -> None:
    issues = get_open_failure_issues()
    target_title = "[Glintory Automation] Failure"
    matching = [i for i in issues if i.get("title") == target_title]

    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    repo = os.environ.get("GITHUB_REPOSITORY", "unknown")
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

    body = (
        f"### Glintory Automation Failed\n\n"
        f"A scheduled Glintory automation execution has failed. Please check the logs in GitHub Actions.\n\n"
        f"- **Run ID:** {run_id}\n"
        f"- **Attempt:** {run_attempt}\n"
        f"- **Run URL:** {run_url}\n"
        f"- **Automation Job Result:** {automation_result}\n"
        f"- **Deploy Pages Job Result:** {deploy_pages_result}\n"
        f"- **UTC Timestamp:** {datetime.now(UTC).isoformat()}\n\n"
        f"#### Instructions for Recovery:\n"
        f"1. Navigate to the Run URL above.\n"
        f"2. Inspect the failed job logs for any database integrity, network collection, or validation audit failures.\n"
        f"3. Address the root cause and trigger the workflow again using `workflow_dispatch` manually."
    )

    if matching:
        issue_num = matching[0]["number"]
        print(f"Adding comment to existing open failure issue #{issue_num}...")
        run_gh(["issue", "comment", str(issue_num), "--body", body])
    else:
        print("Creating new failure issue...")
        run_gh(
            [
                "issue",
                "create",
                "--title",
                target_title,
                "--label",
                "automation-failure",
                "--body",
                body,
            ]
        )


def main():
    parser = argparse.ArgumentParser(
        description="Handle Glintory automation notifications."
    )
    parser.add_argument(
        "--automation-result",
        required=True,
        help="Result status of the automation job (e.g. success, failure, cancelled)",
    )
    parser.add_argument(
        "--deploy-pages-result",
        required=True,
        help="Result status of the deploy-pages job (e.g. success, failure, cancelled)",
    )
    args = parser.parse_args()

    # Ensure GITHUB_TOKEN is available in env (needed by gh CLI)
    if not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN"):
        print(
            "Warning: GITHUB_TOKEN or GH_TOKEN not set in environment.", file=sys.stderr
        )

    try:
        # Idempotently ensure the label exists before anything else
        ensure_label_exists()

        if args.automation_result == "success" and args.deploy_pages_result == "success":
            handle_success(args.automation_result, args.deploy_pages_result)
        else:
            handle_failure(args.automation_result, args.deploy_pages_result)
    except Exception:
        sys.stderr.write("NOTIFICATION_FAILED\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
