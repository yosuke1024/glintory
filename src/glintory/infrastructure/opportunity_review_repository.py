from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from glintory.domain.enums import EvidenceRelationType, OpportunityStatus
from glintory.domain.models import (
    Decision,
    Note,
    Opportunity,
    OpportunitySignal,
    Signal,
)
from glintory.domain.review import (
    ConcurrentStatusChangeError,
    EvidenceAlreadyExcludedError,
    EvidenceAlreadyLinkedError,
    EvidenceNotExcludedError,
    EvidenceNotLinkedError,
    NoteNotFoundError,
    OpportunityNotFoundError,
    ReviewValidationError,
    SignalNotFoundError,
)


class OpportunityReviewRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def transition_status(
        self,
        opportunity_id: str,
        expected_status: OpportunityStatus,
        target_status: OpportunityStatus,
        reason: str | None,
        now_dt: datetime,
    ) -> None:
        """Update opportunity status and record decision in a single transaction."""
        opp = self.session.get(Opportunity, opportunity_id)
        if not opp:
            raise OpportunityNotFoundError("Opportunity not found.")

        if opp.status != expected_status:
            raise ConcurrentStatusChangeError(
                "Opportunity status has changed concurrently."
            )

        opp.status = target_status
        opp.updated_at = now_dt

        decision = Decision(
            opportunity_id=opportunity_id,
            from_status=expected_status,
            to_status=target_status,
            reason=reason,
            created_at=now_dt,
        )
        self.session.add(decision)

        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError(
                "Database error during status transition."
            ) from None

    def create_note(
        self,
        opportunity_id: str,
        body: str,
        now_dt: datetime,
    ) -> str:
        """Create a new note for an opportunity."""
        opp = self.session.get(Opportunity, opportunity_id)
        if not opp:
            raise OpportunityNotFoundError("Opportunity not found.")

        note = Note(
            opportunity_id=opportunity_id,
            body=body,
            created_at=now_dt,
            updated_at=now_dt,
        )
        self.session.add(note)

        try:
            self.session.flush()
            return note.id
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError(
                "Database error during note creation."
            ) from None

    def update_note(
        self,
        opportunity_id: str,
        note_id: str,
        body: str,
        now_dt: datetime,
    ) -> None:
        """Update an existing note."""
        note = self.session.get(Note, note_id)
        if not note or note.opportunity_id != opportunity_id:
            raise NoteNotFoundError("Note not found.")

        note.body = body
        note.updated_at = now_dt

        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError("Database error during note update.") from None

    def delete_note(
        self,
        opportunity_id: str,
        note_id: str,
    ) -> None:
        """Delete an existing note."""
        note = self.session.get(Note, note_id)
        if not note or note.opportunity_id != opportunity_id:
            raise NoteNotFoundError("Note not found.")

        self.session.delete(note)

        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError(
                "Database error during note deletion."
            ) from None

    def add_evidence(
        self,
        opportunity_id: str,
        signal_id: str,
        relation_type: EvidenceRelationType,
        relevance_score: float,
        review_note: str | None,
        now_dt: datetime,
    ) -> None:
        """Link a new signal or restore a previously excluded link."""
        opp = self.session.get(Opportunity, opportunity_id)
        if not opp:
            raise OpportunityNotFoundError("Opportunity not found.")

        sig = self.session.get(Signal, signal_id)
        if not sig:
            raise SignalNotFoundError("Signal not found.")

        opp_sig = (
            self.session.query(OpportunitySignal)
            .filter_by(opportunity_id=opportunity_id, signal_id=signal_id)
            .first()
        )

        if opp_sig:
            if not opp_sig.is_excluded:
                raise EvidenceAlreadyLinkedError("Evidence is already linked.")

            # Restore and update fields
            opp_sig.is_excluded = False
            opp_sig.relation_type = relation_type
            opp_sig.relevance_score = relevance_score
            opp_sig.association_source = "manual"
            opp_sig.reviewed_at = now_dt
            opp_sig.review_note = review_note
            opp_sig.updated_at = now_dt
        else:
            opp_sig = OpportunitySignal(
                opportunity_id=opportunity_id,
                signal_id=signal_id,
                relation_type=relation_type,
                relevance_score=relevance_score,
                association_source="manual",
                is_excluded=False,
                reviewed_at=now_dt,
                review_note=review_note,
                created_at=now_dt,
                updated_at=now_dt,
            )
            self.session.add(opp_sig)

        opp.evidence_updated_at = now_dt

        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError(
                "Database error during evidence addition."
            ) from None

    def update_evidence(
        self,
        opportunity_id: str,
        signal_id: str,
        relation_type: EvidenceRelationType,
        relevance_score: float,
        review_note: str | None,
        now_dt: datetime,
    ) -> str:
        """Update relation and relevance for an active evidence."""
        opp_sig = (
            self.session.query(OpportunitySignal)
            .filter_by(opportunity_id=opportunity_id, signal_id=signal_id)
            .first()
        )
        if not opp_sig or opp_sig.is_excluded:
            raise EvidenceNotLinkedError("Evidence is not linked.")

        # Check identical
        old_note = opp_sig.review_note or ""
        new_note = review_note or ""

        if (
            opp_sig.relation_type == relation_type
            and opp_sig.relevance_score == relevance_score
            and old_note == new_note
        ):
            return "identical"

        if (
            opp_sig.relation_type == relation_type
            and opp_sig.relevance_score == relevance_score
        ):
            opp_sig.review_note = review_note
            opp_sig.reviewed_at = now_dt
            opp_sig.updated_at = now_dt
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                raise ReviewValidationError(
                    "Database error during evidence update."
                ) from None
            return "note_only"

        opp_sig.relation_type = relation_type
        opp_sig.relevance_score = relevance_score
        opp_sig.review_note = review_note
        opp_sig.reviewed_at = now_dt
        opp_sig.updated_at = now_dt

        opp = self.session.get(Opportunity, opportunity_id)
        if opp:
            opp.evidence_updated_at = now_dt

        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError(
                "Database error during evidence update."
            ) from None
        return "score_fields"

    def exclude_evidence(
        self,
        opportunity_id: str,
        signal_id: str,
        review_note: str,
        now_dt: datetime,
    ) -> None:
        """Exclude an evidence link."""
        opp_sig = (
            self.session.query(OpportunitySignal)
            .filter_by(opportunity_id=opportunity_id, signal_id=signal_id)
            .first()
        )
        if not opp_sig or opp_sig.is_excluded:
            raise EvidenceAlreadyExcludedError(
                "Evidence is already excluded or not linked."
            )

        opp_sig.is_excluded = True
        opp_sig.review_note = review_note
        opp_sig.reviewed_at = now_dt
        opp_sig.updated_at = now_dt

        opp = self.session.get(Opportunity, opportunity_id)
        if opp:
            opp.evidence_updated_at = now_dt

        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError(
                "Database error during evidence exclusion."
            ) from None

    def restore_evidence(
        self,
        opportunity_id: str,
        signal_id: str,
        relation_type: EvidenceRelationType,
        relevance_score: float,
        review_note: str | None,
        now_dt: datetime,
    ) -> None:
        """Restore an excluded evidence link."""
        opp_sig = (
            self.session.query(OpportunitySignal)
            .filter_by(opportunity_id=opportunity_id, signal_id=signal_id)
            .first()
        )
        if not opp_sig or not opp_sig.is_excluded:
            raise EvidenceNotExcludedError("Evidence is not excluded or not linked.")

        opp_sig.is_excluded = False
        opp_sig.relation_type = relation_type
        opp_sig.relevance_score = relevance_score
        opp_sig.association_source = "manual"
        opp_sig.review_note = review_note
        opp_sig.reviewed_at = now_dt
        opp_sig.updated_at = now_dt

        opp = self.session.get(Opportunity, opportunity_id)
        if opp:
            opp.evidence_updated_at = now_dt

        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ReviewValidationError(
                "Database error during evidence restoration."
            ) from None
