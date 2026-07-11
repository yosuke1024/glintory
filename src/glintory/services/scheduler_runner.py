import asyncio
import logging
import secrets
import signal
from datetime import UTC, datetime
from typing import Callable

from sqlalchemy.orm import Session

from glintory.domain.scheduling import (
    SchedulerLeaseLostError,
)
from glintory.infrastructure.scheduler_lease import SchedulerLeaseRepository
from glintory.services.scheduler_service import SchedulerService
from glintory.config import settings

logger = logging.getLogger(__name__)

class SchedulerRunner:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        scheduler_service: SchedulerService,
        owner_token: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.scheduler_service = scheduler_service
        self.owner_token = owner_token or secrets.token_urlsafe(32)

        self.poll_seconds = settings.scheduler_poll_seconds
        self.lease_seconds = settings.scheduler_lease_seconds
        self.heartbeat_seconds = settings.scheduler_heartbeat_seconds

        self._shutdown_event = asyncio.Event()
        self._lease_lost = False
        self._active_ticks_count = 0

    async def run_once(self) -> int:
        """
        Runs a single tick of the scheduler.
        Returns exit code:
        0: success
        6: lease unavailable
        7: lease lost
        1: other error
        """
        logger.info(
            "operation=scheduler_start "
            "lease_name=default "
            "mode=once "
            "message=\"Starting scheduler in once mode.\""
        )

        # 1. Try to acquire lease
        session = self.session_factory()
        try:
            lease_repo = SchedulerLeaseRepository(session)
            acquired = lease_repo.acquire(
                owner_token=self.owner_token,
                lease_seconds=self.lease_seconds,
            )
            session.commit()
            if not acquired:
                logger.error("operation=scheduler_lease_unavailable lease_name=default message=\"Could not acquire lease.\"")
                return 6
        except Exception:
            session.rollback()
            logger.exception("operation=scheduler_lease_error message=\"Lease acquisition failed.\"")
            return 1
        finally:
            session.close()

        # 2. Run tick
        exit_code = 0
        try:
            await self.scheduler_service.run_tick(owner_token=self.owner_token)
        except SchedulerLeaseLostError:
            logger.error("operation=scheduler_lease_lost lease_name=default message=\"Lease was lost during tick.\"")
            exit_code = 7
        except Exception:
            logger.exception("operation=scheduler_tick_error message=\"Error running scheduler tick.\"")
            exit_code = 1
        finally:
            # 3. Release lease
            session = self.session_factory()
            try:
                lease_repo = SchedulerLeaseRepository(session)
                lease_repo.release(owner_token=self.owner_token)
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

        return exit_code

    async def run_continuous(self) -> int:
        """
        Runs the scheduler continuously.
        """
        logger.info(
            "operation=scheduler_start "
            "lease_name=default "
            "mode=continuous "
            "message=\"Starting scheduler in continuous mode.\""
        )

        # Signal handling setup
        try:
            loop = asyncio.get_running_loop()
            def handle_signal():
                logger.info("operation=scheduler_shutdown_requested message=\"Shutdown signal received.\"")
                self._shutdown_event.set()

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, handle_signal)
        except (ValueError, RuntimeError):
            # add_signal_handler fails if not in main thread (e.g. during some pytest runner setups)
            pass

        # 1. Acquire initial lease
        session = self.session_factory()
        try:
            lease_repo = SchedulerLeaseRepository(session)
            acquired = lease_repo.acquire(
                owner_token=self.owner_token,
                lease_seconds=self.lease_seconds,
            )
            session.commit()
            if not acquired:
                logger.error("operation=scheduler_lease_unavailable lease_name=default message=\"Could not acquire lease.\"")
                return 6
        except Exception:
            session.rollback()
            logger.exception("operation=scheduler_lease_error message=\"Initial lease acquisition failed.\"")
            return 1
        finally:
            session.close()

        # Start background task loops
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        tick_loop_task = asyncio.create_task(self._tick_loop())

        # Wait for shutdown signal or lease lost
        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            logger.info("operation=scheduler_cancelled message=\"Scheduler runner was cancelled.\"")
        finally:
            # Signal shutdown to background loops
            self._shutdown_event.set()

            # Cancel background tasks and wait
            heartbeat_task.cancel()
            tick_loop_task.cancel()

            await asyncio.gather(heartbeat_task, tick_loop_task, return_exceptions=True)

            # Wait for active tick processes to finish safely
            while self._active_ticks_count > 0:
                await asyncio.sleep(0.1)

            # Release lease
            session = self.session_factory()
            try:
                lease_repo = SchedulerLeaseRepository(session)
                lease_repo.release(owner_token=self.owner_token)
                session.commit()
                logger.info("operation=scheduler_lease_released lease_name=default message=\"Lease released successfully.\"")
            except Exception:
                session.rollback()
            finally:
                session.close()

        if self._lease_lost:
            return 7
        return 0

    async def _heartbeat_loop(self):
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.heartbeat_seconds)
                if self._shutdown_event.is_set():
                    break

                # Renew lease in a short session
                session = self.session_factory()
                try:
                    lease_repo = SchedulerLeaseRepository(session)
                    lease_repo.renew(
                        owner_token=self.owner_token,
                        lease_seconds=self.lease_seconds,
                    )
                    session.commit()
                    logger.debug("operation=scheduler_heartbeat lease_name=default message=\"Lease renewed.\"")
                except SchedulerLeaseLostError:
                    session.rollback()
                    logger.error("operation=scheduler_lease_lost lease_name=default message=\"Lease lost during heartbeat.\"")
                    self._lease_lost = True
                    self._shutdown_event.set()
                    break
                except Exception:
                    session.rollback()
                    logger.exception("operation=scheduler_heartbeat_failed message=\"Error renewing lease.\"")
                finally:
                    session.close()
            except asyncio.CancelledError:
                break

    async def _tick_loop(self):
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self.poll_seconds)
                if self._shutdown_event.is_set():
                    break

                if self._lease_lost:
                    break

                self._active_ticks_count += 1
                try:
                    await self.scheduler_service.run_tick(owner_token=self.owner_token)
                except SchedulerLeaseLostError:
                    logger.error("operation=scheduler_lease_lost lease_name=default message=\"Lease lost during tick.\"")
                    self._lease_lost = True
                    self._shutdown_event.set()
                    break
                except Exception:
                    logger.exception("operation=scheduler_tick_error message=\"Error running scheduler tick.\"")
                finally:
                    self._active_ticks_count -= 1
            except asyncio.CancelledError:
                break
