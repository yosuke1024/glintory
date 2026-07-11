import logging
import math
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.enums import EvidenceRelationType, OpportunityStatus
from glintory.domain.models import OpportunitySignal
from glintory.domain.review import (
    EvidenceAddRequest,
    EvidenceReviewResult,
    EvidenceUpdateRequest,
    InvalidStatusTransitionError,
    NoteCreateRequest,
    NoteUpdateRequest,
    ReviewReasonRequiredError,
    ReviewValidationError,
    StatusTransitionRequest,
)
from glintory.infrastructure.opportunity_review_repository import (
    OpportunityReviewRepository,
)

logger = logging.getLogger(__name__)


class OpportunityReviewService:
    TRANSITION_RULES = {
        OpportunityStatus.INBOX: {
            OpportunityStatus.WATCH,
            OpportunityStatus.VALIDATE,
            OpportunityStatus.REJECTED,
            OpportunityStatus.ARCHIVED,
        },
        OpportunityStatus.WATCH: {
            OpportunityStatus.INBOX,
            OpportunityStatus.VALIDATE,
            OpportunityStatus.REJECTED,
            OpportunityStatus.ARCHIVED,
        },
        OpportunityStatus.VALIDATE: {
            OpportunityStatus.WATCH,
            OpportunityStatus.BUILD,
            OpportunityStatus.REJECTED,
            OpportunityStatus.ARCHIVED,
        },
        OpportunityStatus.BUILD: {
            OpportunityStatus.VALIDATE,
            OpportunityStatus.REJECTED,
            OpportunityStatus.ARCHIVED,
        },
        OpportunityStatus.REJECTED: {
            OpportunityStatus.INBOX,
            OpportunityStatus.ARCHIVED,
        },
        OpportunityStatus.ARCHIVED: {
            OpportunityStatus.INBOX,
        },
    }

    def __init__(
        self,
        session_factory: Callable[[], Session],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def _validate_uuid(self, val: str) -> None:
        if not val or len(val) != 36:
            raise ReviewValidationError("Invalid ID format.")

    def _validate_relevance(self, score: float) -> None:
        if math.isnan(score) or math.isinf(score):
            raise ReviewValidationError("Relevance score must be a finite number.")
        if not (0.0 <= score <= 1.0):
            raise ReviewValidationError("Relevance score must be between 0.0 and 1.0.")

    def _validate_review_note(
        self, relation_type: EvidenceRelationType, note: str | None
    ) -> str | None:
        if note is not None:
            note = note.strip()
            note = unicodedata.normalize("NFC", note)
            note = note.replace("\x00", "")
            note = note.replace("\r\n", "\n").replace("\r", "\n")
            if len(note) > settings.review_reason_max_chars:
                raise ReviewValidationError(
                    f"Review note exceeds maximum length of {settings.review_reason_max_chars}."
                )
        else:
            note = ""

        if relation_type == EvidenceRelationType.CONTRADICTING and (
            not note or len(note) < 3
        ):
            raise ReviewValidationError(
                "Contradicting relation requires a review note of at least 3 characters."
            )
        return note if note else None

    def _validate_reason(
        self,
        current_status: OpportunityStatus,
        target_status: OpportunityStatus,
        reason: str | None,
    ) -> str | None:
        if reason is not None:
            reason = reason.strip()
            reason = unicodedata.normalize("NFC", reason)
            reason = reason.replace("\x00", "")
            reason = reason.replace("\r\n", "\n").replace("\r", "\n")
            if len(reason) > settings.review_reason_max_chars:
                raise ReviewValidationError(
                    f"Reason exceeds maximum length of {settings.review_reason_max_chars}."
                )
        else:
            reason = ""

        reason_required = False
        if target_status in (
            OpportunityStatus.REJECTED,
            OpportunityStatus.ARCHIVED,
        ) or current_status in (OpportunityStatus.REJECTED, OpportunityStatus.ARCHIVED):
            reason_required = True

        if reason_required and (not reason or len(reason) < 3):
            raise ReviewReasonRequiredError(
                "A reason of at least 3 characters is required for this status change."
            )

        return reason if reason else None

    def _validate_note_body(self, body: str) -> str:
        if not body:
            raise ReviewValidationError("Note body cannot be empty.")
        body = body.strip()
        body = unicodedata.normalize("NFC", body)
        body = body.replace("\x00", "")
        body = body.replace("\r\n", "\n").replace("\r", "\n")
        if not body:
            raise ReviewValidationError("Note body cannot be empty or whitespace-only.")
        if len(body) > settings.review_note_max_chars:
            raise ReviewValidationError(
                f"Note body exceeds maximum length of {settings.review_note_max_chars}."
            )
        return body

    def transition_status(self, request: StatusTransitionRequest) -> None:
        """Execute opportunity status transition with validation."""
        self._validate_uuid(request.opportunity_id)
        if request.expected_status == request.target_status:
            raise InvalidStatusTransitionError("Cannot transition to the same status.")

        allowed = self.TRANSITION_RULES.get(request.expected_status, set())
        if request.target_status not in allowed:
            raise InvalidStatusTransitionError(
                f"Transition from {request.expected_status.value} to {request.target_status.value} is not allowed."
            )

        reason = self._validate_reason(
            request.expected_status, request.target_status, request.reason
        )

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        now_dt = self.clock()
        try:
            repo.transition_status(
                opportunity_id=request.opportunity_id,
                expected_status=request.expected_status,
                target_status=request.target_status,
                reason=reason,
                now_dt=now_dt,
            )
            session.commit()
            logger.info(
                "Opportunity review status transitioned: opp_id=%s, from=%s, to=%s",
                request.opportunity_id,
                request.expected_status.value,
                request.target_status.value,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_note(self, request: NoteCreateRequest) -> str:
        """Create a new review note."""
        self._validate_uuid(request.opportunity_id)
        body = self._validate_note_body(request.body)

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        now_dt = self.clock()
        try:
            note_id = repo.create_note(
                opportunity_id=request.opportunity_id,
                body=body,
                now_dt=now_dt,
            )
            session.commit()
            logger.info(
                "Opportunity review note created: opp_id=%s, note_id=%s",
                request.opportunity_id,
                note_id,
            )
            return note_id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_note(self, request: NoteUpdateRequest) -> None:
        """Update an existing review note."""
        self._validate_uuid(request.opportunity_id)
        self._validate_uuid(request.note_id)
        body = self._validate_note_body(request.body)

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        now_dt = self.clock()
        try:
            repo.update_note(
                opportunity_id=request.opportunity_id,
                note_id=request.note_id,
                body=body,
                now_dt=now_dt,
            )
            session.commit()
            logger.info(
                "Opportunity review note updated: opp_id=%s, note_id=%s",
                request.opportunity_id,
                request.note_id,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_note(self, opportunity_id: str, note_id: str) -> None:
        """Delete a review note."""
        self._validate_uuid(opportunity_id)
        self._validate_uuid(note_id)

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        try:
            repo.delete_note(
                opportunity_id=opportunity_id,
                note_id=note_id,
            )
            session.commit()
            logger.info(
                "Opportunity review note deleted: opp_id=%s, note_id=%s",
                opportunity_id,
                note_id,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_evidence(self, request: EvidenceAddRequest) -> EvidenceReviewResult:
        """Add manual evidence or restore excluded evidence."""
        self._validate_uuid(request.opportunity_id)
        self._validate_uuid(request.signal_id)
        self._validate_relevance(request.relevance_score)
        review_note = self._validate_review_note(
            request.relation_type, request.review_note
        )

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        now_dt = self.clock()
        try:
            # Check if there is an existing excluded link to decide action name
            existing = (
                session.query(OpportunitySignal)
                .filter_by(
                    opportunity_id=request.opportunity_id, signal_id=request.signal_id
                )
                .first()
            )
            action = "restored" if (existing and existing.is_excluded) else "added"

            repo.add_evidence(
                opportunity_id=request.opportunity_id,
                signal_id=request.signal_id,
                relation_type=request.relation_type,
                relevance_score=request.relevance_score,
                review_note=review_note,
                now_dt=now_dt,
            )
            session.commit()
            logger.info(
                "Opportunity review evidence added/linked: opp_id=%s, signal_id=%s, relation=%s, relevance=%s, action=%s",
                request.opportunity_id,
                request.signal_id,
                request.relation_type.value,
                request.relevance_score,
                action,
            )
            return EvidenceReviewResult(
                opportunity_id=request.opportunity_id,
                signal_id=request.signal_id,
                action=action,
                score_is_stale=True,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_evidence(self, request: EvidenceUpdateRequest) -> EvidenceReviewResult:
        """Update an active manual or clustered evidence."""
        self._validate_uuid(request.opportunity_id)
        self._validate_uuid(request.signal_id)
        self._validate_relevance(request.relevance_score)
        review_note = self._validate_review_note(
            request.relation_type, request.review_note
        )

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        now_dt = self.clock()
        try:
            update_type = repo.update_evidence(
                opportunity_id=request.opportunity_id,
                signal_id=request.signal_id,
                relation_type=request.relation_type,
                relevance_score=request.relevance_score,
                review_note=review_note,
                now_dt=now_dt,
            )
            session.commit()
            logger.info(
                "Opportunity review evidence updated: opp_id=%s, signal_id=%s, type=%s",
                request.opportunity_id,
                request.signal_id,
                update_type,
            )
            is_stale = update_type == "score_fields"
            action = "updated" if update_type != "identical" else "identical"
            return EvidenceReviewResult(
                opportunity_id=request.opportunity_id,
                signal_id=request.signal_id,
                action=action,
                score_is_stale=is_stale,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def exclude_evidence(
        self, opportunity_id: str, signal_id: str, review_note: str
    ) -> EvidenceReviewResult:
        """Exclude an active evidence from score calculations."""
        self._validate_uuid(opportunity_id)
        self._validate_uuid(signal_id)

        if not review_note or len(review_note.strip()) < 3:
            raise ReviewValidationError(
                "Excluding evidence requires a review note of at least 3 characters."
            )
        validated_note = self._validate_review_note(
            EvidenceRelationType.RELATED, review_note
        )  # related dummy to skip check
        review_note_val = validated_note if validated_note is not None else ""

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        now_dt = self.clock()
        try:
            repo.exclude_evidence(
                opportunity_id=opportunity_id,
                signal_id=signal_id,
                review_note=review_note_val,
                now_dt=now_dt,
            )
            session.commit()
            logger.info(
                "Opportunity review evidence excluded: opp_id=%s, signal_id=%s",
                opportunity_id,
                signal_id,
            )
            return EvidenceReviewResult(
                opportunity_id=opportunity_id,
                signal_id=signal_id,
                action="excluded",
                score_is_stale=True,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def restore_evidence(
        self,
        opportunity_id: str,
        signal_id: str,
        relation_type: EvidenceRelationType,
        relevance_score: float,
        review_note: str | None,
    ) -> EvidenceReviewResult:
        """Restore an excluded evidence link back to active state."""
        self._validate_uuid(opportunity_id)
        self._validate_uuid(signal_id)
        self._validate_relevance(relevance_score)
        review_note = self._validate_review_note(relation_type, review_note)

        session = self.session_factory()
        repo = OpportunityReviewRepository(session)
        now_dt = self.clock()
        try:
            repo.restore_evidence(
                opportunity_id=opportunity_id,
                signal_id=signal_id,
                relation_type=relation_type,
                relevance_score=relevance_score,
                review_note=review_note,
                now_dt=now_dt,
            )
            session.commit()
            logger.info(
                "Opportunity review evidence restored: opp_id=%s, signal_id=%s, relation=%s, relevance=%s",
                opportunity_id,
                signal_id,
                relation_type.value,
                relevance_score,
            )
            return EvidenceReviewResult(
                opportunity_id=opportunity_id,
                signal_id=signal_id,
                action="restored",
                score_is_stale=True,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
