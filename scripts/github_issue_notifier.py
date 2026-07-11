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


class NotificationError(Exception):
    pass


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
    except Exception as e:
        sys.stderr.write("FAILURE_ISSUE_LOOKUP_FAILED\n")
        raise NotificationError("FAILURE_ISSUE_LOOKUP_FAILED") from e


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
    except Exception as e:
        sys.stderr.write("FAILURE_LABEL_SETUP_FAILED\n")
        raise NotificationError("FAILURE_LABEL_SETUP_FAILED") from e


def handle_success(
    automation_result: str, deploy_pages_result: str, collection_status: str
) -> None:
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
        f"- **Collection Status:** {collection_status}\n"
        f"- **UTC Timestamp:** {datetime.now(UTC).isoformat()}\n"
    )

    for issue in matching:
        issue_num = issue["number"]
        print(f"Adding recovery comment and closing issue #{issue_num}...")
        run_gh(["issue", "comment", str(issue_num), "--body", body])
        run_gh(["issue", "close", str(issue_num)])


def handle_failure(
    automation_result: str, deploy_pages_result: str, collection_status: str
) -> None:
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
        f"- **Collection Status:** {collection_status}\n"
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


def write_step_summary(args) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    prune_res = "N/A"
    if args.prune_status:
        prune_res = f"{args.prune_status} (Deleted: {args.pruned_deleted_count or 0})"

    lines = [
        "## Glintory Execution Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| **Run ID** | {run_id} |",
        f"| **Run Attempt** | {run_attempt} |",
        f"| **Started At** | {args.started_at or 'N/A'} |",
        f"| **Completed At** | {completed_at} |",
        f"| **Restored Asset Name** | {args.restored_asset_name or 'N/A'} |",
        f"| **Restored Asset ID** | {args.restored_asset_id or 'N/A'} |",
        f"| **Collection Status** | {args.collection_status or 'N/A'} |",
        f"| **Due Count** | {args.due_count or 0} |",
        f"| **Succeeded Count** | {args.succeeded_count or 0} |",
        f"| **Partial Count** | {args.partial_count or 0} |",
        f"| **Failed Count** | {args.failed_count or 0} |",
        f"| **New State Asset Name** | {args.uploaded_asset_name or 'N/A'} |",
        f"| **New State Asset ID** | {args.uploaded_asset_id or 'N/A'} |",
        f"| **Prune Result** | {prune_res} |",
        f"| **Database Size** | {args.database_size or 'N/A'} |",
        f"| **Source Count** | {args.source_count or 0} |",
        f"| **Signal Count** | {args.signal_count or 0} |",
        f"| **Opportunity Count** | {args.opportunity_count or 0} |",
        f"| **Pages Deployment Result** | {args.pages_deployment_result or 'N/A'} |",
        f"| **Pages URL** | {args.pages_url or 'N/A'} |",
        "",
    ]

    if args.collection_status == "partial":
        lines.extend(
            [
                "### :warning: Warning: Partial Collection Success",
                "Some schedules failed to collect. Check the logs for details.",
                "",
            ]
        )

    with open(summary_path, "a") as f:
        f.write("\n".join(lines))


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
    parser.add_argument(
        "--collection-status",
        default="success",
        help="Operational status of the collection run",
    )
    parser.add_argument("--started-at", default="")
    parser.add_argument("--restored-asset-name", default="")
    parser.add_argument("--restored-asset-id", default="")
    parser.add_argument("--uploaded-asset-name", default="")
    parser.add_argument("--uploaded-asset-id", default="")
    parser.add_argument("--pruned-deleted-count", type=int, default=0)
    parser.add_argument("--prune-status", default="")
    parser.add_argument("--database-size", default="")
    parser.add_argument("--source-count", type=int, default=0)
    parser.add_argument("--signal-count", type=int, default=0)
    parser.add_argument("--opportunity-count", type=int, default=0)
    parser.add_argument("--pages-deployment-result", default="")
    parser.add_argument("--pages-url", default="")
    parser.add_argument("--due-count", type=int, default=0)
    parser.add_argument("--succeeded-count", type=int, default=0)
    parser.add_argument("--partial-count", type=int, default=0)
    parser.add_argument("--failed-count", type=int, default=0)

    args = parser.parse_args()

    # Ensure GITHUB_TOKEN is available in env (needed by gh CLI)
    if not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN"):
        print(
            "Warning: GITHUB_TOKEN or GH_TOKEN not set in environment.", file=sys.stderr
        )

    try:
        # Idempotently ensure the label exists before anything else
        ensure_label_exists()

        # Success conditions
        is_success = (
            args.automation_result == "success"
            and args.deploy_pages_result == "success"
            and args.collection_status == "success"
        )

        # Failure conditions
        is_failure = (
            args.automation_result != "success"
            or args.deploy_pages_result != "success"
            or args.collection_status
            in ("failed", "lease_lost", "infrastructure_failed")
        )

        if is_failure:
            handle_failure(
                args.automation_result, args.deploy_pages_result, args.collection_status
            )
        elif is_success:
            handle_success(
                args.automation_result, args.deploy_pages_result, args.collection_status
            )
        else:
            print(
                f"Partial or non-failure condition. No issue state changes. Status: {args.collection_status}"
            )

        # Always write Actions summary
        write_step_summary(args)

    except NotificationError:
        sys.exit(1)
    except Exception:
        sys.stderr.write("NOTIFICATION_FAILED\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
