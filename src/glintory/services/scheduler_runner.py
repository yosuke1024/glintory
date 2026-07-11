import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.scheduling import (
    SchedulerLeaseLostError,
    SchedulerTickResult,
)
from glintory.infrastructure.scheduler_lease import SchedulerLeaseRepository
from glintory.services.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SchedulerRunOnceResult:
    exit_code: int
    tick_result: SchedulerTickResult | None


class SchedulerRunner:
    lease_seconds: float
    heartbeat_seconds: float

    def __init__(
        self,
        session_factory: Callable[[], Session],
        scheduler_service: SchedulerService,
        owner_token: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.scheduler_service = scheduler_service
        self.owner_token = owner_token or secrets.token_urlsafe(32)
        self.clock = clock or (lambda: datetime.now(UTC))

        self.lease_seconds = float(settings.scheduler_lease_seconds)
        self.heartbeat_seconds = float(settings.scheduler_heartbeat_seconds)

        self._shutdown_event = asyncio.Event()
        self._lease_lost = False
        self._active_ticks_count = 0

    async def run_once(self) -> SchedulerRunOnceResult:
        """
        Runs a single tick of the scheduler.
        """
        logger.info(
            "operation=scheduler_start "
            "lease_name=default "
            "mode=once "
            'message="Starting scheduler in once mode."'
        )

        # 1. Try to acquire lease
        session = self.session_factory()
        try:
            lease_repo = SchedulerLeaseRepository(session, clock=self.clock)
            acquired = lease_repo.acquire(
                owner_token=self.owner_token,
                lease_seconds=int(self.lease_seconds),
            )
            session.commit()
            if not acquired:
                logger.error(
                    'operation=scheduler_lease_unavailable lease_name=default message="Could not acquire lease."'
                )
                return SchedulerRunOnceResult(exit_code=6, tick_result=None)
        except Exception:
            session.rollback()
            logger.exception(
                'operation=scheduler_lease_error message="Lease acquisition failed."'
            )
            return SchedulerRunOnceResult(exit_code=1, tick_result=None)
        finally:
            session.close()

        # Start heartbeat background loop
        self._shutdown_event.clear()
        self._lease_lost = False
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # 2. Run tick
        exit_code = 0
        tick_result = None
        try:
            tick_result = await self.scheduler_service.run_tick(
                owner_token=self.owner_token
            )
        except SchedulerLeaseLostError:
            logger.error(
                'operation=scheduler_lease_lost lease_name=default message="Lease was lost during tick."'
            )
            exit_code = 7
        except Exception:
            logger.exception(
                'operation=scheduler_tick_error message="Error running scheduler tick."'
            )
            exit_code = 1
        finally:
            # Stop heartbeat loop task
            self._shutdown_event.set()
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

            # 3. Release lease
            session = self.session_factory()
            try:
                lease_repo = SchedulerLeaseRepository(session, clock=self.clock)
                lease_repo.release(owner_token=self.owner_token)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

        if self._lease_lost:
            exit_code = 7

        return SchedulerRunOnceResult(exit_code=exit_code, tick_result=tick_result)

    async def _heartbeat_loop(self):
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.heartbeat_seconds)
                if self._shutdown_event.is_set():
                    break

                # Renew lease in a short session
                session = self.session_factory()
                try:
                    lease_repo = SchedulerLeaseRepository(session, clock=self.clock)
                    lease_repo.renew(
                        owner_token=self.owner_token,
                        lease_seconds=int(self.lease_seconds),
                    )
                    session.commit()
                    logger.debug(
                        'operation=scheduler_heartbeat lease_name=default message="Lease renewed."'
                    )
                except SchedulerLeaseLostError:
                    session.rollback()
                    logger.error(
                        'operation=scheduler_lease_lost lease_name=default message="Lease lost during heartbeat."'
                    )
                    self._lease_lost = True
                    self._shutdown_event.set()
                    break
                except Exception:
                    session.rollback()
                    logger.exception(
                        'operation=scheduler_heartbeat_failed message="Error renewing lease."'
                    )
                finally:
                    session.close()
            except asyncio.CancelledError:
                break
