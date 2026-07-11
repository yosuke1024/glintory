# Glintory

Find the signals worth building on.

## GitHub-Native Scheduled Collection & Durable State Architecture

Glintory adopts a serverless architecture that uses GitHub Actions Scheduled Workflows as an external scheduler, eliminating the need for a constantly running background scheduler daemon. It starts at specified times, restores the database state, runs the pipelines, generates a static website, and shuts down.

### Pipeline Execution Order

The scheduled automation workflow executes steps in the following strict order:

1. **Preflight**: Validates that the public site URL uses the HTTPS schema and does not contain invalid queries or fragments using the same validation logic in Python.
2. **State Restore**: Downloads and extracts the previously saved SQLite state (archive) from the GitHub Release (`glintory-state`).
3. **Migration**: Updates the database schema to the latest version using Alembic.
4. **Manifest Sync**: Synchronizes the public input sources configurations (`public-sources.json`) into the database.
5. **Collection**: Fetches data from sources whose schedules have reached their due dates.
6. **Deterministic Opportunity Analysis**: Deduplicates, clusters, and links evidence from the collected signals.
7. **Deterministic Scoring**: Calculates opportunity scores and priority levels based on versioned deterministic rules.
8. **Local LLM Enrichment**: Temporarily spawns a local LLM (`llama-server`) on GitHub Actions to enrich and explain opportunities in both English and Japanese.
9. **Static Site Build**: Generates a responsive public static site from the latest database state using Jinja2 templates.
10. **State Snapshot**: Flushes the SQLite WAL checkpoint and archives the current state (SQLite database and manifests).
11. **Local Verify**: Validates archive integrity, filename rules, file sizes, and SHA-256 hashes locally.
12. **Release Upload**: Uploads the verified archive to the GitHub Release (clobbering is strictly forbidden).
13. **Post-upload Verify**: Re-downloads the uploaded asset and performs double-verification of the SHA-256 hash.
14. **Prune**: Automatically deletes old state archives, keeping only the latest 5 generations.
15. **Pages Artifact Upload**: Uploads the static site build directory as a GitHub Pages artifact.
16. **Pages Deploy**: Deploys the static site to GitHub Pages.
17. **Notify**: Controls and sends issue notifications based on the pipeline run's success or failure.

> [!WARNING]
> **Fail-Closed Protection on Build Failures**
> If the static site build step fails, the workflow aborts immediately. New states will not be uploaded, old state archives will not be pruned, and no site deployments will occur. The last known healthy database state is safely preserved.

### GitHub State Storage (`github_state_store.py`)

At the end of each workflow execution, the SQLite state database is compressed into `glintory-state-{GITHUB_RUN_ID}-{GITHUB_RUN_ATTEMPT}.tar.gz` and uploaded as a prerelease asset to the GitHub Release tagged `glintory-state`.

- **Latest State Resolution**:
  The store fetches release assets via the REST API (`repos/:owner/:repo/releases/tags/glintory-state`), filters for filenames matching `^glintory-state-[0-9]+-[0-9]+\.tar\.gz$`, and downloads the latest asset sorted by `created_at DESC` and `id DESC`.
- **Double Verification**:
  After uploading the new state archive, the workflow downloads it back to a temporary folder, extracts it, and verifies its file layout and SHA-256 hash against the local original. If verification fails, the database state is rolled back to the previous generation, no deployment happens, and the workflow fails.
- **5-Generation Pruning**:
  Upon successful verification, old state archives are pruned, leaving only the 5 most recent versions.
- **First-run Atomic DB Initialization**:
  If the database file does not exist (e.g., first execution), the script creates a temporary DB file (`.tmp`) first. It then runs Alembic migrations, checks SQLite integrity (`PRAGMA integrity_check`), and verifies essential tables. Once verified, it flushes files to disk via `fsync` and atomically renames the temporary file to the target database path using `os.replace`. If any step fails, the target database is left untouched, and the workflow exits with a `STATE_RESTORE_FAILED` error.
- **Operational Note**:
  The `glintory-state` tag and release are managed automatically. **Do not delete them manually, and do not enable immutable release protection for this tag.**

### Required Variables and GitHub Actions Configuration

To set up and run the automation, configure the following variables in your GitHub repository.

#### 1. Repository Variables
Navigate to repository `Settings -> Secrets and variables -> Actions -> Variables` and define:
- `GLINTORY_PUBLIC_SITE_URL`: The absolute base URL where your GitHub Pages site is hosted (e.g., `https://<username>.github.io/glintory`). This URL is used directly for generating absolute URLs in the Sitemap. Do not append a trailing slash.
- `GLINTORY_REPOSITORY_URL`: The absolute HTTPS URL of your repository, linked from the static site footer.
- `GLINTORY_PIXAPPS_URL` (Optional): The integration URL for PixApps. If defined, it is rendered on the public site.

#### 2. Workflow Permissions
Ensure that the workflow execution environment is granted write permissions: `contents: write`, `pages: write`, `id-token: write`.

#### 3. First Run (Manual Trigger)
To initialize a new repository, manually run the `Glintory Scheduled Automation` workflow via `workflow_dispatch` in the Actions tab. This creates the initial release and database state.

### Failure / Recovery Issues

If a scheduled execution fails, the workflow automatically opens a GitHub Issue titled `[Glintory Automation] Failure` and labels it `automation-failure`.

- **Notification and Closing Policies**:
  * **FAILED**: (Exit code non-zero, excluding 3 and 4): Creates a failure issue or comments on an existing open issue.
  * **PARTIAL**: (Exit code 3 - some sources failed but at least one succeeded): Deploys the static site and uploads the new state, but does not open a failure issue nor closes any existing open ones. Warnings are displayed in the Actions Summary.
  * **SUCCESS**: (All sources completed successfully): If an open failure issue exists, the workflow posts a recovery comment and automatically closes the issue.
- **Security & Privacy**:
  To protect system integrity, **database URLs, authentication credentials, API tokens, and raw exception stack traces are never exposed in issue descriptions or comments.**

### Actions Summary Report

Upon completion, the workflow generates a comprehensive report in the GitHub Actions Step Summary containing:
* Run ID & Run Attempt
* Start & End timestamps (UTC)
* Previous state archive name & ID
* Collection Status & Counts (Due, Succeeded, Partial, Failed)
* Newly uploaded state archive name & ID
* Pruning results (Deleted count, status)
* Database statistics (DB size, number of sources, signals, and opportunities)
* Pages deployment status & site URL

> [!NOTE]
> Detailed error messages, source configurations, HTTP response bodies, and API tokens are omitted from the summary report to prevent credential and detail leaks.

### Public State Safety Audit & Size Constraints

- **Public Safety Audit**:
  The SQLite database is scanned before archiving to ensure no sensitive details (e.g., API keys, environment variables, GitHub tokens, and private review notes/decisions) are exported.
  - If any matched secrets or invalid JSON configurations are detected, the workflow fails immediately (fail-closed) and aborts state upload.
- **Size Limits**:
  - Compressed state archive size: max 10MB
  - Extracted database file size: max 50MB
  - Metadata manifest size: max 1MB
  - Directories containing duplicate names, symlinks, sockets, or non-regular files are rejected.

---

## GitHub Collector

The `GitHubCollector` queries the GitHub REST API to gather public repository metadata and issue details.

### Features
- Collects public repository metadata matching search queries.
- Collects public issue metadata matching search queries.
- Normalizes raw payloads into a standardized `RawItem` format.

### Configuration & Authentication

While the GitHub API can be queried without authentication, anonymous requests are subject to strict rate limits (60 requests per hour).

* **On GitHub Actions**: You do **not** need to manually generate or register a Personal Access Token (PAT). The workflow file (`glintory-automation.yml`) automatically maps the temporary, short-lived `${{ secrets.GITHUB_TOKEN }}` generated for each run to `GLINTORY_GITHUB_TOKEN`. This is secure and requires no configuration.
* **On Local Environment**: If you run collection pipelines frequently during development or testing, you will hit rate limits quickly. In this case, it is recommended to create a personal PAT and configure it in your `.env` file.

#### 1. Environment Variables (Recommended for Local Development Only)
Add the following variables to your local `.env` file:

```env
# Optional: Personal Access Token for relaxing local rate limits
GLINTORY_GITHUB_TOKEN=your_personal_access_token
```


You can also override API endpoints and limits:

```env
GLINTORY_GITHUB_API_URL=https://api.github.com
GLINTORY_GITHUB_API_VERSION=2026-03-10
GLINTORY_GITHUB_EXCERPT_MAX_CHARS=2000
```

> [!WARNING]
> To prevent credential leaks, never include tokens directly in the Source configuration JSON. Tokens must be loaded exclusively from the environment.

#### 2. Source Configuration Example

Below is a JSON configuration example for the GitHub collector using standard GitHub Search query parameters:

```json
{
  "repository_queries": [
    {
      "query": "topic:self-hosted pushed:>2026-04-01",
      "sort": "updated",
      "order": "desc",
      "max_items": 20
    }
  ],
  "issue_queries": [
    {
      "query": "\"too expensive\" is:issue created:>2026-04-01",
      "sort": "created",
      "order": "desc",
      "max_items": 20
    }
  ],
  "per_page": 50,
  "max_pages_per_query": 2,
  "include_forks": false,
  "include_archived": false
}
```

### Manual Verification (Smoke Tests)

You can run standalone smoke tests to verify connectivity and persistence to local SQLite:

```bash
# Verify collector connectivity
GLINTORY_GITHUB_TOKEN=your_token uv run python scripts/smoke_github_collector.py

# Verify persistence and deduplication flow
GLINTORY_GITHUB_TOKEN=your_token uv run python scripts/smoke_github_persistence.py
```

---

## Hacker News Collector

The `HackerNewsCollector` fetches items from the official Hacker News Firebase API.

### Features
- **Official Firebase API**: Queries feeds (Ask HN, Show HN, Top Stories, New Stories, Best Stories, Job Stories) securely.
- **HTML Sanitization**: Strip tags and decodes HTML entities to clean text.
- **HN Discussion URL as Canonical**: Sets `https://news.ycombinator.com/item?id=<id>` as the canonical URL for signals, keeping external links in metadata.
- **Job Stories Filtering**: Controls job collection via `include_jobs`.
- **Deduplication**: HN Items are isolated per source and deduplicated upon ingestion.

### Configuration

Add the following parameters to your `.env` file to customize:

```env
GLINTORY_HN_API_URL=https://hacker-news.firebaseio.com/v0
GLINTORY_HN_WEB_ITEM_URL_TEMPLATE=https://news.ycombinator.com/item?id={item_id}
GLINTORY_HN_TEXT_MAX_CHARS=5000
```

#### Source Configuration Example

```json
{
  "feeds": [
    "ask",
    "show",
    "new"
  ],
  "max_items_per_feed": 25,
  "include_jobs": false,
  "include_dead": false,
  "include_deleted": false,
  "minimum_score": 2,
  "lookback_days": 90
}
```

### Standalone Smoke Tests

```bash
uv run python scripts/smoke_hackernews_persistence.py
```

---

## RSS / Atom Collector

The `RSSCollector` parses RSS 2.0, RSS 1.0, and Atom 1.0 feeds, storing entries in the SQLite database.
The technology community "Lobsters" (`https://lobste.rs/rss`) is registered as the default public RSS source. It is completely independent from Hacker News and GitHub, and is highly suitable for discovering developer demands, limitations of existing tools, and personal project launches.


### Features
- **Secure HTTP Client**: Uses the common HTTP client to fetch raw bytes securely, bypassing feedparser's built-in fetcher.
- **SSRF Defenses**: Prevents requests targeting loopbacks, private subnets, localhost, and invalid schemes. Redirect targets are checked sequentially.
- **Graceful Fallbacks (Non-strict Mode)**:
  - If XML parsing fails (Bozo exception), it recovers valid entries and marks the run status as `PARTIAL`.
  - Individual item failures (e.g., missing titles, invalid URLs) are skipped without failing the entire run.
- **Safe HTML Stripping**: Strip HTML tags from titles and summaries.
- **Metadata Whitelisting**: Limits allowed metadata properties (e.g., `feed_format`, `entry_tags`, `default_tags`, etc.).

### Configuration

#### Source Configuration Example

```json
{
  "feed_url": "https://example.com/feed.xml",
  "max_items": 20,
  "max_entries_to_scan": 100,
  "lookback_days": 90,
  "include_undated": true,
  "signal_type": "trend",
  "use_content_fallback": true,
  "strict_parsing": false,
  "default_categories": ["rss"],
  "default_tags": ["tech"]
}
```

### Parameters
- `feed_url` (Required): The URL of the RSS/Atom feed.
- `max_items`: Maximum number of signals to store from a single run (default: 100).
- `max_entries_to_scan`: Maximum number of entries to parse from the feed stream (default: 100).
- `lookback_days`: Maximum age (in days) of entries to ingest.
- `include_undated`: Allows ingesting entries lacking timestamps (default: `true`).
- `signal_type`: The `SignalType` mapped to entries (e.g., `trend`, `request`, `pain`, `launch`, `job_demand`).
- `use_content_fallback`: Uses `content` body if `summary` is empty (default: `true`).
- `strict_parsing`: Fails the run if bozo parser warnings are raised (default: `false`).

### Standalone Smoke Tests

```bash
uv run python scripts/smoke_rss_persistence.py --feed-url https://hnrss.org/frontpage
```

---

## Signal Normalization & Persistence

Collected raw items are normalized based on deterministic rules and stored.

### Features
- **Deterministic Rules**: Normalization, URL cleaning, signal classification, and freshness metrics are calculated via deterministic functions without calling AI.
- **Deduplication Constraints**:
  - Signals must have a unique `canonical_url` per `Source`.
  - Matching priority: `source_id + external_id` -> `source_id + canonical_url`.
- **Ingestion States**:
  - **Inserted**: New signal stored.
  - **Updated**: Existing signal updated (only if content has changed).
  - **Duplicate**: No content changes. Only timestamps and metadata fields are updated.
- **Metrics**: Captures runs statistics (`inserted_count`, `updated_count`, `duplicate_count`, etc.).

### Signal Type Classification

Signals are categorized into types based on properties:
- **GitHub Repository**: Always mapped to `SignalType.PROJECT`.
- **GitHub Issue**: Classified by labels and keywords:
  1. Labels matching `bug`, `regression`, `broken`, `defect` -> `SignalType.COMPLAINT`.
  2. Labels matching `feature`, `enhancement`, `proposal`, `request` -> `SignalType.REQUEST`.
  3. Context text containing pain-point keywords -> `SignalType.PAIN`.
  4. Default -> `SignalType.REQUEST`.
- **Hacker News**:
  - `hn_ask`: Mapped to `SignalType.PAIN` (if keywords match) or `SignalType.REQUEST`.
  - `hn_show`: Mapped to `SignalType.LAUNCH`.
  - `hn_story`: Mapped to `SignalType.TREND`.
  - `hn_job`: Mapped to `SignalType.JOB_DEMAND`.
- **Unsupported**: Pull requests, discussions, and unknown types are ignored.

### Schema Migrations

Manage database schemas using Alembic:

```bash
# Upgrade database to latest schema version
uv run alembic upgrade head

# Downgrade schema by one revision
uv run alembic downgrade -1

# Check migration status consistency
uv run alembic check
```

---

## Command Line Interface (CLI)

Use the CLI to manage sources and manually trigger collection pipelines.

### Setup

Before running commands, configure your SQLite database schemas:

```bash
uv run alembic upgrade head
```

### CLI Command List

#### 1. Add Source (`source add`)
```bash
uv run glintory source add \
  --name hn-main \
  --type hackernews \
  --config config/hackernews-source.example.json
```
Use `--disabled` to register a source in inactive status. Use `--json` for JSON output.

#### 2. List Sources (`source list`)
```bash
uv run glintory source list --enabled-only --json
```

#### 3. Show Source Details (`source show`)
```bash
uv run glintory source show hn-main
```

#### 4. Update Source Configuration (`source update`)
```bash
uv run glintory source update hn-main --config config/hackernews-source.example.json
```

#### 5. Toggle Active State (`source enable` / `source disable`)
```bash
uv run glintory source enable hn-main
uv run glintory source disable hn-main
```

#### 6. Trigger Collection (`collect`)
```bash
# Trigger collection for a specific source
uv run glintory collect --source hn-main

# Trigger collection for all active sources
uv run glintory collect --all
```

### Docker Usage

You can also run commands inside the Docker container:

```bash
# Run database migrations
docker compose run --rm app alembic upgrade head

# List registered sources
docker compose run --rm app glintory source list

# Run all active collection jobs
docker compose run --rm app glintory collect --all
```

---

## Web Interface & FTS5 Search

Search and filter collected signals in real-time through the UI powered by SQLite FTS5.

### Local Setup

```bash
# Apply migrations
uv run alembic upgrade head

# Run development server
uv run uvicorn glintory.main:app --reload
```
- Open Dashboard: `http://localhost:8000/`
- View Signals: `http://localhost:8000/signals`

### Search Engine Capabilities
- **Searchable Fields**: Matches terms inside `title` (weight: 8.0), `excerpt` (weight: 2.0), and `author` (weight: 1.0).
- **Filtering**: Supports data sources, signal types, and published dates.
- **Constraints**: Terms are sanitized and logically combined using `AND` operators. Custom operators (`OR`, `NOT`, `*`, `NEAR`) are not supported.

---

## Opportunity Analysis & Deterministic Scoring

Signals are clustered into opportunities and evaluated using versioned scoring algorithms.

### CLI Command List

#### 1. Analyze and Cluster (`analyze`)
Clusters orphaned signals into opportunities using TF-IDF and cosine similarity.

```bash
uv run glintory analyze --dry-run --json
```

#### 2. Score Opportunities (`score`)
Calculates values using versioned scoring algorithms.

```bash
uv run glintory score --dry-run --json
```

### Scoring Framework (V1 Rules)
- **Evidence Score (0 - 50 pts)**: Based on volume of signals, diverse origins, and freshness.
- **Feasibility Score (0 - 50 pts)**: Evaluated based on existence of concrete developer demand.
- **Penalty Score (-30 - 0 pts)**: Decreases points for source saturation or stagnancy.
- **Total Score (0 - 100 pts)**: Calculated as `Evidence + Feasibility + Penalty`.
- **Confidence (HIGH / MEDIUM / LOW)**: Determined by signal volumes and source varieties.

---

## Opportunity Review Workflow

Enables reviewers to manually override status states and filter signals.

### Features
1. **Transitions**: Move opportunities between `inbox`, `watch`, `validate`, `rejected`, and `archived` states. Reasons (minimum 3 chars) are required for rejections or archiving.
2. **Review Notes**: Reviewers can add, edit, or delete notes (max 500 chars).
3. **Evidence Editing**: Reclassify relationships (`supporting`, `related`, `contradicting`) or exclude irrelevant signals.
4. **FTS5 Linking**: Manually search and link additional signals to opportunities.
5. **Staleness Tracking**: Modifications mark opportunity scores as stale (`score_is_stale = true`), excluding them from Dashboard Top lists until recalculated.
6. **Audit Trails**: Actions are logged securely (`logger.info`).

---

## Local LLM Opportunity Enrichment

Spawn local instances of `llama-server` during GitHub Actions workflows to enrich opportunities with AI context.

### Design Principles & Setup
- **Deterministic Isolation**: LLM outputs do not override score values, relationships, or statuses.
- **No External Callouts**: Spawns a local host process. No credentials or outbound endpoints are exposed.
- **Stale Protection**: Calculations store a SHA-256 hash containing prompts and inputs. If any details change, the cache invalidates automatically.
- **Safety Sandboxing**: JSON schema structures are verified. HTML tags or unsafe scripts are discarded.
- **Static Previews**: Shows titles, summaries, target users, problem descriptions, and risk analyses in both English and Japanese. Fallback algorithms are executed if parsing fails.

### Run CLI

```bash
# Enrich unprocessed opportunities (max 10)
uv run glintory enrich run

# Force re-evaluation of a specific opportunity
uv run glintory enrich run --opportunity <UUID> --force
```

### Environment Variables
- `GLINTORY_LOCAL_LLM_ENABLED`: Activates local inference (`true` / `false`).
- `GLINTORY_LOCAL_LLM_MODEL_REPO`: Hugging Face model repository.
- `GLINTORY_LOCAL_LLM_MODEL_FILE`: Model filename.
- `GLINTORY_LOCAL_LLM_MODEL_PATH`: Local path to GGUF model.
- `GLINTORY_LOCAL_LLM_MODEL_REVISION`: Fixed commit hash of HF repository.
- `GLINTORY_LOCAL_LLM_MODEL_SHA256`: Expected SHA-256 of downloaded model.
- `GLINTORY_LOCAL_LLM_BINARY_PATH`: Path to `llama-server` binary.
- `GLINTORY_LOCAL_LLM_BINARY_SHA256`: Expected SHA-256 of `llama-server` binary.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
