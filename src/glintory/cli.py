import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from glintory.bootstrap import bootstrap
from glintory.cli_config import ConfigLoadError, load_json_object
from glintory.collectors.registry import CollectorNotFoundError
from glintory.config import Settings
from glintory.domain.enums import CollectionRunStatus
from glintory.infrastructure.repositories import SourceRepository
from glintory.domain.operations import CollectionTriggerType, SourceAlreadyRunningError
from glintory.infrastructure.schema_status import (
    DatabaseSchemaError,
    check_schema_status,
)


def get_version() -> str:
    try:
        return importlib.metadata.version("glintory")
    except Exception:
        return "0.1.0"


def setup_logging() -> None:
    log_level_str = os.environ.get("GLINTORY_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level_str, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glintory",
        description="Glintory CLI to manage sources and perform collection.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Setup source command
    source_parser = subparsers.add_parser("source", help="Manage sources")
    source_subparsers = source_parser.add_subparsers(dest="subcommand", required=True)

    # Setup source add command
    add_parser = source_subparsers.add_parser("add", help="Add a new source")
    add_parser.add_argument("--name", required=True, help="Name of the source")
    add_parser.add_argument("--type", required=True, help="Type of the source")
    add_parser.add_argument("--config", required=True, help="Path to config JSON file")
    add_parser.add_argument(
        "--disabled", action="store_true", help="Create source as disabled"
    )
    add_parser.add_argument("--json", action="store_true", help="Output in JSON format")

    # Setup source list command
    list_parser = source_subparsers.add_parser("list", help="List all sources")
    list_parser.add_argument(
        "--enabled-only", action="store_true", help="Only list enabled sources"
    )
    list_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    # Setup source show command
    show_parser = source_subparsers.add_parser("show", help="Show details of a source")
    show_parser.add_argument("identifier", help="Source Name or UUID")
    show_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    # Setup source update command
    update_parser = source_subparsers.add_parser(
        "update", help="Update source configuration"
    )
    update_parser.add_argument("identifier", help="Source Name or UUID")
    update_parser.add_argument(
        "--config", required=True, help="Path to new config JSON file"
    )
    update_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    # Setup source enable command
    enable_parser = source_subparsers.add_parser("enable", help="Enable a source")
    enable_parser.add_argument("identifier", help="Source Name or UUID")
    enable_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    # Setup source disable command
    disable_parser = source_subparsers.add_parser("disable", help="Disable a source")
    disable_parser.add_argument("identifier", help="Source Name or UUID")
    disable_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    # Setup collect command
    collect_parser = subparsers.add_parser("collect", help="Perform collection run")
    group = collect_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", help="Name or UUID of a single source to collect")
    group.add_argument(
        "--all", action="store_true", help="Collect from all enabled sources"
    )
    collect_parser.add_argument(
        "--max-items", type=int, help="Override maximum items to collect (1-1000)"
    )
    collect_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    # Setup analyze command
    analyze_parser = subparsers.add_parser(
        "analyze", help="Perform opportunity clustering analysis"
    )
    analyze_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform analysis without writing to the database",
    )
    analyze_parser.add_argument(
        "--cluster-version",
        default="v1",
        help="Clustering algorithm version",
    )
    analyze_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )

    # Setup score command
    score_parser = subparsers.add_parser("score", help="Perform opportunity scoring")
    score_parser.add_argument(
        "--opportunity",
        help="UUID of a single opportunity to score",
    )
    score_parser.add_argument(
        "--as-of",
        help="Target date for evaluation (YYYY-MM-DD)",
    )
    score_parser.add_argument(
        "--max-opportunities",
        type=int,
        help="Maximum opportunities to process",
    )
    score_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score without saving to the database",
    )
    score_parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format",
    )

    return parser


async def run_cli(args: argparse.Namespace) -> int:
    setup_logging()

    # Determine settings
    settings = Settings()

    async with bootstrap(settings) as runtime:
        # Verify database schema status first
        try:
            check_schema_status(runtime.engine)
        except DatabaseSchemaError as e:
            sys.stderr.write(f"Database configuration error:\n{e}\n")
            return 5

        # Dispatch command
        if args.command == "source":
            return await run_source_command(args, runtime)
        if args.command == "collect":
            return await run_collect_command(args, runtime)
        if args.command == "analyze":
            return await run_analyze_command(args, runtime)
        if args.command == "score":
            return await run_score_command(args, runtime)

    return 0


async def _run_source_add(args: argparse.Namespace, runtime: Any) -> int:
    try:
        config_data = load_json_object(args.config)
    except ConfigLoadError as e:
        sys.stderr.write(f"Failed to load config: {e}\n")
        return 2

    try:
        collector = runtime.registry.get(args.type)
    except CollectorNotFoundError:
        sys.stderr.write(f"Unknown source type: {args.type}\n")
        return 2

    try:
        validated_config = collector.validate_config(config_data)
    except Exception as e:
        sys.stderr.write(f"Invalid configuration: {e}\n")
        return 2

    session = runtime.session_factory()
    try:
        repo = SourceRepository(session)
        source = repo.create(
            name=args.name,
            source_type=args.type,
            config=validated_config,
            enabled=not args.disabled,
        )
        session.commit()

        if args.json:
            print(
                json.dumps(
                    {
                        "id": source.id,
                        "name": source.name,
                        "source_type": source.source_type,
                        "enabled": source.enabled,
                    }
                )
            )
        else:
            print("Source created.")
            print(f"Name: {source.name}")
            print(f"Type: {source.source_type}")
            print(f"Enabled: {'yes' if source.enabled else 'no'}")
        return 0
    except ValueError as e:
        session.rollback()
        sys.stderr.write(f"Error: {e}\n")
        return 2
    except Exception as e:
        session.rollback()
        sys.stderr.write(f"Unexpected internal error: {e}\n")
        return 1
    finally:
        session.close()


async def _run_source_list(args: argparse.Namespace, runtime: Any) -> int:
    session = runtime.session_factory()
    try:
        repo = SourceRepository(session)
        sources = repo.list_enabled() if args.enabled_only else repo.list_all()

        if args.json:
            out = []
            for s in sources:
                out.append(
                    {
                        "id": s.id,
                        "name": s.name,
                        "source_type": s.source_type,
                        "enabled": s.enabled,
                        "last_success_at": s.last_success_at.isoformat()
                        if s.last_success_at
                        else None,
                        "last_failure_at": s.last_failure_at.isoformat()
                        if s.last_failure_at
                        else None,
                        "consecutive_failures": s.consecutive_failures,
                    }
                )
            print(json.dumps(out))
        else:
            print(
                f"{'NAME':<20} {'TYPE':<12} {'ENABLED':<8} {'LAST SUCCESS':<25} {'LAST FAILURE':<25} {'FAILURES':<8}"
            )
            for s in sources:
                succ = s.last_success_at.isoformat() if s.last_success_at else "-"
                fail = s.last_failure_at.isoformat() if s.last_failure_at else "-"
                enabled_str = "yes" if s.enabled else "no"
                print(
                    f"{s.name:<20} {s.source_type:<12} {enabled_str:<8} {succ:<25} {fail:<25} {s.consecutive_failures:<8}"
                )
        return 0
    finally:
        session.close()


async def _run_source_show(args: argparse.Namespace, runtime: Any) -> int:
    session = runtime.session_factory()
    try:
        repo = SourceRepository(session)
        source = repo.get_by_identifier(args.identifier)
        if not source:
            sys.stderr.write(f"Source not found: {args.identifier}\n")
            return 2

        try:
            collector = runtime.registry.get(source.source_type)
            summary = collector.get_config_summary(source.config)
        except Exception:
            summary = "No summary available"

        if args.json:
            print(
                json.dumps(
                    {
                        "id": source.id,
                        "name": source.name,
                        "source_type": source.source_type,
                        "enabled": source.enabled,
                        "auth_required": source.auth_required,
                        "last_success_at": source.last_success_at.isoformat()
                        if source.last_success_at
                        else None,
                        "last_failure_at": source.last_failure_at.isoformat()
                        if source.last_failure_at
                        else None,
                        "consecutive_failures": source.consecutive_failures,
                        "last_error": source.last_error,
                        "config_summary": summary,
                    }
                )
            )
        else:
            print(f"ID: {source.id}")
            print(f"Name: {source.name}")
            print(f"Type: {source.source_type}")
            print(f"Enabled: {'yes' if source.enabled else 'no'}")
            print(f"Auth Required: {'yes' if source.auth_required else 'no'}")
            print(
                f"Last Success: {source.last_success_at.isoformat() if source.last_success_at else '-'}"
            )
            print(
                f"Last Failure: {source.last_failure_at.isoformat() if source.last_failure_at else '-'}"
            )
            print(f"Consecutive Failures: {source.consecutive_failures}")
            print(f"Last Error: {source.last_error or '-'}")
            print("Config Summary:")
            for line in summary.splitlines():
                print(f"  {line}")
        return 0
    finally:
        session.close()


async def _run_source_update(args: argparse.Namespace, runtime: Any) -> int:
    try:
        config_data = load_json_object(args.config)
    except ConfigLoadError as e:
        sys.stderr.write(f"Failed to load config: {e}\n")
        return 2

    session = runtime.session_factory()
    exit_code = 0
    try:
        repo = SourceRepository(session)
        source = repo.get_by_identifier(args.identifier)
        if not source:
            sys.stderr.write(f"Source not found: {args.identifier}\n")
            exit_code = 2
        else:
            try:
                collector = runtime.registry.get(source.source_type)
            except CollectorNotFoundError:
                sys.stderr.write(
                    f"Collector not found for type: {source.source_type}\n"
                )
                return 2

            try:
                validated_config = collector.validate_config(config_data)
            except Exception as e:
                sys.stderr.write(f"Invalid configuration: {e}\n")
                return 2

            repo.update_config(source.id, validated_config)
            session.commit()

            if args.json:
                print(
                    json.dumps(
                        {
                            "id": source.id,
                            "name": source.name,
                            "source_type": source.source_type,
                            "enabled": source.enabled,
                        }
                    )
                )
            else:
                print("Source updated.")
                print(f"Name: {source.name}")
                print(f"Type: {source.source_type}")
                print(f"Enabled: {'yes' if source.enabled else 'no'}")
    except ValueError as e:
        session.rollback()
        sys.stderr.write(f"Error: {e}\n")
        exit_code = 2
    except Exception as e:
        session.rollback()
        sys.stderr.write(f"Unexpected internal error: {e}\n")
        exit_code = 1
    finally:
        session.close()

    return exit_code


async def _run_source_toggle(args: argparse.Namespace, runtime: Any) -> int:
    session = runtime.session_factory()
    try:
        repo = SourceRepository(session)
        source = repo.get_by_identifier(args.identifier)
        if not source:
            sys.stderr.write(f"Source not found: {args.identifier}\n")
            return 2

        enabled_val = args.subcommand == "enable"
        repo.set_enabled(source.id, enabled_val)
        session.commit()

        status_str = "enabled" if enabled_val else "disabled"
        if args.json:
            print(
                json.dumps(
                    {"id": source.id, "name": source.name, "enabled": source.enabled}
                )
            )
        else:
            print(f"Source '{source.name}' has been {status_str}.")
        return 0
    except Exception as e:
        session.rollback()
        sys.stderr.write(f"Unexpected internal error: {e}\n")
        return 1
    finally:
        session.close()


async def run_source_command(args: argparse.Namespace, runtime: Any) -> int:
    if args.subcommand == "add":
        return await _run_source_add(args, runtime)
    if args.subcommand == "list":
        return await _run_source_list(args, runtime)
    if args.subcommand == "show":
        return await _run_source_show(args, runtime)
    if args.subcommand == "update":
        return await _run_source_update(args, runtime)
    if args.subcommand in ("enable", "disable"):
        return await _run_source_toggle(args, runtime)
    return 2


async def _run_collect_single(args: argparse.Namespace, runtime: Any) -> int:
    session = runtime.session_factory()
    try:
        repo = SourceRepository(session)
        source = repo.get_by_identifier(args.source)
        if not source:
            sys.stderr.write(f"Source not found: {args.source}\n")
            return 2
        if not source.enabled:
            sys.stderr.write(f"Source '{source.name}' is disabled.\n")
            return 2
        source_id = source.id
        source_name = source.name
        source_type = source.source_type
    finally:
        session.close()

    try:
        res = await runtime.collection_service.run_source(
            source_id,
            trigger_type=CollectionTriggerType.CLI,
            max_items=args.max_items,
        )
    except SourceAlreadyRunningError as e:
        sys.stderr.write(f"Source operations conflict: {e}\n")
        return 4
    except Exception as e:
        sys.stderr.write(f"Collection execution error: {e}\n")
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "source_type": source_type,
                    "collection_run_id": res.run_id,
                    "status": res.status.value,
                    "fetched_count": res.fetched_count,
                    "inserted_count": res.inserted_count,
                    "updated_count": res.updated_count,
                    "duplicate_count": res.duplicate_count,
                    "warning_count": res.warning_count,
                    "error_count": res.error_count,
                }
            )
        )
    else:
        print(f"Source: {source_name}")
        print(f"Type: {source_type}")
        print(f"Status: {res.status.value}")
        print(f"Fetched: {res.fetched_count}")
        print(f"Inserted: {res.inserted_count}")
        print(f"Updated: {res.updated_count}")
        print(f"Duplicates: {res.duplicate_count}")
        print(f"Warnings: {res.warning_count}")
        print(f"Errors: {res.error_count}")
        print(f"Collection Run: {res.run_id}")

    if res.status == CollectionRunStatus.SUCCEEDED:
        return 0
    if res.status == CollectionRunStatus.PARTIAL:
        return 3
    return 4


async def _run_collect_all(args: argparse.Namespace, runtime: Any) -> int:
    session = runtime.session_factory()
    try:
        repo = SourceRepository(session)
        enabled_sources = repo.list_enabled()
    finally:
        session.close()

    if not enabled_sources:
        if args.json:
            print(
                json.dumps(
                    {
                        "summary": {
                            "source_count": 0,
                            "succeeded_count": 0,
                            "partial_count": 0,
                            "failed_count": 0,
                            "fetched_count": 0,
                            "inserted_count": 0,
                            "updated_count": 0,
                            "duplicate_count": 0,
                            "warning_count": 0,
                            "error_count": 0,
                        },
                        "runs": [],
                    }
                )
            )
        else:
            print("No enabled sources found.")
        return 0

    runs_data = []
    succeeded_count = 0
    partial_count = 0
    failed_count = 0

    total_fetched = 0
    total_inserted = 0
    total_updated = 0
    total_duplicate = 0
    total_warning = 0
    total_error = 0
    for source in enabled_sources:
        try:
            res = await runtime.collection_service.run_source(
                source.id,
                trigger_type=CollectionTriggerType.CLI,
                max_items=args.max_items,
            )
        except SourceAlreadyRunningError as e:
            sys.stderr.write(
                f"Source '{source.name}' is busy (already running). Skipping.\n"
            )
            failed_count += 1
            total_error += 1
            runs_data.append(
                {
                    "source_id": source.id,
                    "source_name": source.name,
                    "source_type": source.source_type,
                    "collection_run_id": "",
                    "status": "failed",
                    "fetched_count": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "duplicate_count": 0,
                    "warning_count": 0,
                    "error_count": 1,
                }
            )
            continue
        except Exception as e:
            sys.stderr.write(
                f"Unexpected error running collector for '{source.name}': {e}\n"
            )
            failed_count += 1
            total_error += 1
            runs_data.append(
                {
                    "source_id": source.id,
                    "source_name": source.name,
                    "source_type": source.source_type,
                    "collection_run_id": "",
                    "status": "failed",
                    "fetched_count": 0,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "duplicate_count": 0,
                    "warning_count": 0,
                    "error_count": 1,
                }
            )
            continue

        if res.status == CollectionRunStatus.SUCCEEDED:
            succeeded_count += 1
        elif res.status == CollectionRunStatus.PARTIAL:
            partial_count += 1
        else:
            failed_count += 1

        total_fetched += res.fetched_count
        total_inserted += res.inserted_count
        total_updated += res.updated_count
        total_duplicate += res.duplicate_count
        total_warning += res.warning_count
        total_error += res.error_count

        runs_data.append(
            {
                "source_id": source.id,
                "source_name": source.name,
                "source_type": source.source_type,
                "collection_run_id": res.run_id,
                "status": res.status.value,
                "fetched_count": res.fetched_count,
                "inserted_count": res.inserted_count,
                "updated_count": res.updated_count,
                "duplicate_count": res.duplicate_count,
                "warning_count": res.warning_count,
                "error_count": res.error_count,
            }
        )

    if args.json:
        print(
            json.dumps(
                {
                    "summary": {
                        "source_count": len(enabled_sources),
                        "succeeded_count": succeeded_count,
                        "partial_count": partial_count,
                        "failed_count": failed_count,
                        "fetched_count": total_fetched,
                        "inserted_count": total_inserted,
                        "updated_count": total_updated,
                        "duplicate_count": total_duplicate,
                        "warning_count": total_warning,
                        "error_count": total_error,
                    },
                    "runs": runs_data,
                }
            )
        )
    else:
        for run in runs_data:
            print(
                f"Source '{run['source_name']}' ({run['source_type']}): status={run['status']}, fetched={run['fetched_count']}, inserted={run['inserted_count']}, updated={run['updated_count']}, duplicates={run['duplicate_count']}, warnings={run['warning_count']}, errors={run['error_count']}"
            )

        print("\n--- Summary ---")
        print(f"Sources: {len(enabled_sources)}")
        print(f"Succeeded: {succeeded_count}")
        print(f"Partial: {partial_count}")
        print(f"Failed: {failed_count}")
        print(f"Fetched: {total_fetched}")
        print(f"Inserted: {total_inserted}")
        print(f"Updated: {total_updated}")
        print(f"Duplicates: {total_duplicate}")
        print(f"Warnings: {total_warning}")
        print(f"Errors: {total_error}")

    if failed_count > 0:
        return 4
    if partial_count > 0:
        return 3
    return 0


async def run_collect_command(args: argparse.Namespace, runtime: Any) -> int:
    if args.max_items is not None and not (1 <= args.max_items <= 1000):
        sys.stderr.write("max-items must be between 1 and 1000.\n")
        return 2

    if args.source:
        return await _run_collect_single(args, runtime)
    if args.all:
        return await _run_collect_all(args, runtime)

    return 2


async def run_analyze_command(args: argparse.Namespace, runtime: Any) -> int:
    session = runtime.session_factory()
    try:
        from glintory.domain.clustering import OpportunityClusteringConfig
        from glintory.infrastructure.opportunity_clustering_repository import (
            OpportunityClusteringRepository,
        )
        from glintory.services.opportunity_analysis import OpportunityAnalysisService
        from glintory.services.opportunity_clustering import (
            OpportunityClusteringEngine,
        )

        config = OpportunityClusteringConfig(cluster_version=args.cluster_version)
        repo = OpportunityClusteringRepository(session)
        engine = OpportunityClusteringEngine(config)
        service = OpportunityAnalysisService(session, repo, engine, config)

        res = service.analyze_and_cluster(dry_run=args.dry_run)

        if args.json:
            print(
                json.dumps(
                    {
                        "analyzed_signals_count": res.analyzed_signals_count,
                        "created_opportunities_count": res.created_opportunities_count,
                        "linked_signals_count": res.linked_signals_count,
                        "dry_run": res.dry_run,
                    }
                )
            )
        else:
            print("Opportunity analysis completed.")
            print(f"Signals analyzed: {res.analyzed_signals_count}")
            print(f"Opportunities created: {res.created_opportunities_count}")
            print(f"Signals linked: {res.linked_signals_count}")
            print(f"Dry run: {'yes' if res.dry_run else 'no'}")
        return 0
    except Exception as e:
        session.rollback()
        sys.stderr.write(f"Error during analysis: {e}\n")
        return 1
    finally:
        session.close()


async def run_score_command(args: argparse.Namespace, runtime: Any) -> int:
    target_date = None
    if args.as_of:
        try:
            target_date = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            sys.stderr.write("Invalid date format for --as-of. Use YYYY-MM-DD.\n")
            return 2

    max_opps = None
    if args.max_opportunities is not None:
        if not (1 <= args.max_opportunities <= 10000):
            sys.stderr.write("max-opportunities must be between 1 and 10000.\n")
            return 2
        max_opps = args.max_opportunities

    from glintory.infrastructure.opportunity_scoring_repository import (
        OpportunityScoringRepository,
    )
    from glintory.services.opportunity_scoring import OpportunityScoringEngine
    from glintory.services.opportunity_scoring_service import OpportunityScoringService

    scoring_version = runtime.settings.scoring_version

    try:
        engine = OpportunityScoringEngine(scoring_version=scoring_version)
    except ValueError as e:
        sys.stderr.write(f"Scoring configuration error: {e}\n")
        return 1

    service = OpportunityScoringService(
        session_factory=runtime.session_factory,
        repository_factory=OpportunityScoringRepository,
        engine=engine,
        scoring_version=scoring_version,
    )

    try:
        res = service.score_opportunities(
            opportunity_id=args.opportunity,
            as_of_date=target_date,
            max_opportunities=max_opps,
            dry_run=args.dry_run,
        )

        if args.json:
            print(
                json.dumps(
                    {
                        "scoring_version": res.scoring_version,
                        "as_of_date": res.as_of_date.isoformat(),
                        "dry_run": res.dry_run,
                        "analyzed_opportunity_count": res.analyzed_opportunity_count,
                        "scored_opportunity_count": res.scored_opportunity_count,
                        "unchanged_opportunity_count": res.unchanged_opportunity_count,
                        "skipped_opportunity_count": res.skipped_opportunity_count,
                        "created_snapshot_count": res.created_snapshot_count,
                        "updated_opportunity_count": res.updated_opportunity_count,
                        "scored_opportunity_ids": res.scored_opportunity_ids,
                    }
                )
            )
        else:
            print("Opportunity scoring completed.")
            print(f"Scoring version: {res.scoring_version}")
            print(f"As of: {res.as_of_date.isoformat()}")
            print(f"Dry run: {'yes' if res.dry_run else 'no'}")
            print(f"Opportunities analyzed: {res.analyzed_opportunity_count}")
            print(f"Opportunities scored: {res.scored_opportunity_count}")
            print(f"Unchanged: {res.unchanged_opportunity_count}")
            print(f"Skipped: {res.skipped_opportunity_count}")
            print(f"Score snapshots created: {res.created_snapshot_count}")
            print(f"Opportunities updated: {res.updated_opportunity_count}")
        return 0
    except ValueError as e:
        sys.stderr.write(f"Argument Error: {e}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"Unexpected internal error during scoring: {e}\n")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run_cli(args))
    except KeyboardInterrupt:
        sys.stderr.write("Execution interrupted.\n")
        return 130
    except Exception as e:
        sys.stderr.write(f"Unexpected internal error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
