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
        <a href="{{ base_path }}/diagnostics.html">Diagnostics</a>
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
            <h4 class="card-title">
              {% if op.title_ja %}
                {{ op.title_ja }}
              {% else %}
                日本語要約はまだ生成されていません。
              {% endif %}
            </h4>
            <div class="score-value">{{ op.total_score }}</div>
          </div>
          <p>
            {% if op.summary_ja %}
              {{ op.summary_ja[:150] }}...
            {% else %}
              英語版を確認してください。
            {% endif %}
          </p>
          <div class="meta-info">
            <span>Confidence: {{ op.confidence }}</span>
            <span>Status: {{ op.status }}</span>
          </div>
        </a>
      {% else %}
        <div style="grid-column: 1 / -1; background-color: rgba(99, 102, 241, 0.05); border: 1px solid var(--border); border-radius: 0.5rem; padding: 2rem; text-align: center;">
          <p style="margin: 0; font-size: 1.1rem; font-weight: 600; color: var(--text-secondary);">
            現在、公開条件を満たしたOpportunityはありません。
          </p>
          <p style="margin: 0.5rem 0 0 0; font-size: 0.95rem; color: var(--text-secondary);">
            調査中のResearch Candidateを確認してください。
          </p>
        </div>
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
  <style>
    .tab-btn {
      background: none;
      border: none;
      color: var(--text-secondary);
      cursor: pointer;
      font-weight: 600;
      padding: 0.5rem 1rem;
      border-bottom: 2px solid transparent;
      transition: color 0.2s, border-color 0.2s;
    }
    .tab-btn.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
    }
  </style>
</head>
<body>
  <header>
    <div class="nav-container">
      <a href="{{ base_path }}/" class="logo">Glintory</a>
      <nav class="nav-links">
        <a href="{{ base_path }}/">Dashboard</a>
        <a href="{{ base_path }}/opportunities/">Opportunities</a>
        <a href="{{ base_path }}/diagnostics.html">Diagnostics</a>
      </nav>
    </div>
  </header>

  <div class="container">
    <div class="section-title">All Scored Opportunities</div>
    
    <div style="margin-bottom: 1.5rem; display: flex; gap: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem;">
      <button class="tab-btn" id="btn-pub" onclick="switchTab('published')">Published Opportunities (<span id="count-published">0</span>)</button>
      <button class="tab-btn" id="btn-res" onclick="switchTab('research')">Research Candidates (<span id="count-research">0</span>)</button>
      <button class="tab-btn" id="btn-rej" onclick="switchTab('rejected')">Rejected Candidates (<span id="count-rejected">0</span>)</button>
      <button class="tab-btn" id="btn-all" onclick="switchTab('all')">All</button>
    </div>

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
          <tr data-status="{{ op_data.stage }}">
            <td>
              <a href="{{ base_path }}/opportunities/{{ op_data.op.id }}/" style="color: inherit; font-weight: 600; text-decoration: none;">
                {% if op_data.op.title_ja %}
                  {{ op_data.op.title_ja }}
                {% else %}
                  日本語要約はまだ生成されていません。
                {% endif %}
              </a>
            </td>
            <td><span class="score-value" style="font-size: 1.25rem;">{{ op_data.op.total_score }}</span></td>
            <td>{{ op_data.op.confidence }}</td>
            <td>
              <span class="badge {% if op_data.stage == 'published' %}badge-success{% elif op_data.stage == 'research' %}badge-info{% else %}badge-accent{% endif %}">
                {{ op_data.stage }}
              </span>
            </td>
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

  <script>
    function switchTab(status) {
      const rows = document.querySelectorAll('tbody tr');
      rows.forEach(row => {
        const rowStatus = row.getAttribute('data-status');
        if (status === 'all' || rowStatus === status) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      });
      
      const buttons = document.querySelectorAll('.tab-btn');
      buttons.forEach(btn => btn.classList.remove('active'));
      
      if (status === 'published') document.getElementById('btn-pub').classList.add('active');
      else if (status === 'research') document.getElementById('btn-res').classList.add('active');
      else if (status === 'rejected') document.getElementById('btn-rej').classList.add('active');
      else if (status === 'all') document.getElementById('btn-all').classList.add('active');
    }

    document.addEventListener('DOMContentLoaded', () => {
      const rows = document.querySelectorAll('tbody tr');
      let pub = 0, res = 0, rej = 0;
      rows.forEach(row => {
        const status = row.getAttribute('data-status');
        if (status === 'published') pub++;
        else if (status === 'research') res++;
        else if (status === 'rejected') rej++;
      });
      document.getElementById('count-published').textContent = pub;
      document.getElementById('count-research').textContent = res;
      document.getElementById('count-rejected').textContent = rej;
      
      if (pub > 0) {
        switchTab('published');
      } else if (res > 0) {
        switchTab('research');
      } else {
        switchTab('all');
      }
    });
  </script>

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
  <title>{% if locale == 'ja' and translation_fallback %}{{ op.title_en or op.title }}{% else %}{{ loc_data.title }}{% endif %} - Glintory</title>
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
        <a href="{{ base_path }}/diagnostics.html">Diagnostics</a>
      </nav>
    </div>
  </header>

  <div class="container">
    <!-- Language Switcher Route -->
    <div style="display: flex; justify-content: flex-end; margin-top: 1rem; gap: 1rem; font-size: 0.9rem;">
      {% if locale == 'en' %}
        <a href="{{ base_path }}/opportunities/{{ op.id }}/" style="color: var(--accent); text-decoration: none; font-weight: 600;">View in Japanese (日本語)</a>
      {% else %}
        <a href="{{ base_path }}/opportunities/{{ op.id }}/en/" style="color: var(--accent); text-decoration: none; font-weight: 600;">View in English (English)</a>
      {% endif %}
    </div>

    <!-- Japanese Localization Missing Warning -->
    {% if locale == 'ja' and translation_fallback %}
      <div style="background-color: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; border-radius: 0.5rem; padding: 1rem; margin-top: 1rem; margin-bottom: 1rem;">
        <p style="margin: 0; font-size: 1rem; color: #f87171; font-weight: 600;">
          ⚠️ 日本語要約はまだ生成されていません。
        </p>
        <p style="margin: 0.5rem 0 0 0; font-size: 0.9rem; color: var(--text-secondary);">
          この案件の日本語翻訳データは現在利用できません。恐れ入りますが、以下のリンクより英語版をご確認ください。
        </p>
        <div style="margin-top: 1rem;">
          <a href="{{ base_path }}/opportunities/{{ op.id }}/en/" class="btn-primary" style="display: inline-block; padding: 0.5rem 1rem; font-size: 0.85rem; text-decoration: none; border-radius: 0.25rem;">English Version (英語版) を見る</a>
        </div>
      </div>
    {% endif %}

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

    {% if enrichment and not (locale == 'ja' and translation_fallback) %}
      <div style="background-color: rgba(99, 102, 241, 0.05); border: 1px solid var(--accent); border-radius: 0.5rem; padding: 1rem; margin-top: 1rem; margin-bottom: 1rem;">
        <p style="margin: 0; font-size: 0.9rem; color: #818cf8; font-weight: 600;">
          {% if locale == 'en' %}
            ✨ AI-generated brief based on the evidence below.
          {% else %}
            ✨ 以下の証拠データに基づくAI生成の日本語参考訳です。
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
        <h1 style="margin: 0; font-size: 2.25rem;">
          {% if locale == 'ja' and translation_fallback %}
            日本語要約はまだ生成されていません。
          {% else %}
            {{ loc_data.title }}
          {% endif %}
        </h1>
        <div class="score-value" style="font-size: 3rem;">{{ op.total_score }}</div>
      </div>
      
      <p style="font-size: 1.15rem; color: var(--text-secondary); margin-top: 1.5rem;">
        {% if locale == 'ja' and translation_fallback %}
          日本語の要約情報はまだ生成されていません。英語版を確認してください。
        {% else %}
          {{ loc_data.summary }}
        {% endif %}
      </p>
      
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

    {% if enrichment and not (locale == 'ja' and translation_fallback) %}
      <div class="card" style="margin-top: 1.5rem; padding: 2rem;">
        <h2 style="margin-top: 0; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem;">
          {% if locale == 'en' %}AI Enrichment Analysis{% else %}AIによる付加分析 (AI Enrichment Analysis){% endif %}
        </h2>
        
        {% if loc_data.problem %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Problem Statement{% else %}課題定義 (Problem Statement){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.problem }}</p>
        {% endif %}
        
        {% if loc_data.target_user %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Target Users{% else %}対象ユーザー (Target Users){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.target_user }}</p>
        {% endif %}
        
        {% if loc_data.current_workaround %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Current Workarounds{% else %}現在の回避策 (Current Workarounds){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.current_workaround }}</p>
        {% endif %}
        
        {% if loc_data.existing_solution_gap %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Gap in Existing Solutions{% else %}既存手段の不足理由 (Gap in Existing Solutions){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.existing_solution_gap }}</p>
        {% endif %}
        
        {% if loc_data.mvp_direction %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}MVP Direction{% else %}MVP開発の方向性 (MVP Direction){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.mvp_direction }}</p>
        {% endif %}

        {% if loc_data.why_selected %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Why Selected{% else %}選定理由 (Why Selected){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.why_selected }}</p>
        {% endif %}
        
        {% if loc_data.risks %}
          <h3 style="margin-top: 1.5rem; font-size: 1.25rem;">
            {% if locale == 'en' %}Risks{% else %}想定リスク (Risks){% endif %}
          </h3>
          <p style="color: var(--text-secondary);">{{ loc_data.risks }}</p>
        {% endif %}
      </div>
    {% endif %}

    <div class="section-title">
      {% if locale == 'en' %}Evidence & Signals{% else %}根拠データとシグナル (Evidence & Signals){% endif %}
    </div>
    {% for ev in evidences %}
      <div class="card" style="margin-bottom: 1rem; border-left: 4px solid var(--accent);">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <h4 style="margin: 0;">
            <a href="{{ ev.url | safe_url }}" target="_blank" rel="noopener" style="color: inherit; text-decoration: none;">{{ ev.title }}</a>
            {% if enrichment and ev.id in enrichment.evidence_refs %}
              <span class="badge badge-success" style="font-size: 0.65rem; margin-left: 0.5rem;">AI Ref</span>
            {% endif %}
          </h4>
          <div style="display: flex; gap: 0.5rem;">
            <span class="badge badge-info">役割: {{ ev.signal_role }}</span>
            <span class="badge badge-accent">Relevance: {{ ev.relevance_score }}</span>
          </div>
        </div>
        
        <!-- Evidence summaries -->
        <div style="margin-top: 0.75rem; padding: 0.5rem; background-color: rgba(99, 102, 241, 0.03); border-radius: 0.25rem;">
          <strong style="font-size: 0.85rem; color: var(--accent);">
            {% if locale == 'ja' %}証拠要約:{% else %}Summary:{% endif %}
          </strong>
          <span style="font-size: 0.9rem; color: var(--text-primary);">
            {% if locale == 'ja' %}
              {{ ev.summary_ja or '（要約未生成）' }}
            {% else %}
              {{ ev.summary_en or 'No summary' }}
            {% endif %}
          </span>
        </div>

        <!-- Collapsible original excerpt -->
        <details style="margin-top: 0.75rem; font-size: 0.85rem; color: var(--text-secondary);">
          <summary style="cursor: pointer; font-weight: 600; outline: none; user-select: none;">
            {% if locale == 'ja' %}原文を表示 (Original Excerpt){% else %}Show Original Excerpt{% endif %}
          </summary>
          <p style="margin-top: 0.5rem; padding: 0.5rem; background-color: rgba(0, 0, 0, 0.2); border-radius: 0.25rem; white-space: pre-wrap; font-family: monospace;">{{ ev.excerpt }}</p>
        </details>

        <div class="meta-info" style="margin-top: 0.75rem; font-size: 0.8rem;">
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

DIAGNOSTICS_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Glintory - Diagnostics</title>
  <link rel="stylesheet" href="{{ base_path }}/assets/app.css">
</head>
<body>
  <header>
    <div class="nav-container">
      <a href="{{ base_path }}/" class="logo">Glintory</a>
      <nav class="nav-links">
        <a href="{{ base_path }}/">Dashboard</a>
        <a href="{{ base_path }}/opportunities/">Opportunities</a>
        <a href="{{ base_path }}/diagnostics.html">Diagnostics</a>
      </nav>
    </div>
  </header>
  
  <main class="container">
    <h1>Collector &amp; Pipeline Diagnostics</h1>
    <p>ソース収集ログとシステム診断情報です。</p>

    <!-- Current Snapshot -->
    <h2 style="margin-top: 2rem;">Current Snapshot</h2>
    <div class="grid" style="margin-bottom: 2rem; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Current Published</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #34d399;">{{ global_stats.current_published }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">公開中の案件数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Current Research</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #60a5fa;">{{ global_stats.current_research }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">調査中の案件数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Current Rejected</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #f87171;">{{ global_stats.current_rejected }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">却下された案件数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Enrichment Pending</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #fbbf24;">{{ global_stats.current_enrichment_pending }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">付加情報生成待ち</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Discovery Leads</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: var(--accent);">{{ global_stats.current_discovery_leads }} ({{ global_stats.verified_discovery_leads }} verified)</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">探索リード数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Active Candidates</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #e879f9;">{{ global_stats.current_active_candidates }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">アクティブ候補数</p>
      </div>
    </div>

    <!-- Identity Verification Check -->
    <div style="margin-bottom: 2rem; padding: 0.75rem; border-radius: 0.25rem; font-size: 0.9rem; background-color: rgba(52, 211, 153, 0.1); border: 1px solid #34d399; color: #34d399; font-weight: bold;">
      ✓ Snapshot Identity Check: current_total ({{ global_stats.current_total }}) = published ({{ global_stats.current_published }}) + research ({{ global_stats.current_research }}) + rejected ({{ global_stats.current_rejected }}) + pending ({{ global_stats.current_enrichment_pending }})
    </div>

    <!-- Historical Stats -->
    <h2>Historical Metrics</h2>
    <div class="grid" style="margin-bottom: 2rem; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem;">
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Historical Gate Passed</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #34d399;">{{ global_stats.historical_gate_passed }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">過去ゲート通過回数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Historical Gate Rejected</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #f87171;">{{ global_stats.historical_gate_rejected }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">過去ゲート却下回数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Historical Analysis Runs</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #60a5fa;">{{ global_stats.historical_analysis_runs }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">過去クラスタ分析回数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Discovery Reports Processed</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #a78bfa;">{{ global_stats.discovery_reports_processed }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">処理済みレポート数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Discovery URLs Extracted</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #fbbf24;">{{ global_stats.discovery_urls_extracted }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">抽出一次情報URL数</p>
      </div>
      <div class="card" style="padding: 1rem;">
        <h4 style="margin:0; font-size:0.9rem;">Primary Sources Dispatched</h4>
        <div class="score-value" style="font-size: 2rem; margin-top: 0.5rem; color: #e879f9;">{{ global_stats.primary_sources_dispatched }}</div>
        <p style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.25rem;">配信済み一次クエリ数</p>
      </div>
    </div>

    <!-- Fine-grained Diagnostic Metrics -->
    <h3>Quality & Evidence Classification Details</h3>
    <div class="grid" style="margin-bottom: 2rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem;">
      <div class="card" style="padding: 1.25rem;">
        <h4 style="margin-top:0;">Evidence Distribution</h4>
        <ul style="padding-left:1.25rem; font-size:0.9rem; line-height:1.6;">
          <li><strong>Demand-only candidates:</strong> {{ global_stats.demand_only_count }}</li>
          <li><strong>Multi-evidence candidates:</strong> {{ global_stats.multi_evidence_count }}</li>
          <li><strong>Strong single-demand (Condition B):</strong> {{ global_stats.strong_single_demand_count }}</li>
        </ul>
      </div>
      <div class="card" style="padding: 1.25rem;">
        <h4 style="margin-top:0;">Rejection Reasons Breakdown</h4>
        <ul style="padding-left:1.25rem; font-size:0.9rem; line-height:1.6;">
          <li><strong>Supply-only (No demand):</strong> {{ global_stats.supply_only_rejected_count }}</li>
          <li><strong>Single Show HN:</strong> {{ global_stats.single_show_hn_rejected_count }}</li>
          <li><strong>Feasibility Constraint:</strong> {{ global_stats.explicit_feasibility_rejected_count }}</li>
        </ul>
      </div>
      <div class="card" style="padding: 1.25rem;">
        <h4 style="margin-top:0;">Clustering & Dedup Diagnostics</h4>
        <ul style="padding-left:1.25rem; font-size:0.9rem; line-height:1.6;">
          <li><strong>Average signals per cluster:</strong> {{ global_stats.average_signals_per_cluster }}</li>
          <li><strong>Singleton clusters:</strong> {{ global_stats.singleton_cluster_count }}</li>
          <li><strong>Cross-source clusters:</strong> {{ global_stats.cross_source_cluster_count }}</li>
          <li><strong>Duplicate evidence removed:</strong> {{ global_stats.duplicate_evidence_removed_count }} signals</li>
        </ul>
      </div>
    </div>

    <!-- Missing Facets & Gate Reasons tables -->
    <div class="grid" style="margin-bottom: 2rem; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
      <div class="card" style="padding: 1.25rem;">
        <h3 style="margin-top:0;">Missing Facets Summary (Completeness Gaps)</h3>
        <p style="font-size:0.85rem; color:var(--text-secondary);">Gaps in the structural completeness of opportunities</p>
        <table style="font-size: 0.9rem;">
          <thead>
            <tr>
              <th>Structural Element</th>
              <th>Missing Count</th>
            </tr>
          </thead>
          <tbody>
            {% for facet_key, m_count in global_stats.missing_facets_summary.items() %}
            <tr>
              <td style="font-weight:600; text-transform:capitalize;">{{ facet_key }}</td>
              <td><span style="color:#f87171; font-weight:bold;">{{ m_count }}</span></td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>

      <div class="card" style="padding: 1.25rem;">
        <h3 style="margin-top:0;">Gate Reasons Distribution</h3>
        <p style="font-size:0.85rem; color:var(--text-secondary);">Breakdown of opportunities by decision outcomes</p>
        <table style="font-size: 0.9rem;">
          <thead>
            <tr>
              <th>Decision Reason</th>
              <th>Count</th>
            </tr>
          </thead>
          <tbody>
            {% for reason_str, r_count in global_stats.gate_reason_counts.items() %}
            <tr>
              <td style="font-size:0.85rem; max-width:280px; overflow:hidden; text-overflow:ellipsis;">{{ reason_str }}</td>
              <td style="font-weight:bold;">{{ r_count }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Pipeline Stats by Source Type -->
    <h2>Source Type Pipeline Audits</h2>
    <div class="table-container" style="overflow-x: auto; margin-top: 1rem; margin-bottom: 2rem;">
      <table style="width: 100%; border-collapse: collapse; text-align: left; background-color: var(--bg-secondary); border: 1px solid var(--border); border-radius: 0.5rem;">
        <thead>
          <tr style="border-bottom: 2px solid var(--border); font-weight: bold; background-color: rgba(255,255,255,0.02); font-size: 0.85rem;">
            <th style="padding: 0.75rem;">Source Type</th>
            <th style="padding: 0.75rem;">Enabled Sources</th>
            <th style="padding: 0.75rem;">Last Run</th>
            <th style="padding: 0.75rem;">Collection Runs</th>
            <th style="padding: 0.75rem;">Fetched</th>
            <th style="padding: 0.75rem;">Inserted</th>
            <th style="padding: 0.75rem;">Updated</th>
            <th style="padding: 0.75rem;">Persisted</th>
            <th style="padding: 0.75rem;">Skipped</th>
            <th style="padding: 0.75rem;">Failed Runs</th>
            <th style="padding: 0.75rem;">Signals Submitted to Analysis</th>
            <th style="padding: 0.75rem;">Candidate Opportunities</th>
            <th style="padding: 0.75rem;">Gate Passed</th>
            <th style="padding: 0.75rem;">Gate Rejected</th>
            <th style="padding: 0.75rem;">Scored</th>
            <th style="padding: 0.75rem;">Enriched</th>
            <th style="padding: 0.75rem;">Published</th>
            <th style="padding: 0.75rem;">Evidence Used by Published Opportunities</th>
          </tr>
        </thead>
        <tbody>
          {% for stype in ["github", "hackernews", "rss"] %}
            {% set stats = pipeline_stats[stype] %}
            <tr style="border-bottom: 1px solid var(--border); font-size: 0.85rem;">
              <td style="padding: 0.75rem; font-weight: 600; text-transform: uppercase; color: var(--accent);">{{ stype }}</td>
              <td style="padding: 0.75rem;">{{ stats.enabled_sources }}</td>
              <td style="padding: 0.75rem; font-size: 0.75rem;">{{ stats.last_run | format_datetime }}</td>
              <td style="padding: 0.75rem;">{{ stats.collection_runs }}</td>
              <td style="padding: 0.75rem;">{{ stats.fetched }}</td>
              <td style="padding: 0.75rem;">{{ stats.inserted }}</td>
              <td style="padding: 0.75rem;">{{ stats.updated }}</td>
              <td style="padding: 0.75rem;">{{ stats.persisted }}</td>
              <td style="padding: 0.75rem;">{{ stats.skipped }}</td>
              <td style="padding: 0.75rem;">
                <span style="color: {% if stats.failed > 0 %}#f87171{% else %}inherit{% endif %};">
                  {{ stats.failed }}
                </span>
              </td>
              <td style="padding: 0.75rem;">{{ stats.signals_analyzed }}</td>
              <td style="padding: 0.75rem;">{{ stats.candidates }}</td>
              <td style="padding: 0.75rem;">{{ stats.gate_passed }}</td>
              <td style="padding: 0.75rem;">{{ stats.gate_rejected }}</td>
              <td style="padding: 0.75rem;">{{ stats.scored }}</td>
              <td style="padding: 0.75rem;">{{ stats.enriched }}</td>
              <td style="padding: 0.75rem;">{{ stats.published }}</td>
              <td style="padding: 0.75rem;">{{ stats.evidence_used }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <!-- Analysis Executions -->
    <h2 style="margin-top: 2rem;">Recent Analysis Executions</h2>
    <div class="table-container" style="overflow-x: auto; margin-top: 1rem; margin-bottom: 2rem;">
      <table style="width: 100%; border-collapse: collapse; text-align: left; background-color: var(--bg-secondary); border: 1px solid var(--border); border-radius: 0.5rem;">
        <thead>
          <tr style="border-bottom: 2px solid var(--border); font-weight: bold; background-color: rgba(255,255,255,0.02); font-size: 0.85rem;">
            <th style="padding: 1rem;">Run Time</th>
            <th style="padding: 1rem;">Status</th>
            <th style="padding: 1rem;">Submitted Signals</th>
            <th style="padding: 1rem;">Created Candidates</th>
            <th style="padding: 1rem;">Updated Candidates</th>
            <th style="padding: 1rem;">Gate Passed</th>
            <th style="padding: 1rem;">Gate Rejected</th>
          </tr>
        </thead>
        <tbody>
          {% for run in analysis_runs %}
            <tr style="border-bottom: 1px solid var(--border); font-size: 0.85rem;">
              <td style="padding: 1rem; color: var(--text-secondary);">{{ run.started_at | format_datetime }}</td>
              <td style="padding: 1rem;">
                <span class="status-badge" style="
                  padding: 0.25rem 0.5rem;
                  border-radius: 0.25rem;
                  font-size: 0.85rem;
                  font-weight: 600;
                  background-color: {% if run.status == 'succeeded' %}#065f46{% else %}#991b1b{% endif %};
                  color: #fff;
                ">{{ run.status }}</span>
              </td>
              <td style="padding: 1rem;">{{ run.submitted_signal_count }}</td>
              <td style="padding: 1rem;">{{ run.created_candidate_count }}</td>
              <td style="padding: 1rem;">{{ run.updated_candidate_count }}</td>
              <td style="padding: 1rem;">{{ run.gate_passed_count }}</td>
              <td style="padding: 1rem;">{{ run.gate_rejected_count }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <!-- Scoring Executions -->
    <h2>Recent Scoring Executions</h2>
    <div class="table-container" style="overflow-x: auto; margin-top: 1rem; margin-bottom: 2rem;">
      <table style="width: 100%; border-collapse: collapse; text-align: left; background-color: var(--bg-secondary); border: 1px solid var(--border); border-radius: 0.5rem;">
        <thead>
          <tr style="border-bottom: 2px solid var(--border); font-weight: bold; background-color: rgba(255,255,255,0.02); font-size: 0.85rem;">
            <th style="padding: 1rem;">Run Time</th>
            <th style="padding: 1rem;">Status</th>
            <th style="padding: 1rem;">Analyzed</th>
            <th style="padding: 1rem;">Scored</th>
            <th style="padding: 1rem;">Unchanged</th>
          </tr>
        </thead>
        <tbody>
          {% for run in scoring_runs %}
            <tr style="border-bottom: 1px solid var(--border); font-size: 0.85rem;">
              <td style="padding: 1rem; color: var(--text-secondary);">{{ run.started_at | format_datetime }}</td>
              <td style="padding: 1rem;">
                <span class="status-badge" style="
                  padding: 0.25rem 0.5rem;
                  border-radius: 0.25rem;
                  font-size: 0.85rem;
                  font-weight: 600;
                  background-color: {% if run.status == 'succeeded' %}#065f46{% else %}#991b1b{% endif %};
                  color: #fff;
                ">{{ run.status }}</span>
              </td>
              <td style="padding: 1rem;">{{ run.analyzed_count }}</td>
              <td style="padding: 1rem;">{{ run.scored_count }}</td>
              <td style="padding: 1rem;">{{ run.unchanged_count }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <!-- Publishing Executions -->
    <h2>Recent Publishing Executions</h2>
    <div class="table-container" style="overflow-x: auto; margin-top: 1rem; margin-bottom: 2rem;">
      <table style="width: 100%; border-collapse: collapse; text-align: left; background-color: var(--bg-secondary); border: 1px solid var(--border); border-radius: 0.5rem;">
        <thead>
          <tr style="border-bottom: 2px solid var(--border); font-weight: bold; background-color: rgba(255,255,255,0.02); font-size: 0.85rem;">
            <th style="padding: 1rem;">Run Time</th>
            <th style="padding: 1rem;">Status</th>
            <th style="padding: 1rem;">Published Opportunities</th>
            <th style="padding: 1rem;">JuryPress Ready</th>
            <th style="padding: 1rem;">Dataset Content Hash</th>
          </tr>
        </thead>
        <tbody>
          {% for run in publishing_runs %}
            <tr style="border-bottom: 1px solid var(--border); font-size: 0.85rem;">
              <td style="padding: 1rem; color: var(--text-secondary);">{{ run.started_at | format_datetime }}</td>
              <td style="padding: 1rem;">
                <span class="status-badge" style="
                  padding: 0.25rem 0.5rem;
                  border-radius: 0.25rem;
                  font-size: 0.85rem;
                  font-weight: 600;
                  background-color: {% if run.status == 'succeeded' %}#065f46{% else %}#991b1b{% endif %};
                  color: #fff;
                ">{{ run.status }}</span>
              </td>
              <td style="padding: 1rem;">{{ run.published_count }}</td>
              <td style="padding: 1rem;">{{ run.jurypress_ready_count }}</td>
              <td style="padding: 1rem; font-family: monospace;">{{ run.dataset_content_hash[:8] if run.dataset_content_hash else '-' }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <!-- Recent Collector Runs -->
    <h2>Recent Collector Executions</h2>
    <div class="table-container" style="overflow-x: auto; margin-top: 1rem;">
      <table style="width: 100%; border-collapse: collapse; text-align: left; background-color: var(--bg-secondary); border: 1px solid var(--border); border-radius: 0.5rem;">
        <thead>
          <tr style="border-bottom: 2px solid var(--border); font-weight: bold; background-color: rgba(255,255,255,0.02);">
            <th style="padding: 1rem;">Source</th>
            <th style="padding: 1rem;">Status</th>
            <th style="padding: 1rem;">Fetched</th>
            <th style="padding: 1rem;">Persisted</th>
            <th style="padding: 1rem;">Skipped</th>
            <th style="padding: 1rem;">Errors</th>
            <th style="padding: 1rem;">Run Time</th>
            <th style="padding: 1rem;">Error Detail</th>
          </tr>
        </thead>
        <tbody>
          {% for run in diagnostics_data %}
            <tr style="border-bottom: 1px solid var(--border);">
              <td style="padding: 1rem; font-weight: 600;">{{ run.source_name }}</td>
              <td style="padding: 1rem;">
                <span class="status-badge" style="
                  padding: 0.25rem 0.5rem;
                  border-radius: 0.25rem;
                  font-size: 0.85rem;
                  font-weight: 600;
                  background-color: {% if run.status == 'succeeded' %}#065f46{% elif run.status == 'partial' %}#92400e{% else %}#991b1b{% endif %};
                  color: #fff;
                ">{{ run.status }}</span>
              </td>
              <td style="padding: 1rem;">{{ run.fetched_count }}</td>
              <td style="padding: 1rem;">{{ run.inserted_count + run.updated_count }}</td>
              <td style="padding: 1rem;">{{ run.skipped_count }}</td>
              <td style="padding: 1rem;">{{ run.error_count }}</td>
              <td style="padding: 1rem; font-size: 0.85rem; color: var(--text-secondary);">
                {{ run.started_at | format_datetime }}
              </td>
              <td style="padding: 1rem; font-size: 0.85rem; color: #f87171; max-width: 300px; word-wrap: break-word;">
                {% if run.error_type %}
                  <strong>{{ run.error_type }}</strong>: {{ run.sanitized_error_message }}
                {% else %}
                  -
                {% endif %}
              </td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </main>
</body>
</html>
"""
