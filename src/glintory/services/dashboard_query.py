from typing import Any

from glintory.infrastructure.dashboard_repository import DashboardRepository


class DashboardQueryService:
    """Service to coordinate loading summary and recent items data for the Today page dashboard."""

    def __init__(self, repo: DashboardRepository) -> None:
        self.repo = repo

    def get_dashboard_data(self) -> dict[str, Any]:
        """Loads and consolidates database metrics and top recent items."""
        summary = self.repo.get_real_summary()
        recent_signals = self.repo.get_recent_signals()
        return {
            "summary": summary,
            "recent_signals": recent_signals,
        }
