#!/usr/bin/env python3
import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime

# Import glintory functions safely
# As it is packaged, we can import from glintory
try:
    from alembic import command
    from alembic.config import Config

    from glintory.infrastructure.database import reset_db_connections
    from glintory.services.state_management import (
        create_state_snapshot,
        restore_state_archive,
        verify_state_archive,
    )
except ImportError:
    # Fallback in case of local execution paths
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from alembic import command
    from alembic.config import Config

    from glintory.infrastructure.database import reset_db_connections
    from glintory.services.state_management import (
        create_state_snapshot,
        restore_state_archive,
        verify_state_archive,
    )


# Regular expression for state assets
ASSET_PATTERN = re.compile(r"^glintory-state-(\d+)-(\d+)\.tar\.gz$")


class GitHubAPIError(Exception):
    def __init__(
        self,
        stage_code: str,
        *,
        return_code: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(stage_code)
        self.stage_code = stage_code
        self.return_code = return_code
        self.http_status = http_status


class GitHubReleaseNotFoundError(GitHubAPIError):
    pass


class GitHubClient:
    """Wrapper around GitHub CLI (gh) to interact with Releases API.
    Can be easily mocked in unit tests.
    """

    def run_gh(self, args: list[str], stage_code: str = "GITHUB_API_ERROR") -> str:
        cmd = ["gh"] + args
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout
        except subprocess.CalledProcessError as e:
            stderr = e.stderr or ""
            return_code = e.returncode
            http_status = None

            # Extract HTTP status
            match = re.search(r"\b(404|401|403|429|5\d{2})\b", stderr)
            if match:
                http_status = int(match.group(1))

            if http_status == 404:
                raise GitHubReleaseNotFoundError(
                    stage_code,
                    return_code=return_code,
                    http_status=http_status,
                ) from None
            raise GitHubAPIError(
                stage_code,
                return_code=return_code,
                http_status=http_status,
            ) from None

    def get_release_assets(self, tag: str) -> list[dict]:
        """Fetch assets for the release associated with the given tag.
        Returns sorted assets by created_at DESC, id DESC.
        """
        try:
            out = self.run_gh(
                ["api", f"repos/:owner/:repo/releases/tags/{tag}"],
                stage_code="STATE_DOWNLOAD_FAILED",
            )
            data = json.loads(out)
            assets = data.get("assets", [])

            # Format and parse dates for correct sorting
            for asset in assets:
                created_at_str = asset.get("created_at") or ""
                try:
                    clean_date = created_at_str.replace("Z", "+00:00")
                    parsed = datetime.fromisoformat(clean_date)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    else:
                        parsed = parsed.astimezone(UTC)
                    asset["parsed_created_at"] = parsed
                except Exception:
                    asset["parsed_created_at"] = datetime.min.replace(tzinfo=UTC)

                try:
                    asset["normalized_id"] = int(asset.get("id", 0))
                except Exception:
                    asset["normalized_id"] = 0

            # Sort by parsed_created_at DESC, normalized_id DESC
            assets.sort(
                key=lambda x: (x["parsed_created_at"], x.get("normalized_id", 0)),
                reverse=True,
            )
            return assets
        except GitHubReleaseNotFoundError:
            raise
        except GitHubAPIError:
            raise

    def create_release_if_not_exists(self, tag: str) -> None:
        """Create the managed state release if it does not exist."""
        try:
            out = self.run_gh(
                ["release", "view", tag, "--json", "tagName,isPrerelease,isDraft"],
                stage_code="STATE_UPLOAD_FAILED",
            )
            data = json.loads(out)
            tag_name = data.get("tagName")
            is_prerelease = data.get("isPrerelease")
            is_draft = data.get("isDraft")

            if tag_name != tag or is_prerelease is not True or is_draft is not False:
                sys.stderr.write("INVALID_RELEASE_CONFIGURATION\n")
                raise SystemExit(1)
            return
        except GitHubReleaseNotFoundError:
            # Release doesn't exist, create it as a prerelease (latest=false)
            try:
                self.run_gh(
                    [
                        "release",
                        "create",
                        tag,
                        "--title",
                        "Glintory State — Machine Managed",
                        "--notes",
                        "Machine-managed public Glintory state.\nDo not delete or enable immutable release protection for this release.",
                        "--prerelease",
                    ],
                    stage_code="STATE_UPLOAD_FAILED",
                )
            except Exception as e:
                raise GitHubAPIError("STATE_UPLOAD_FAILED") from e

    def upload_asset(self, tag: str, file_path: str) -> dict:
        """Upload an asset to the specified release.
        Clobber is NOT allowed.
        """
        self.run_gh(
            ["release", "upload", tag, file_path], stage_code="STATE_UPLOAD_FAILED"
        )
        filename = os.path.basename(file_path)

        # Exponential backoff retry (up to 3 times) to fetch the uploaded asset details
        import time

        delay = 1.0
        for _ in range(3):
            try:
                assets = self.get_release_assets(tag)
                for asset in assets:
                    if asset.get("name") == filename:
                        return asset
            except Exception:
                pass
            time.sleep(delay)
            delay *= 2

        # Cleanup if confirmation fails
        with contextlib.suppress(Exception):
            self.run_gh(
                ["release", "delete-asset", tag, filename, "-y"],
                stage_code="STATE_UPLOAD_FAILED",
            )
        raise GitHubAPIError("STATE_UPLOAD_CONFIRMATION_FAILED")

    def download_asset(self, tag: str, name: str, output_dir: str) -> str:
        """Download a specific asset by name from the release."""
        self.run_gh(
            [
                "release",
                "download",
                tag,
                "--pattern",
                name,
                "--dir",
                output_dir,
            ],
            stage_code="STATE_DOWNLOAD_FAILED",
        )
        return os.path.join(output_dir, name)

    def delete_asset(self, asset_id: int) -> None:
        """Delete an asset from the release by its ID."""
        self.run_gh(
            ["api", "-X", "DELETE", f"repos/:owner/:repo/releases/assets/{asset_id}"],
            stage_code="STATE_UPLOAD_FAILED",
        )


def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def handle_download_latest(client: GitHubClient, state_dir: str, db_url: str) -> None:
    os.makedirs(state_dir, exist_ok=True)
    db_path = db_url[10:] if db_url.startswith("sqlite:///") else db_url

    print("Checking for latest state asset...")
    release_not_found = False
    valid_assets = []

    try:
        assets = client.get_release_assets("glintory-state")
        # Filter by pattern: glintory-state-{run_id}-{run_attempt}.tar.gz
        for asset in assets:
            name = asset.get("name", "")
            if ASSET_PATTERN.match(name):
                valid_assets.append(asset)
    except GitHubReleaseNotFoundError:
        release_not_found = True
    except GitHubAPIError:
        sys.stderr.write("STATE_DOWNLOAD_FAILED\n")
        raise SystemExit(1)
    except Exception:
        sys.stderr.write("STATE_DOWNLOAD_FAILED\n")
        raise SystemExit(1)

    # First-run condition: ReleaseNotFound or 0 valid assets
    if release_not_found or not valid_assets:
        print(
            "No valid state assets found (or release not found). Initializing empty database..."
        )
        db_parent = os.path.dirname(os.path.abspath(db_path))
        if db_parent:
            os.makedirs(db_parent, exist_ok=True)

        if os.path.exists(db_path):
            os.remove(db_path)

        # Create empty DB file
        with open(db_path, "w"):
            pass

        try:
            reset_db_connections()
            project_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..")
            )
            alembic_cfg = Config(os.path.join(project_root, "alembic.ini"))
            command.upgrade(alembic_cfg, "head")

            # Verify alembic current works
            command.current(alembic_cfg)

            # SQLite integrity check
            conn = sqlite3.connect(db_path)
            try:
                res = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if res != "ok":
                    raise ValueError(f"Integrity check failed: {res}")
            finally:
                conn.close()

            print("Database initialized successfully.")
            return
        except Exception:
            sys.stderr.write("STATE_RESTORE_FAILED\n")
            raise SystemExit(1)

    latest_asset = valid_assets[0]
    asset_name = latest_asset["name"]
    print(f"Latest asset selected (Asset ID: {latest_asset['id']})")

    try:
        downloaded_path = client.download_asset("glintory-state", asset_name, state_dir)
    except Exception:
        sys.stderr.write("STATE_DOWNLOAD_FAILED\n")
        raise SystemExit(1)

    print("Verifying downloaded archive...")
    try:
        verify_state_archive(downloaded_path)
        restore_state_archive(downloaded_path, db_path, force=True)
        print("Database restored successfully.")
    except Exception:
        sys.stderr.write("STATE_VERIFY_FAILED\n")
        raise SystemExit(1)


def handle_upload_and_verify(
    client: GitHubClient, state_dir: str, db_url: str, metadata_file: str | None
) -> None:
    os.makedirs(state_dir, exist_ok=True)

    run_id = os.environ.get("GITHUB_RUN_ID")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    if not run_id or not run_attempt:
        sys.stderr.write("STATE_UPLOAD_FAILED\n")
        raise SystemExit(1)

    # Target filename format: glintory-state-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}.tar.gz
    archive_name = f"glintory-state-{run_id}-{run_attempt}.tar.gz"
    local_archive_path = os.path.join(state_dir, archive_name)

    # 1. Create Local Snapshot
    print("Creating local state snapshot...")
    try:
        # Close active connections first to ensure clean snapshot
        reset_db_connections()
        create_state_snapshot(
            output_path=local_archive_path,
            database_url=db_url,
            run_id=run_id,
            run_attempt=run_attempt,
            metadata_file=metadata_file,
            profile="public",
        )
    except Exception:
        sys.stderr.write("STATE_VERIFY_FAILED\n")
        raise SystemExit(1)

    # 2. Local Verify
    print("Verifying local archive...")
    try:
        verify_state_archive(local_archive_path)
    except Exception:
        sys.stderr.write("STATE_VERIFY_FAILED\n")
        if os.path.exists(local_archive_path):
            os.remove(local_archive_path)
        raise SystemExit(1)

    # Calculate SHA-256 of local archive
    local_sha = compute_sha256(local_archive_path)

    # Ensure release exists
    print("Ensuring target release exists...")
    try:
        client.create_release_if_not_exists("glintory-state")
    except Exception:
        sys.stderr.write("STATE_UPLOAD_FAILED\n")
        raise SystemExit(1)

    # 3. Upload to Release (no clobber)
    print("Uploading archive to release...")
    uploaded_asset = None
    try:
        uploaded_asset = client.upload_asset("glintory-state", local_archive_path)
    except Exception:
        sys.stderr.write("STATE_UPLOAD_FAILED\n")
        raise SystemExit(1)

    # 4. Download Uploaded Asset to Temporary Directory
    double_verification_failed = False
    try:
        with tempfile.TemporaryDirectory() as tmp_download_dir:
            print("Downloading uploaded asset for double verification...")
            downloaded_path = client.download_asset(
                "glintory-state", archive_name, tmp_download_dir
            )

            # 5. Verify the re-downloaded archive
            print("Verifying downloaded archive structure and metadata...")
            downloaded_manifest = verify_state_archive(downloaded_path)

            # 6. Verify Manifest fields match
            if (
                downloaded_manifest.get("github_run_id") != run_id
                or downloaded_manifest.get("github_run_attempt") != run_attempt
            ):
                raise ValueError("Run ID / Attempt mismatch")

            # 7. Compare SHA-256 hashes of local and downloaded archives
            downloaded_sha = compute_sha256(downloaded_path)
            if downloaded_sha != local_sha:
                raise ValueError("SHA-256 mismatch")
    except Exception:
        double_verification_failed = True

    if double_verification_failed:
        sys.stderr.write("STATE_POST_UPLOAD_VERIFY_FAILED\n")
        if uploaded_asset:
            try:
                client.delete_asset(uploaded_asset["id"])
            except Exception:
                sys.stderr.write("NEW_ASSET_CLEANUP_FAILED\n")
                raise SystemExit(1)
        raise SystemExit(1)

    print("Double verification succeeded. State uploaded securely.")

    # 8. Prune old assets (keep latest 5 generations)
    handle_prune(client)


def handle_prune(client: GitHubClient) -> None:
    print("Pruning old state assets, keeping latest 5...")
    try:
        assets = client.get_release_assets("glintory-state")
    except Exception:
        sys.stderr.write("STATE_PRUNE_FAILED\n")
        raise SystemExit(1)

    valid_assets = []
    for asset in assets:
        name = asset.get("name", "")
        if ASSET_PATTERN.match(name):
            valid_assets.append(asset)

    # valid_assets is sorted by created_at DESC, id DESC
    if len(valid_assets) > 5:
        to_delete = valid_assets[5:]
        for asset in to_delete:
            asset_id = asset["id"]
            print(f"Deleting old state asset (Asset ID: {asset_id})")
            try:
                client.delete_asset(asset_id)
            except Exception:
                sys.stderr.write(
                    f"STATE_PRUNE_FAILED: Could not delete asset ID {asset_id}\n"
                )
                raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Manage Glintory durable state assets in GitHub Releases."
    )
    parser.add_argument(
        "--state-dir",
        default=".state",
        help="Directory to download/upload state bundles",
    )
    parser.add_argument(
        "--db-url",
        default="sqlite:///data/glintory.sqlite3",
        help="Database URL to snapshot or restore",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # download-latest
    subparsers.add_parser(
        "download-latest", help="Download and restore latest valid state asset."
    )

    # upload-and-verify
    upload_parser = subparsers.add_parser(
        "upload-and-verify", help="Upload local state and verify it."
    )
    upload_parser.add_argument(
        "--metadata-file",
        help="Path to metadata json to bundle with snapshot",
    )

    # prune
    subparsers.add_parser("prune", help="Prune old state assets keeping latest 5.")

    args = parser.parse_args()
    client = GitHubClient()

    # Overwrite db_url from env if present
    db_url = os.environ.get("GLINTORY_DATABASE_URL", args.db_url)

    try:
        if args.command == "download-latest":
            handle_download_latest(client, args.state_dir, db_url)
        elif args.command == "upload-and-verify":
            handle_upload_and_verify(client, args.state_dir, db_url, args.metadata_file)
        elif args.command == "prune":
            handle_prune(client)
    except SystemExit as e:
        sys.exit(e.code)
    except Exception:
        sys.stderr.write("STATE_STORE_EXECUTION_FAILED\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
