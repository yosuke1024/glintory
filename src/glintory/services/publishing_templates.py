CSS_CONTENT = """
:root {
  --bg-primary: #0b0f19;
  --bg-secondary: #111827;
  --text-primary: #f3f4f6;
  --text-secondary: #9ca3af;
  --border: #1f2937;
  --accent: #6366f1;
  --accent-hover: #4f46e5;
  --accent-gradient: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
}

body {
  background-color: var(--bg-primary);
  color: var(--text-primary);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  margin: 0;
  padding: 0;
  line-height: 1.5;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem 1rem;
}

header {
  border-bottom: 1px solid var(--border);
  background-color: rgba(17, 24, 39, 0.8);
  backdrop-filter: blur(8px);
  position: sticky;
  top: 0;
  z-index: 10;
}

.nav-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 1rem;
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

INDEX_TEMPLATE = """<!DOCTYPE html>
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
          <p>{{ (op.proposed_solution or '')[:150] }}...</p>
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
"""

LIST_TEMPLATE = """<!DOCTYPE html>
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
"""

DETAIL_TEMPLATE = """<!DOCTYPE html>
<html lang="{% if locale == 'ja' %}ja{% else %}en{% endif %}">
<head>
  <meta charset="UTF-8">
  <title>{{ loc_data.title }} - Glintory</title>
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
    <!-- Language Switcher Route -->
    <div style="display: flex; justify-content: flex-end; margin-top: 1rem; gap: 1rem; font-size: 0.9rem;">
      {% if locale == 'en' %}
        <a href="{{ base_path }}/opportunities/{{ op.id }}/ja/" style="color: var(--accent); text-decoration: none; font-weight: 600;">View in Japanese (日本語)</a>
      {% else %}
        <a href="{{ base_path }}/opportunities/{{ op.id }}/" style="color: var(--accent); text-decoration: none; font-weight: 600;">View in English (English)</a>
      {% endif %}
    </div>

    <!-- Stale Warning -->
    {% if enrichment and llm_is_stale %}
      <div style="background-color: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 0.5rem; padding: 1rem; margin-top: 1rem;">
        <p style="margin: 0; font-size: 0.9rem; color: #f87171; font-weight: 600;">
          {% if locale == 'en' %}
            ⚠️ This AI summary is based on outdated evidence data. (AI-generated content is stale)
          {% else %}
            ⚠️ このAI要約は古い証拠データに基づいている可能性があります。(AI生成コンテンツは最新ではありません)
          {% endif %}
        </p>
      </div>
    {% endif %}

    {% if enrichment %}
      <div style="background-color: rgba(99, 102, 241, 0.05); border: 1px solid var(--accent); border-radius: 0.5rem; padding: 1rem; margin-top: 1rem; margin-bottom: 1rem;">
        <p style="margin: 0; font-size: 0.9rem; color: #818cf8; font-weight: 600;">
          {% if locale == 'en' %}
            ✨ AI-generated brief based on the evidence below.
          {% else %}
            {% if translation_fallback %}
              ✨ 日本語訳はまだ生成されていません。英語版のAI要約を表示しています。
            {% else %}
              ✨ 以下の証拠データに基づくAI生成の日本語参考訳です。
            {% endif %}
          {% endif %}
        </p>
        <p style="margin: 0.25rem 0 0 0; font-size: 0.8rem; color: var(--text-secondary);">
          {% if locale == 'en' %}
            The opportunity score remains rule-based and deterministic.
          {% else %}
            機会スコアはルールベースで決定論的なままです。
          {% endif %}
        </p>
      </div>
    {% endif %}

    <div class="card" style="margin-top: 1rem; padding: 2rem;">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <h1 style="margin: 0; font-size: 2.25rem;">{{ loc_data.title }}</h1>
        <div class="score-value" style="font-size: 3rem;">{{ op.total_score }}</div>
      </div>
      
      <p style="font-size: 1.15rem; color: var(--text-secondary); margin-top: 1.5rem;">{{ loc_data.summary }}</p>
      
      <div class="meta-info" style="margin-top: 2rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
        <div><strong>Status:</strong> {{ op.status }}</div>
        <div><strong>Confidence:</strong> {{ op.confidence }}</div>
        {% if enrichment and enrichment.llm_confidence %}
          <div><strong>AI Confidence:</strong> {{ enrichment.llm_confidence }}</div>
        {% endif %}
        <div><strong>Scoring Version:</strong> {{ op.current_scoring_version or "N/A" }}</div>
        <div><strong>Score Status:</strong> <span class="badge {% if score_is_stale %}badge-accent{% else %}badge-success{% endif %}">{% if score_is_stale %}Stale{% else %}Current{% endif %}</span></div>
        <div><strong>Evidence Score:</strong> {{ op.evidence_score }}</div>
        <div><strong>Feasibility Score:</strong> {{ op.feasibility_score }}</div>
        <div><strong>Penalty Score:</strong> {{ op.penalty_score }}</div>
        <div><strong>Last Scored:</strong> {{ op.last_scored_at | format_datetime }}</div>
      </div>
    </div>

    {% if enrichment %}
      <div class="card" style="margin-top: 1.5rem; padding: 2rem;">
        <h2 style="margin-top: 0; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem;">
          {% if locale == 'en' %}AI Enrichment Analysis{% else %}AIによる付加分析 (AI Enrichment Analysis){% endif %}
        </h2>
        
        {% if loc_data.problem_statement %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Problem Statement{% else %}課題定義 (Problem Statement){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.problem_statement }}</p>
        {% endif %}
        
        {% if loc_data.target_users %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Target Users{% else %}対象ユーザー (Target Users){% endif %}
          </h3>
          <ul style="color: var(--text-secondary); padding-left: 1.25rem;">
            {% for user in loc_data.target_users %}
              <li>{{ user }}</li>
            {% endfor %}
          </ul>
        {% endif %}
        
        {% if loc_data.why_now %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Why Now{% else %}市場背景とタイミング (Why Now){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.why_now }}</p>
        {% endif %}
        
        {% if loc_data.evidence_synthesis %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Evidence Synthesis{% else %}証拠データの総合分析 (Evidence Synthesis){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.evidence_synthesis }}</p>
        {% endif %}
        
        {% if loc_data.build_direction %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Build Direction{% else %}開発方針の推奨 (Build Direction){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.build_direction }}</p>
        {% endif %}
        
        {% if loc_data.risks %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Risks{% else %}リスク分析 (Risks){% endif %}
          </h3>
          <ul style="color: var(--text-secondary); padding-left: 1.25rem;">
            {% for risk in loc_data.risks %}
              <li>{{ risk }}</li>
            {% endfor %}
          </ul>
        {% endif %}
        
        {% if loc_data.tags %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}AI Tags{% else %}AIタグ (AI Tags){% endif %}
          </h3>
          <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.5rem;">
            {% for tag in loc_data.tags %}
              <span class="badge badge-info">{{ tag }}</span>
            {% endfor %}
          </div>
        {% endif %}
      </div>
    {% endif %}

    <div class="section-title">
      {% if locale == 'en' %}Evidence & Signals{% else %}根拠データとシグナル (Evidence & Signals){% endif %}
    </div>
    {% for ev in evidences %}
      <div class="card" style="margin-bottom: 1rem; border-left: 4px solid var(--accent);">
        <div style="display: flex; justify-content: space-between;">
          <h4 style="margin: 0;">
            <a href="{{ ev.url | safe_url }}" target="_blank" rel="noopener" style="color: inherit; text-decoration: none;">{{ ev.title }}</a>
            {% if enrichment and ev.id in enrichment.evidence_refs %}
              <span class="badge badge-success" style="font-size: 0.65rem; margin-left: 0.5rem;">AI Ref</span>
            {% endif %}
          </h4>
          <span class="badge badge-accent">Relevance: {{ ev.relevance_score }}</span>
        </div>
        <p style="font-size: 0.9rem; color: var(--text-secondary); margin-top: 0.5rem;">{{ ev.excerpt }}</p>
        <div class="meta-info" style="margin-top: 0.5rem; font-size: 0.8rem;">
          <span>Source: {{ ev.source_name }} ({{ ev.source_type }})</span>
          <span>Published: {{ ev.published_at | format_datetime }}</span>
        </div>
      </div>
    {% else %}
      <p>
        {% if locale == 'en' %}
          No public evidence items associated with this opportunity.
        {% else %}
          この機会に関連付けられた公開の根拠データはありません。
        {% endif %}
      </p>
    {% endfor %}
  </div>

  <footer class="footer">
    <p>Powered by {% if repo_url %}<a href="{{ repo_url | safe_url }}" target="_blank" rel="noopener">Glintory</a>{% else %}Glintory{% endif %}.</p>
  </footer>
</body>
</html>
"""
