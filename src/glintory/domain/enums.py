from enum import StrEnum


class CollectionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class OpportunityStatus(StrEnum):
    INBOX = "inbox"
    WATCH = "watch"
    VALIDATE = "validate"
    BUILD = "build"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SignalType(StrEnum):
    PROJECT = "project"
    PAIN = "pain"
    REQUEST = "request"
    ADOPTION = "adoption"
    LAUNCH = "launch"
    HACKATHON_PROJECT = "hackathon_project"
    FUNDING = "funding"
    JOB_DEMAND = "job_demand"
    TREND = "trend"
    COMPARISON = "comparison"
    MIGRATION = "migration"
    COMPLAINT = "complaint"
    MANUAL = "manual"


class EvidenceRelationType(StrEnum):
    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    RELATED = "related"
