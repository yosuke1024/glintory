from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from glintory.collectors.base import RawItem
from glintory.domain.models import Signal
from glintory.domain.signals import (
    SignalIdentityCollisionError,
    SignalPersistenceError,
    SignalPersistenceResult,
    SignalPersistenceWarning,
)
from glintory.infrastructure.repositories import SignalRepository
from glintory.services.normalization import SignalNormalizer


class SignalIngestionService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory
        self.normalizer = SignalNormalizer()

    def ingest(
        self,
        *,
        source_id: str,
        source_type: str,
        collection_run_id: str,
        raw_items: Sequence[RawItem],
        collected_at: datetime,
    ) -> SignalPersistenceResult:
        # 1. Normalize items batch
        norm_result = self.normalizer.normalize_batch(
            source_id=source_id,
            source_type=source_type,
            collection_run_id=collection_run_id,
            items=raw_items,
            collected_at=collected_at,
        )

        ingestion_warnings = [
            SignalPersistenceWarning(
                code=w.code, message=w.message, external_id=w.external_id
            )
            for w in norm_result.warnings
        ]
        ingestion_errors = [
            SignalPersistenceError(
                code=e.code, message=e.message, external_id=e.external_id
            )
            for e in norm_result.errors
        ]

        # If no valid signals, we can skip DB connection and return directly
        if not norm_result.signals:
            return SignalPersistenceResult(
                inserted_count=0,
                updated_count=0,
                duplicate_count=0,
                signal_ids=[],
                warnings=ingestion_warnings,
                errors=ingestion_errors,
            )

        session = self.session_factory()
        inserted_count = 0
        updated_count = 0
        duplicate_count = 0
        signal_ids = []

        try:
            repo = SignalRepository(session)
            for norm_sig in norm_result.signals:
                existing = None
                # First match priority: external_id
                if norm_sig.external_id:
                    existing = repo.find_by_external_id(
                        norm_sig.source_id, norm_sig.external_id
                    )

                # Second match priority: canonical_url
                if not existing:
                    existing = repo.find_by_canonical_url(
                        norm_sig.source_id, norm_sig.canonical_url
                    )

                if existing:
                    try:
                        # Check if there is meaningful change before saving
                        is_meaningful_change = (
                            existing.external_id != norm_sig.external_id
                            or existing.canonical_url != norm_sig.canonical_url
                            or existing.title != norm_sig.title
                            or existing.excerpt != norm_sig.excerpt
                            or existing.author != norm_sig.author
                            or existing.published_at != norm_sig.published_at
                            or existing.language != norm_sig.language
                            or existing.signal_type != norm_sig.signal_type
                            or list(existing.categories) != list(norm_sig.categories)
                            or list(existing.tags) != list(norm_sig.tags)
                            or dict(existing.metrics) != dict(norm_sig.metrics)
                            or dict(existing.raw_metadata)
                            != dict(norm_sig.raw_metadata)
                            or existing.content_hash != norm_sig.content_hash
                            or abs(existing.freshness_score - norm_sig.freshness_score)
                            > 1e-9
                            or abs(
                                existing.source_quality_score
                                - norm_sig.source_quality_score
                            )
                            > 1e-9
                        )

                        if is_meaningful_change:
                            repo.update_existing(existing, norm_sig)
                            updated_count += 1
                        else:
                            # Duplicate: update run_id and collected_at bypassing ORM onupdate trigger
                            session.execute(
                                update(Signal)
                                .where(Signal.id == existing.id)
                                .values(
                                    collection_run_id=norm_sig.collection_run_id,
                                    collected_at=norm_sig.collected_at,
                                    updated_at=Signal.updated_at,
                                )
                            )
                            # Expire updated fields in ORM object to synchronize with DB on next access
                            session.expire(
                                existing, ["collection_run_id", "collected_at"]
                            )
                            duplicate_count += 1

                        signal_ids.append(existing.id)

                    except SignalIdentityCollisionError as e:
                        ingestion_errors.append(
                            SignalPersistenceError(
                                code="identity_collision",
                                message=str(e),
                                external_id=norm_sig.external_id,
                            )
                        )
                else:
                    sig = repo.insert(norm_sig)
                    inserted_count += 1
                    signal_ids.append(sig.id)

            session.commit()

        except Exception as e:
            session.rollback()
            code = "database_error"
            if "integrity" in type(e).__name__.lower():
                code = "integrity_error"

            # Mask connection strings/queries from leaking
            safe_msg = f"A database error occurred: {type(e).__name__}"

            return SignalPersistenceResult(
                inserted_count=0,
                updated_count=0,
                duplicate_count=0,
                signal_ids=[],
                warnings=ingestion_warnings,
                errors=list(ingestion_errors)
                + [
                    SignalPersistenceError(
                        code=code,
                        message=safe_msg,
                        external_id=None,
                    )
                ],
            )
        finally:
            session.close()

        return SignalPersistenceResult(
            inserted_count=inserted_count,
            updated_count=updated_count,
            duplicate_count=duplicate_count,
            signal_ids=signal_ids,
            warnings=ingestion_warnings,
            errors=ingestion_errors,
        )
