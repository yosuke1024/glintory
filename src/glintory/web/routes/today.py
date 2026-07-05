import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

# Identify absolute path for the templates directory
base_dir = pathlib.Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(base_dir / "templates"))


@router.get("/", response_class=HTMLResponse)
async def read_today(request: Request):
    # Define demo opportunities meeting the product spec and user criteria
    demo_opportunities = [
        {
            "id": "demo-opt-1",
            "title": "Local-First Markdown Note Sync Tool",
            "description": "A lightweight local-first markdown editor that auto-syncs with GitHub and self-hosted databases without complex setup.",
            "evidence_count": 3,
            "evidence": [
                "GitHub issue #412: 'Is there a way to sync notes locally?'",
                "Hacker News comment: 'I wish Obsidian-like sync was open source and cheaper.'",
            ],
            "total_score": 85,
            "confidence": "High",
            "mvp_scope": "React + SQLite local-first sync with GitHub Gist",
            "gap": "Standard tools require proprietary clouds or complex self-hosting configurations.",
        },
        {
            "id": "demo-opt-2",
            "title": "Zero-Config DB Backup Agent for Railway",
            "description": "Automatically backups Postgres/MySQL to AWS S3 or Cloudflare R2 with email notifications on failure.",
            "evidence_count": 2,
            "evidence": [
                "DEV Community post: 'Railway DB backups are hard to configure'",
                "Hacker News Ask HN: 'How do you automate Railway DB backups?'",
            ],
            "total_score": 78,
            "confidence": "Medium",
            "mvp_scope": "Dockerized Python script deployable on Railway via 1-Click Template",
            "gap": "Current templates require complex Env setup or paid third-party integrations.",
        },
        {
            "id": "demo-opt-3",
            "title": "Ad-supported Multi-platform Recipe Planner",
            "description": "An offline-first recipe manager and shopping list planner with local sync for small family groups.",
            "evidence_count": 2,
            "evidence": [
                "Twitter search: 'looking for an alternative recipe app that does not cost a monthly subscription'",
                "Reddit r/indiehackers: 'My wife and I want a shared recipe app that works offline'",
            ],
            "total_score": 75,
            "confidence": "Medium",
            "mvp_scope": "Capacitor + SQLite with ad integration and basic local-network sync",
            "gap": "Most recipe apps require internet connections and expensive subscriptions.",
        },
    ]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "opportunities": demo_opportunities,
            "last_collected": "2026-07-06 00:00:00",
            "new_signals": 12,
            "failed_sources": 0,
        },
    )
