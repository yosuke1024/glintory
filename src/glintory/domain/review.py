from dataclasses import dataclass

from glintory.domain.enums import EvidenceRelationType, OpportunityStatus

# ------------------------------------------------------------
# Domain Request Models
# ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatusTransitionRequest:
    opportunity_id: str
    expected_status: OpportunityStatus
    target_status: OpportunityStatus
    reason: str | None


@dataclass(frozen=True, slots=True)
class NoteCreateRequest:
    opportunity_id: str
    body: str


@dataclass(frozen=True, slots=True)
class NoteUpdateRequest:
    opportunity_id: str
    note_id: str
    body: str


@dataclass(frozen=True, slots=True)
class EvidenceAddRequest:
    opportunity_id: str
    signal_id: str
    relation_type: EvidenceRelationType
    relevance_score: float
    review_note: str | None


@dataclass(frozen=True, slots=True)
class EvidenceUpdateRequest:
    opportunity_id: str
    signal_id: str
    relation_type: EvidenceRelationType
    relevance_score: float
    review_note: str | None


@dataclass(frozen=True, slots=True)
class EvidenceReviewResult:
    opportunity_id: str
    signal_id: str
    action: str
    score_is_stale: bool


# ------------------------------------------------------------
# Domain Errors
# ------------------------------------------------------------


class ReviewDomainError(Exception):
    """Base domain error for opportunity review."""

    pass


class OpportunityNotFoundError(ReviewDomainError):
    pass


class InvalidStatusTransitionError(ReviewDomainError):
    pass


class ConcurrentStatusChangeError(ReviewDomainError):
    pass


class ReviewReasonRequiredError(ReviewDomainError):
    pass


class ReviewValidationError(ReviewDomainError):
    pass


class NoteNotFoundError(ReviewDomainError):
    pass


class SignalNotFoundError(ReviewDomainError):
    pass


class EvidenceAlreadyLinkedError(ReviewDomainError):
    pass


class EvidenceNotLinkedError(ReviewDomainError):
    pass


class EvidenceAlreadyExcludedError(ReviewDomainError):
    pass


class EvidenceNotExcludedError(ReviewDomainError):
    pass
