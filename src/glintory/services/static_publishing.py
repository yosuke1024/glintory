import json
import os
from datetime import UTC, datetime
from typing import Any

from jinja2 import Environment, select_autoescape
from sqlalchemy.orm import Session

from glintory.domain.models import (
    Opportunity,
    OpportunitySignal,
    ScheduleExecution,
    Signal,
    Source,
    SourceSchedule,
)


def safe_url(url: str | None) -> str:
    if not url:
        return "#"
    url_lower = url.lower().strip()
    if url_lower.startswith(("http://", "https://")):
        return url
    return "#"


def format_datetime(val: Any) -> str:
    if not val:
        return "N/A"
    if isinstance(val, str):
        return val
    return val.isoformat()


def build_static_site(
    session: Session,
    output_dir: str,
    base_path: str = "",
    site_url: str | None = None,
    pixapps_url: str | None = None,
) -> dict:
    # Ensure directories
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "opportunities"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "data"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "assets"), exist_ok=True)

    # Base path normalization
    # Ensure it starts with / but does not end with /
    if base_path:
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        if base_path.endswith("/"):
            base_path = base_path[:-1]
    else:
        base_path = ""

    # 1. Fetch data from DB
    sources = session.query(Source).all()
    schedules = session.query(SourceSchedule).all()

    # Latest schedule execution
    latest_exec = (
        session.query(ScheduleExecution)
        .order_by(ScheduleExecution.started_at.desc(), ScheduleExecution.id.desc())
        .first()
    )

    # Opportunities list sorted stable
    opportunities = (
        session.query(Opportunity)
        .order_by(
            Opportunity.total_score.desc(),
            Opportunity.last_scored_at.desc(),
            Opportunity.id.desc(),
        )
        .all()
    )

    # Recent signals (last 20)
    signals = (
        session.query(Signal)
        .order_by(
            Signal.created_at.desc(),
            Signal.id.desc(),
        )
        .limit(20)
        .all()
    )

    top_ops = opportunities[:5]
    latest_signals = signals[:10]

    # Generate data/latest.json and data/opportunities.json
    latest_json_data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "latest_scheduler_result": {
            "execution_id": latest_exec.id if latest_exec else None,
            "started_at": format_datetime(latest_exec.started_at)
            if latest_exec
            else None,
            "completed_at": format_datetime(latest_exec.completed_at)
            if latest_exec
            else None,
            "status": latest_exec.status if latest_exec else None,
        },
        "opportunities_count": len(opportunities),
        "signals_count": session.query(Signal).count(),
    }

    with open(os.path.join(output_dir, "data", "latest.json"), "w") as f:
        json.dump(latest_json_data, f, indent=2)

    opportunities_json_data = []
    for op in opportunities:
        opportunities_json_data.append(
            {
                "id": op.id,
                "title": op.title,
                "proposed_solution": op.proposed_solution,
                "total_score": op.total_score,
                "confidence": op.confidence,
                "status": op.status,
                "last_scored_at": format_datetime(op.last_scored_at),
            }
        )
    with open(os.path.join(output_dir, "data", "opportunities.json"), "w") as f:
        json.dump(opportunities_json_data, f, indent=2)

    # Assets: premium app.css
    css_content = """
:root {
  --bg-primary: #0a0f1d;
  --bg-secondary: #141b2d;
  --bg-tertiary: #1f293d;
  --text-primary: #f3f4f6;
  --text-secondary: #9ca3af;
  --accent: #6366f1;
  --accent-gradient: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
  --success: #10b981;
  --border: rgba(255, 255, 255, 0.08);
}

body {
  margin: 0;
  font-family: 'Outfit', 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background-color: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
}

header {
  border-bottom: 1px solid var(--border);
  background-color: rgba(20, 27, 45, 0.7);
  backdrop-filter: blur(12px);
  position: sticky;
  top: 0;
  z-index: 100;
  padding: 1rem 2rem;
}

.nav-container {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.logo {
  font-size: 1.5rem;
  font-weight: 800;
  background: var(--accent-gradient);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  text-decoration: none;
}

.nav-links a {
  color: var(--text-secondary);
  text-decoration: none;
  margin-left: 1.5rem;
  font-weight: 500;
  transition: color 0.2s;
}

.nav-links a:hover {
  color: var(--text-primary);
}

.container {
  max-width: 1200px;
  margin: 2rem auto;
  padding: 0 1.5rem;
}

.hero {
  text-align: center;
  padding: 4rem 1rem;
  background: radial-gradient(circle at 50% 50%, rgba(99, 102, 241, 0.15) 0%, transparent 60%);
}

.hero h1 {
  font-size: 3rem;
  margin-bottom: 1rem;
  font-weight: 800;
}

.hero p {
  color: var(--text-secondary);
  font-size: 1.25rem;
  max-width: 600px;
  margin: 0 auto 2rem;
}

.btn-primary {
  display: inline-block;
  background: var(--accent-gradient);
  color: #fff;
  padding: 0.75rem 1.5rem;
  border-radius: 0.5rem;
  text-decoration: none;
  font-weight: 600;
  transition: transform 0.2s, box-shadow 0.2s;
}

.btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 20px rgba(99, 102, 241, 0.4);
}

.section-title {
  font-size: 1.75rem;
  font-weight: 700;
  margin-top: 3rem;
  margin-bottom: 1.5rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.section-link {
  font-size: 1rem;
  color: var(--accent);
  text-decoration: none;
}

.section-link:hover {
  text-decoration: underline;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 1.5rem;
}

.card {
  background-color: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 1rem;
  padding: 1.5rem;
  transition: transform 0.2s, border-color 0.2s;
  text-decoration: none;
  color: inherit;
  display: block;
}

.card:hover {
  transform: translateY(-4px);
  border-color: var(--accent);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1rem;
}

.card-title {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0;
}

.badge {
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-weight: 600;
  text-transform: uppercase;
}

.badge-accent {
  background-color: rgba(99, 102, 241, 0.15);
  color: #818cf8;
}

.badge-success {
  background-color: rgba(16, 185, 129, 0.15);
  color: #34d399;
}

.badge-info {
  background-color: rgba(59, 130, 246, 0.15);
  color: #60a5fa;
}

.score-value {
  font-size: 2rem;
  font-weight: 800;
  color: var(--accent);
}

.meta-info {
  display: flex;
  gap: 1rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
  margin-top: 1rem;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 1.5rem;
}

th, td {
  padding: 1rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

th {
  color: var(--text-secondary);
  font-weight: 600;
}

tr:hover td {
  background-color: rgba(255, 255, 255, 0.02);
}

.footer {
  text-align: center;
  padding: 4rem 1rem;
  border-top: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 0.9rem;
  margin-top: 4rem;
}

.footer a {
  color: var(--text-primary);
  text-decoration: none;
}
"""
    with open(os.path.join(output_dir, "assets", "app.css"), "w") as f:
        f.write(css_content.strip())

    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    env.filters["safe_url"] = safe_url
    env.filters["format_datetime"] = format_datetime

    index_template = env.from_string("""
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>Glintory - Discovery Portal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="{{ base_path }}/assets/app.css">
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <div class="nav-container">
      <a href="{{ base_path }}/" class="logo">Glintory</a>
      <nav class="nav-links">
        <a href="{{ base_path }}/">Dashboard</a>
        <a href="{{ base_path }}/opportunities/">Opportunities</a>
      </nav>
    </div>
  </header>

  <div class="hero">
    <h1>Find the signals worth building on.</h1>
    <p>Glintory automates the discovery, extraction, and scoring of technology & product opportunities from public source signals.</p>
    {% if pixapps_url %}
      <a href="{{ pixapps_url | safe_url }}" class="btn-primary" target="_blank" rel="noopener">Explore PixApps</a>
    {% endif %}
  </div>

  <div class="container">
    <div class="grid">
      <div class="card">
        <h3>Scheduler Status</h3>
        <p><strong>Last Execution:</strong> {{ latest_exec_time | format_datetime }}</p>
        <p><strong>Status:</strong> <span class="badge {% if latest_exec_status == 'succeeded' %}badge-success{% else %}badge-accent{% endif %}">{{ latest_exec_status }}</span></p>
      </div>
      <div class="card">
        <h3>Repository Stats</h3>
        <p><strong>Active Sources:</strong> {{ active_sources_count }} / {{ total_sources_count }}</p>
        <p><strong>Total Scored Opportunities:</strong> {{ total_ops_count }}</p>
      </div>
    </div>

    <div class="section-title">
      Top Opportunities
      <a href="{{ base_path }}/opportunities/" class="section-link">View All Opportunities &rarr;</a>
    </div>
    <div class="grid">
      {% for op in top_ops %}
        <a href="{{ base_path }}/opportunities/{{ op.id }}/" class="card">
          <div class="card-header">
            <h4 class="card-title">{{ op.title }}</h4>
            <div class="score-value">{{ op.total_score }}</div>
          </div>
          <p>{{ op.proposed_solution[:150] }}...</p>
          <div class="meta-info">
            <span>Confidence: {{ op.confidence }}</span>
            <span>Status: {{ op.status }}</span>
          </div>
        </a>
      {% else %}
        <p>No opportunities found yet.</p>
      {% endfor %}
    </div>

    <div class="section-title">Latest Signals</div>
    <table>
      <thead>
        <tr>
          <th>Signal Title</th>
          <th>Source</th>
          <th>Created At</th>
        </tr>
      </thead>
      <tbody>
        {% for sig in latest_signals %}
          <tr>
            <td><a href="{{ sig.canonical_url | safe_url }}" target="_blank" rel="noopener" style="color: inherit; text-decoration: none;">{{ sig.title }}</a></td>
            <td>{{ sig.source_id[:8] }}</td>
            <td>{{ sig.created_at | format_datetime }}</td>
          </tr>
        {% else %}
          <tr>
            <td colspan="3">No signals collected yet.</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <footer class="footer">
    <p>Powered by <a href="https://github.com/google/glintory" target="_blank" rel="noopener">Glintory</a>. Machine-managed static site.</p>
  </footer>
</body>
</html>
""")

    list_template = env.from_string("""
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>Opportunities - Glintory</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="{{ base_path }}/assets/app.css">
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <div class="nav-container">
      <a href="{{ base_path }}/" class="logo">Glintory</a>
      <nav class="nav-links">
        <a href="{{ base_path }}/">Dashboard</a>
        <a href="{{ base_path }}/opportunities/">Opportunities</a>
      </nav>
    </div>
  </header>

  <div class="container">
    <div class="section-title">All Scored Opportunities</div>
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Total Score</th>
          <th>Confidence</th>
          <th>Status</th>
          <th>Last Scored At</th>
        </tr>
      </thead>
      <tbody>
        {% for op in opportunities %}
          <tr>
            <td><a href="{{ base_path }}/opportunities/{{ op.id }}/" style="color: inherit; font-weight: 600; text-decoration: none;">{{ op.title }}</a></td>
            <td><span class="score-value" style="font-size: 1.25rem;">{{ op.total_score }}</span></td>
            <td>{{ op.confidence }}</td>
            <td><span class="badge badge-accent">{{ op.status }}</span></td>
            <td>{{ op.last_scored_at | format_datetime }}</td>
          </tr>
        {% else %}
          <tr>
            <td colspan="5">No opportunities found yet.</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <footer class="footer">
    <p>Powered by <a href="https://github.com/google/glintory" target="_blank" rel="noopener">Glintory</a>.</p>
  </footer>
</body>
</html>
""")

    detail_template = env.from_string("""
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>{{ op.title }} - Glintory</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="{{ base_path }}/assets/app.css">
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <div class="nav-container">
      <a href="{{ base_path }}/" class="logo">Glintory</a>
      <nav class="nav-links">
        <a href="{{ base_path }}/">Dashboard</a>
        <a href="{{ base_path }}/opportunities/">Opportunities</a>
      </nav>
    </div>
  </header>

  <div class="container">
    <div class="card" style="margin-top: 2rem; padding: 2rem;">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <h1 style="margin: 0; font-size: 2.25rem;">{{ op.title }}</h1>
        <div class="score-value" style="font-size: 3rem;">{{ op.total_score }}</div>
      </div>
      
      <p style="font-size: 1.15rem; color: var(--text-secondary); margin-top: 1.5rem;">{{ op.proposed_solution }}</p>
      
      <div class="meta-info" style="margin-top: 2rem;">
        <span><strong>Confidence:</strong> {{ op.confidence }}</span>
        <span><strong>Status:</strong> {{ op.status }}</span>
        <span><strong>Last Scored:</strong> {{ op.last_scored_at | format_datetime }}</span>
      </div>
    </div>

    <div class="section-title">Evidence & Signals</div>
    {% for ev in evidences %}
      <div class="card" style="margin-bottom: 1rem; border-left: 4px solid var(--accent);">
        <div style="display: flex; justify-content: space-between;">
          <h4 style="margin: 0;"><a href="{{ ev.url | safe_url }}" target="_blank" rel="noopener" style="color: inherit; text-decoration: none;">{{ ev.title }}</a></h4>
          <span class="badge badge-accent">Relevance: {{ ev.relevance_score }}</span>
        </div>
        <p style="font-size: 0.9rem; color: var(--text-secondary); margin-top: 0.5rem;">{{ ev.excerpt }}</p>
        <div class="meta-info" style="margin-top: 0.5rem; font-size: 0.8rem;">
          <span>Source: {{ ev.source_type }}</span>
          <span>Published: {{ ev.published_at | format_datetime }}</span>
        </div>
      </div>
    {% else %}
      <p>No public evidence items associated with this opportunity.</p>
    {% endfor %}
  </div>

  <footer class="footer">
    <p>Powered by <a href="https://github.com/google/glintory" target="_blank" rel="noopener">Glintory</a>.</p>
  </footer>
</body>
</html>
""")

    # Render Index
    active_sources = [s for s in schedules if s.enabled]
    total_sources_count = len(sources)
    active_sources_count = len(active_sources)

    rendered_index = index_template.render(
        base_path=base_path,
        pixapps_url=pixapps_url,
        latest_exec_time=latest_exec.started_at if latest_exec else None,
        latest_exec_status=latest_exec.status if latest_exec else "inactive",
        active_sources_count=active_sources_count,
        total_sources_count=total_sources_count,
        total_ops_count=len(opportunities),
        top_ops=top_ops,
        latest_signals=latest_signals,
    )
    with open(os.path.join(output_dir, "index.html"), "w") as f:
        f.write(rendered_index)

    # Render List page
    rendered_list = list_template.render(
        base_path=base_path,
        opportunities=opportunities,
    )
    with open(os.path.join(output_dir, "opportunities", "index.html"), "w") as f:
        f.write(rendered_list)

    # Render Details
    for op in opportunities:
        # Fetch related evidence via opportunity_signals
        ev_signals = (
            session.query(Signal, OpportunitySignal.relevance_score)
            .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
            .filter(OpportunitySignal.opportunity_id == op.id)
            .order_by(OpportunitySignal.relevance_score.desc())
            .all()
        )

        evidences = []
        for sig, rel_score in ev_signals:
            evidences.append(
                {
                    "title": sig.title,
                    "url": sig.canonical_url,
                    "excerpt": sig.excerpt,
                    "source_type": sig.source_id[:8],
                    "published_at": sig.created_at,
                    "relevance_score": rel_score,
                }
            )

        rendered_detail = detail_template.render(
            base_path=base_path,
            op=op,
            evidences=evidences,
        )

        op_dir = os.path.join(output_dir, "opportunities", op.id)
        os.makedirs(op_dir, exist_ok=True)
        with open(os.path.join(op_dir, "index.html"), "w") as f:
            f.write(rendered_detail)

    # Static: robots.txt
    robots_content = "User-agent: *\nAllow: /\n"
    with open(os.path.join(output_dir, "robots.txt"), "w") as f:
        f.write(robots_content)

    # Static: .nojekyll
    with open(os.path.join(output_dir, ".nojekyll"), "w") as f:
        f.write("")

    # Static: sitemap.xml
    sitemap_items = []

    # helper for sitemap date format
    def get_now_str() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    target_site_url = site_url or ""
    if target_site_url.endswith("/"):
        target_site_url = target_site_url[:-1]

    sitemap_items.append(f"""  <url>
    <loc>{target_site_url}{base_path}/</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""")

    sitemap_items.append(f"""  <url>
    <loc>{target_site_url}{base_path}/opportunities/</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")

    for op in opportunities:
        sitemap_items.append(f"""  <url>
    <loc>{target_site_url}{base_path}/opportunities/{op.id}/</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>""")

    sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{"\n".join(sitemap_items)}
</urlset>
"""
    with open(os.path.join(output_dir, "sitemap.xml"), "w") as f:
        f.write(sitemap_xml.strip())

    return {
        "opportunities_generated": len(opportunities),
        "total_files": len(opportunities)
        + 8,  # details + index + list + robots + sitemap + nojekyll + 2 data jsons
    }
