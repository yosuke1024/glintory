import logging
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from glintory.domain.enums import CollectionRunStatus
from glintory.domain.operations import (
    CollectionTriggerType,
    SourceAlreadyRunningError,
)
from glintory.domain.scheduling import (
    SchedulerTickResult,
    ScheduleExecutionStatus,
)
from glintory.domain.models import Source
from glintory.infrastructure.schedule_execution_repository import ScheduleExecutionRepository
from glintory.services.collection import CollectionService
from glintory.config import settings

logger = logging.getLogger(__name__)

class SchedulerService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        collection_service: CollectionService,
    ) -> None:
        self.session_factory = session_factory
        self.collection_service = collection_service

    async def run_tick(self, *, owner_token: str) -> SchedulerTickResult:
        tick_start = datetime.now(UTC)

        # 1. Recover stale executions & claim due ones
        session = self.session_factory()
        due_count = 0
        claimed_execs = []
        try:
            exec_repo = ScheduleExecutionRepository(session)
            # Recover stale
            stale_min = settings.scheduler_execution_stale_minutes
            stale_threshold = tick_start - timedelta(minutes=stale_min)
            recovered_count = exec_repo.recover_stale_executions(
                now=tick_start,
                stale_threshold_dt=stale_threshold,
            )
            if recovered_count > 0:
                logger.warning(
                    f"operation=scheduler_recovery "
                    f"recovered_count={recovered_count} "
                    f"message=\"Recovered stale executions to abandoned status.\""
                )

            # Claim due executions
            max_due = settings.scheduler_max_due_per_tick
            claimed_execs = exec_repo.claim_due_executions(
                owner_token=owner_token,
                max_due=max_due,
                now=tick_start,
            )
            due_count = len(claimed_execs)  # Count of successfully claimed due slots
            session.commit()
        except Exception as e:
            session.rollback()
            logger.exception("operation=scheduler_tick_claim_failed message=\"Failed to claim due executions.\"")
            raise e
        finally:
            session.close()

        # Summary counts
        succeeded_count = 0
        partial_count = 0
        failed_count = 0
        skipped_busy_count = 0
        skipped_disabled_count = 0
        abandoned_count = 0
        execution_ids = []
        warnings = []

        # 2. Process each claimed execution sequentially (non-concurrently)
        for cl in claimed_execs:
            execution_ids.append(cl.execution_id)
            exec_start = datetime.now(UTC)

            # 2.1 Re-verify Source status in a short session
            session = self.session_factory()
            source_enabled = False
            try:
                src = session.get(Source, cl.source_id)
                if src:
                    source_enabled = src.enabled
            finally:
                session.close()

            # If source was disabled in the meantime
            if not source_enabled:
                session = self.session_factory()
                try:
                    exec_repo = ScheduleExecutionRepository(session)
                    exec_repo.finalize_execution(
                        execution_id=cl.execution_id,
                        status=ScheduleExecutionStatus.SKIPPED_DISABLED,
                        completed_at=datetime.now(UTC),
                        error_summary="Source was disabled at execution time."
                    )
                    session.commit()
                    skipped_disabled_count += 1
                except Exception as fe:
                    session.rollback()
                    warnings.append(f"Failed to finalize skipped_disabled for {cl.execution_id}: {str(fe)}")
                finally:
                    session.close()
                continue

            # 2.2 Run Collection (no DB transaction held during external HTTP communications)
            status = ScheduleExecutionStatus.FAILED
            run_id = None
            err_summary = None

            try:
                run_result = await self.collection_service.run_source(
                    cl.source_id,
                    trigger_type=CollectionTriggerType.SCHEDULED,
                )
                run_id = run_result.collection_run_id

                if run_result.status == CollectionRunStatus.SUCCEEDED:
                    status = ScheduleExecutionStatus.SUCCEEDED
                    succeeded_count += 1
                elif run_result.status == CollectionRunStatus.PARTIAL:
                    status = ScheduleExecutionStatus.PARTIAL
                    partial_count += 1
                elif run_result.status == CollectionRunStatus.FAILED:
                    status = ScheduleExecutionStatus.FAILED
                    failed_count += 1
                elif run_result.status == CollectionRunStatus.ABANDONED:
                    status = ScheduleExecutionStatus.ABANDONED
                    abandoned_count += 1

                # Retrieve error_summary from DB using collection_run_id if available
                if run_id:
                    db_sess = self.session_factory()
                    try:
                        from glintory.domain.models import CollectionRun
                        run_rec = db_sess.get(CollectionRun, run_id)
                        if run_rec:
                            err_summary = run_rec.error_summary
                    finally:
                        db_sess.close()

            except SourceAlreadyRunningError:
                status = ScheduleExecutionStatus.SKIPPED_BUSY
                skipped_busy_count += 1
                err_summary = "Source is already running."
            except Exception as e:
                status = ScheduleExecutionStatus.FAILED
                failed_count += 1
                err_summary = f"Unexpected error: {str(e)}"
                logger.exception(f"operation=scheduler_execution_error execution_id={cl.execution_id} source_id={cl.source_id}")

            # 2.3 Finalize Execution in a short session
            session = self.session_factory()
            try:
                exec_repo = ScheduleExecutionRepository(session)
                exec_repo.finalize_execution(
                    execution_id=cl.execution_id,
                    status=status,
                    completed_at=datetime.now(UTC),
                    collection_run_id=run_id,
                    error_summary=err_summary,
                )
                session.commit()
            except Exception as fe:
                session.rollback()
                logger.exception(f"operation=scheduler_finalize_failed execution_id={cl.execution_id} message=\"{str(fe)}\"")
                warnings.append(f"Failed to finalize execution {cl.execution_id}: {str(fe)}")
            finally:
                session.close()

            duration_ms = int((datetime.now(UTC) - exec_start).total_seconds() * 1000)
            logger.info(
                f"operation=scheduler_execution "
                f"execution_id={cl.execution_id} "
                f"source_id={cl.source_id} "
                f"scheduled_for={cl.scheduled_for.isoformat()} "
                f"status={status.value} "
                f"collection_run_id={run_id or 'none'} "
                f"duration_ms={duration_ms}"
            )

        tick_end = datetime.now(UTC)
        duration_ms = int((tick_end - tick_start).total_seconds() * 1000)

        logger.info(
            f"operation=scheduler_tick "
            f"due_schedule_count={due_count} "
            f"claimed_execution_count={len(claimed_execs)} "
            f"succeeded_count={succeeded_count} "
            f"partial_count={partial_count} "
            f"failed_count={failed_count} "
            f"skipped_busy_count={skipped_busy_count} "
            f"skipped_disabled_count={skipped_disabled_count} "
            f"abandoned_count={abandoned_count} "
            f"duration_ms={duration_ms}"
        )

        return SchedulerTickResult(
            tick_started_at=tick_start,
            tick_completed_at=tick_end,
            due_schedule_count=due_count,
            claimed_execution_count=len(claimed_execs),
            succeeded_count=succeeded_count,
            partial_count=partial_count,
            failed_count=failed_count,
            skipped_busy_count=skipped_busy_count,
            skipped_disabled_count=skipped_disabled_count,
            abandoned_count=abandoned_count,
            execution_ids=tuple(execution_ids),
            warnings=tuple(warnings),
        )
