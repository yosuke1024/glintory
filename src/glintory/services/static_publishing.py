import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, select_autoescape


def validate_site_url(url: str | None) -> str:
    if not url:
        raise ValueError("SITE_URL_REQUIRED")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("INVALID_SITE_URL_SCHEME")
    if not parsed.netloc:
        raise ValueError("INVALID_SITE_URL_NETLOC")
    if parsed.username or parsed.password:
        raise ValueError("INVALID_SITE_URL_CREDENTIALS")
    if parsed.query:
        raise ValueError("INVALID_SITE_URL_QUERY")
    if parsed.fragment:
        raise ValueError("INVALID_SITE_URL_FRAGMENT")
    return url


from sqlalchemy.orm import Session

from glintory.domain.models import (
    Opportunity,
    OpportunitySignal,
    ScheduleExecution,
    Signal,
    Source,
    SourceSchedule,
)
from glintory.infrastructure.opportunity_query import check_stale


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
    generated_at: datetime | None = None,
) -> dict:
    valid_site_url = validate_site_url(site_url)

    # Set generated_at deterministically
    gen_time = generated_at or datetime.now(UTC)

    # Base path normalization
    if base_path:
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        if base_path.endswith("/"):
            base_path = base_path[:-1]
    else:
        base_path = ""

    # Ensure target directory exists for output
    target_parent = os.path.dirname(os.path.abspath(output_dir))
    if target_parent:
        os.makedirs(target_parent, exist_ok=True)

    # Create temporary build directory in the same parent directory to allow atomic move/replace
    temp_build_dir = os.path.join(target_parent, f".tmp-build-{uuid.uuid4().hex}")
    os.makedirs(temp_build_dir, exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "opportunities"), exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "data"), exist_ok=True)
    os.makedirs(os.path.join(temp_build_dir, "assets"), exist_ok=True)

    try:
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

        # Recent signals (last 20) with source join
        signals_data = (
            session.query(Signal, Source.name, Source.source_type)
            .join(Source, Signal.source_id == Source.id)
            .order_by(
                Signal.created_at.desc(),
                Signal.id.desc(),
            )
            .limit(20)
            .all()
        )

        top_ops = opportunities[:5]

        # Build signals dict list for index page
        latest_signals = []
        for sig, src_name, src_type in signals_data[:10]:
            latest_signals.append(
                {
                    "title": sig.title,
                    "canonical_url": sig.canonical_url,
                    "source_name": src_name,
                    "source_type": src_type,
                    "published_at": sig.published_at
                    if sig.published_at
                    else sig.collected_at,
                }
            )

        # Generate data/latest.json and data/opportunities.json
        latest_json_data = {
            "generated_at": gen_time.isoformat(),
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

        with open(os.path.join(temp_build_dir, "data", "latest.json"), "w") as f:
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
        with open(os.path.join(temp_build_dir, "data", "opportunities.json"), "w") as f:
            json.dump(opportunities_json_data, f, indent=2)

        # Assets: premium app.css (with only system fonts, no external CDN)
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
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
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
        with open(os.path.join(temp_build_dir, "assets", "app.css"), "w") as f:
            f.write(css_content.strip())

        # Retrieve repository URL from env
        repo_url = os.environ.get("GLINTORY_REPOSITORY_URL")

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
          <th>Source Name</th>
          <th>Source Type</th>
          <th>Published At</th>
        </tr>
      </thead>
      <tbody>
        {% for sig in latest_signals %}
          <tr>
            <td><a href="{{ sig.canonical_url | safe_url }}" target="_blank" rel="noopener" style="color: inherit; text-decoration: none;">{{ sig.title }}</a></td>
            <td>{{ sig.source_name }}</td>
            <td><span class="badge badge-info">{{ sig.source_type }}</span></td>
            <td>{{ sig.published_at | format_datetime }}</td>
          </tr>
        {% else %}
          <tr>
            <td colspan="4">No signals collected yet.</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <footer class="footer">
    <p>Powered by {% if repo_url %}<a href="{{ repo_url | safe_url }}" target="_blank" rel="noopener">Glintory</a>{% else %}Glintory{% endif %}. Machine-managed static site.</p>
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
          <th>Evidence Count</th>
          <th>Evidence Updated At</th>
          <th>Last Scored At</th>
        </tr>
      </thead>
      <tbody>
        {% for op_data in op_list_data %}
          <tr>
            <td><a href="{{ base_path }}/opportunities/{{ op_data.op.id }}/" style="color: inherit; font-weight: 600; text-decoration: none;">{{ op_data.op.title }}</a></td>
            <td><span class="score-value" style="font-size: 1.25rem;">{{ op_data.op.total_score }}</span></td>
            <td>{{ op_data.op.confidence }}</td>
            <td><span class="badge badge-accent">{{ op_data.op.status }}</span></td>
            <td>{{ op_data.evidence_count }}</td>
            <td>{{ op_data.evidence_updated_at | format_datetime }}</td>
            <td>{{ op_data.last_scored_at | format_datetime }}</td>
          </tr>
        {% else %}
          <tr>
            <td colspan="7">No opportunities found yet.</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <footer class="footer">
    <p>Powered by {% if repo_url %}<a href="{{ repo_url | safe_url }}" target="_blank" rel="noopener">Glintory</a>{% else %}Glintory{% endif %}.</p>
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
      
      <div class="meta-info" style="margin-top: 2rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
        <div><strong>Status:</strong> {{ op.status }}</div>
        <div><strong>Confidence:</strong> {{ op.confidence }}</div>
        <div><strong>Scoring Version:</strong> {{ op.current_scoring_version or "N/A" }}</div>
        <div><strong>Score Status:</strong> <span class="badge {% if score_is_stale %}badge-accent{% else %}badge-success{% endif %}">{% if score_is_stale %}Stale{% else %}Current{% endif %}</span></div>
        <div><strong>Evidence Score:</strong> {{ op.evidence_score }}</div>
        <div><strong>Feasibility Score:</strong> {{ op.feasibility_score }}</div>
        <div><strong>Penalty Score:</strong> {{ op.penalty_score }}</div>
        <div><strong>Last Scored:</strong> {{ op.last_scored_at | format_datetime }}</div>
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
          <span>Source: {{ ev.source_name }} ({{ ev.source_type }})</span>
          <span>Published: {{ ev.published_at | format_datetime }}</span>
        </div>
      </div>
    {% else %}
      <p>No public evidence items associated with this opportunity.</p>
    {% endfor %}
  </div>

  <footer class="footer">
    <p>Powered by {% if repo_url %}<a href="{{ repo_url | safe_url }}" target="_blank" rel="noopener">Glintory</a>{% else %}Glintory{% endif %}.</p>
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
            repo_url=repo_url,
        )
        with open(os.path.join(temp_build_dir, "index.html"), "w") as f:
            f.write(rendered_index)

        # For list page, construct data with non-excluded evidence count
        op_list_data = []
        for op in opportunities:
            ev_count = (
                session.query(OpportunitySignal)
                .filter(OpportunitySignal.opportunity_id == op.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .count()
            )
            op_list_data.append(
                {
                    "op": op,
                    "evidence_count": ev_count,
                    "evidence_updated_at": op.evidence_updated_at,
                    "last_scored_at": op.last_scored_at,
                }
            )

        # Render List page
        rendered_list = list_template.render(
            base_path=base_path,
            op_list_data=op_list_data,
            repo_url=repo_url,
        )
        with open(
            os.path.join(temp_build_dir, "opportunities", "index.html"), "w"
        ) as f:
            f.write(rendered_list)

        # Render Details
        for op in opportunities:
            # Fetch related evidence via opportunity_signals (excluding is_excluded = True)
            ev_signals = (
                session.query(
                    Signal,
                    OpportunitySignal.relevance_score,
                    Source.name,
                    Source.source_type,
                )
                .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
                .join(Source, Signal.source_id == Source.id)
                .filter(OpportunitySignal.opportunity_id == op.id)
                .filter(OpportunitySignal.is_excluded.is_(False))
                .order_by(OpportunitySignal.relevance_score.desc())
                .all()
            )

            evidences = []
            for sig, rel_score, src_name, src_type in ev_signals:
                evidences.append(
                    {
                        "title": sig.title,
                        "url": sig.canonical_url,
                        "excerpt": sig.excerpt,
                        "source_name": src_name,
                        "source_type": src_type,
                        "published_at": sig.published_at
                        if sig.published_at
                        else sig.collected_at,
                        "relevance_score": rel_score,
                    }
                )

            # Determine score stale status
            score_is_stale = check_stale(
                op.current_scoring_version,
                op.last_scored_at,
                op.evidence_updated_at,
            )

            rendered_detail = detail_template.render(
                base_path=base_path,
                op=op,
                evidences=evidences,
                score_is_stale=score_is_stale,
                repo_url=repo_url,
            )

            op_dir = os.path.join(temp_build_dir, "opportunities", op.id)
            os.makedirs(op_dir, exist_ok=True)
            with open(os.path.join(op_dir, "index.html"), "w") as f:
                f.write(rendered_detail)

        # Static: robots.txt
        robots_content = "User-agent: *\nAllow: /\n"
        with open(os.path.join(temp_build_dir, "robots.txt"), "w") as f:
            f.write(robots_content)

        # Static: .nojekyll
        with open(os.path.join(temp_build_dir, ".nojekyll"), "w") as f:
            f.write("")

        # Static: sitemap.xml
        sitemap_items = []

        # helper for sitemap date format
        def get_now_str() -> str:
            return gen_time.strftime("%Y-%m-%d")

        target_site_url = valid_site_url.rstrip("/")

        def make_loc(path: str) -> str:
            full_url = f"{target_site_url}{base_path}{path}"
            return xml_escape(full_url)

        sitemap_items.append(f"""  <url>
    <loc>{make_loc("/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""")

        sitemap_items.append(f"""  <url>
    <loc>{make_loc("/opportunities/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>""")

        for op in opportunities:
            sitemap_items.append(f"""  <url>
    <loc>{make_loc(f"/opportunities/{op.id}/")}</loc>
    <lastmod>{get_now_str()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.6</priority>
  </url>""")

        sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{"\n".join(sitemap_items)}
</urlset>
"""
        with open(os.path.join(temp_build_dir, "sitemap.xml"), "w") as f:
            f.write(sitemap_xml.strip())

        # Atomic replacement of output_dir
        if os.path.exists(output_dir):
            backup_dir = output_dir + f".bak-{uuid.uuid4().hex}"
            try:
                os.rename(output_dir, backup_dir)
                os.rename(temp_build_dir, output_dir)
                shutil.rmtree(backup_dir)
            except Exception:
                # Rollback
                if os.path.exists(backup_dir) and not os.path.exists(output_dir):
                    os.rename(backup_dir, output_dir)
                raise
        else:
            os.rename(temp_build_dir, output_dir)

    except Exception:
        # Cleanup temp directory if something goes wrong
        if os.path.exists(temp_build_dir):
            shutil.rmtree(temp_build_dir)
        raise

    return {
        "opportunities_generated": len(opportunities),
        "total_files": len(opportunities) + 8,
    }
