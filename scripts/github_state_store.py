#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

# Import glintory functions safely
# As it is packaged, we can import from glintory
try:
    from alembic import command
    from alembic.config import Config

    from glintory.bootstrap import bootstrap
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

    from glintory.bootstrap import bootstrap
    from glintory.infrastructure.database import reset_db_connections
    from glintory.services.state_management import (
        create_state_snapshot,
        restore_state_archive,
        verify_state_archive,
    )


# Regular expression for state assets
ASSET_PATTERN = re.compile(r"^glintory-state-(\d+)-(\d+)\.tar\.gz$")


class GitHubAPIError(Exception):
    pass


class GitHubClient:
    """Wrapper around GitHub CLI (gh) to interact with Releases API.
    Can be easily mocked in unit tests.
    """

    def run_gh(self, args: list[str]) -> str:
        cmd = ["gh"] + args
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout
        except subprocess.CalledProcessError as e:
            err_msg = f"gh command failed: {e.stderr or e.stdout}"
            raise GitHubAPIError(err_msg) from e

    def get_release_assets(self, tag: str) -> list[dict]:
        """Fetch assets for the release associated with the given tag.
        Returns sorted assets by created_at DESC, id DESC.
        """
        # Returns JSON representing the release info
        try:
            out = self.run_gh(["api", f"repos/:owner/:repo/releases/tags/{tag}"])
            data = json.loads(out)
            assets = data.get("assets", [])

            # Format and parse dates for correct sorting
            for asset in assets:
                # API dates are typically ISO 8601: "2026-07-11T12:00:00Z"
                created_at_str = asset.get("created_at", "")
                try:
                    # Parse to datetime for reliable DESC sorting
                    asset["parsed_created_at"] = datetime.fromisoformat(
                        created_at_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    asset["parsed_created_at"] = datetime.min

            # Sort by parsed_created_at DESC, id DESC
            assets.sort(
                key=lambda x: (x["parsed_created_at"], x.get("id", 0)), reverse=True
            )
            return assets
        except GitHubAPIError as e:
            # If release doesn't exist, gh api returns 404
            if "404" in str(e):
                return []
            raise

    def create_release_if_not_exists(self, tag: str) -> None:
        """Create the managed state release if it does not exist."""
        try:
            self.run_gh(["release", "view", tag])
        except GitHubAPIError:
            # Release doesn't exist, create it as a prerelease (latest=false)
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
                ]
            )

    def upload_asset(self, tag: str, file_path: str) -> None:
        """Upload an asset to the specified release.
        Clobber is NOT allowed.
        """
        self.run_gh(["release", "upload", tag, file_path])

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
            ]
        )
        return os.path.join(output_dir, name)

    def delete_asset(self, asset_id: int) -> None:
        """Delete an asset from the release by its ID."""
        self.run_gh(
            ["api", "-X", "DELETE", f"repos/:owner/:repo/releases/assets/{asset_id}"]
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
    try:
        assets = client.get_release_assets("glintory-state")
    except Exception as e:
        print(f"Error checking release assets: {e}", file=sys.stderr)
        raise SystemExit(1)

    # Filter by pattern: glintory-state-{run_id}-{run_attempt}.tar.gz
    valid_assets = []
    for asset in assets:
        name = asset.get("name", "")
        if ASSET_PATTERN.match(name):
            valid_assets.append(asset)

    # Sort was already performed in DESC order inside get_release_assets
    if not valid_assets:
        # Check if release exists or not
        # If release tag exists but has 0 valid assets, or release tag does not exist
        # we can initialize a new empty database
        print("No valid state assets found. Initializing empty database...")
        # Clean up database if exists to ensure clean start
        if os.path.exists(db_path):
            os.remove(db_path)
        # Alembic migration
        bootstrap()
        reset_db_connections()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        alembic_cfg = Config(os.path.join(project_root, "alembic.ini"))
        command.upgrade(alembic_cfg, "head")
        print("Database initialized successfully.")
        return

    # Select exactly the latest 1 asset
    latest_asset = valid_assets[0]
    asset_name = latest_asset["name"]
    print(f"Latest asset selected: {asset_name} (ID: {latest_asset['id']})")

    # Download to state_dir
    try:
        downloaded_path = client.download_asset("glintory-state", asset_name, state_dir)
    except Exception as e:
        print(f"Failed to download asset {asset_name}: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Verifying downloaded archive: {downloaded_path}")
    try:
        # Verify the archive
        verify_state_archive(downloaded_path)
        # Restore the database
        restore_state_archive(downloaded_path, db_path, force=True)
        print("Database restored successfully.")
    except Exception as e:
        print(f"Verification or Restore failed for {asset_name}: {e}", file=sys.stderr)
        raise SystemExit(1)


def handle_upload_and_verify(
    client: GitHubClient, state_dir: str, db_url: str, metadata_file: str | None
) -> None:
    os.makedirs(state_dir, exist_ok=True)

    run_id = os.environ.get("GITHUB_RUN_ID")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    if not run_id or not run_attempt:
        print(
            "Error: GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT environment variables must be set.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Target filename format: glintory-state-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}.tar.gz
    archive_name = f"glintory-state-{run_id}-{run_attempt}.tar.gz"
    local_archive_path = os.path.join(state_dir, archive_name)

    # 1. Create Local Snapshot
    print(f"Creating local state snapshot: {local_archive_path}")
    try:
        # Close active connections first to ensure clean snapshot
        reset_db_connections()
        create_state_snapshot(
            output_path=local_archive_path,
            run_id=run_id,
            run_attempt=run_attempt,
            metadata_file=metadata_file,
            profile="public",
        )
    except Exception as e:
        print(f"Snapshot creation failed: {e}", file=sys.stderr)
        raise SystemExit(1)

    # 2. Local Verify
    print("Verifying local archive...")
    try:
        verify_state_archive(local_archive_path)
    except Exception as e:
        print(f"Local verification failed: {e}", file=sys.stderr)
        if os.path.exists(local_archive_path):
            os.remove(local_archive_path)
        raise SystemExit(1)

    # Calculate SHA-256 of local archive
    local_sha = compute_sha256(local_archive_path)

    # Ensure release exists
    print("Ensuring target release exists...")
    try:
        client.create_release_if_not_exists("glintory-state")
    except Exception as e:
        print(f"Failed to create/verify release tag: {e}", file=sys.stderr)
        raise SystemExit(1)

    # 3. Upload to Release (no clobber)
    print(f"Uploading archive to release: {archive_name}")
    try:
        client.upload_asset("glintory-state", local_archive_path)
    except Exception as e:
        print(f"Upload failed: {e}", file=sys.stderr)
        raise SystemExit(1)

    # 4. Download Uploaded Asset to Temporary Directory
    with tempfile.TemporaryDirectory() as tmp_download_dir:
        print("Downloading uploaded asset for double verification...")
        try:
            downloaded_path = client.download_asset(
                "glintory-state", archive_name, tmp_download_dir
            )
        except Exception as e:
            print(f"Verification Download failed: {e}", file=sys.stderr)
            raise SystemExit(1)

        # 5. Verify the re-downloaded archive
        print("Verifying downloaded archive structure and metadata...")
        try:
            downloaded_manifest = verify_state_archive(downloaded_path)
        except Exception as e:
            print(f"Verification of uploaded asset failed: {e}", file=sys.stderr)
            raise SystemExit(1)

        # 6. Verify Manifest fields match
        if (
            downloaded_manifest.get("github_run_id") != run_id
            or downloaded_manifest.get("github_run_attempt") != run_attempt
        ):
            print(
                f"Manifest Run ID or Attempt mismatch! expected={run_id}/{run_attempt}, actual={downloaded_manifest.get('github_run_id')}/{downloaded_manifest.get('github_run_attempt')}",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # 7. Compare SHA-256 hashes of local and downloaded archives
        downloaded_sha = compute_sha256(downloaded_path)
        if downloaded_sha != local_sha:
            print(
                "SHA-256 mismatch between local and uploaded archive!", file=sys.stderr
            )
            raise SystemExit(1)

    print("Double verification succeeded. State uploaded securely.")

    # 8. Prune old assets (keep latest 5 generations)
    handle_prune(client)


def handle_prune(client: GitHubClient) -> None:
    print("Pruning old state assets, keeping latest 5...")
    try:
        assets = client.get_release_assets("glintory-state")
    except Exception as e:
        print(f"Failed to fetch assets for pruning: {e}", file=sys.stderr)
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
            asset_name = asset["name"]
            asset_id = asset["id"]
            print(f"Deleting old state asset: {asset_name} (ID: {asset_id})")
            try:
                client.delete_asset(asset_id)
            except Exception as e:
                print(
                    f"Warning: Failed to delete asset {asset_name}: {e}",
                    file=sys.stderr,
                )


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

    if args.command == "download-latest":
        handle_download_latest(client, args.state_dir, db_url)
    elif args.command == "upload-and-verify":
        handle_upload_and_verify(client, args.state_dir, db_url, args.metadata_file)
    elif args.command == "prune":
        handle_prune(client)


if __name__ == "__main__":
    main()
