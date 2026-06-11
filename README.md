# AgentFlow — Multi-Agent Orchestration System

A production-ready Django + Celery + Anthropic multi-agent pipeline that
collaboratively plans, writes, edits, and quality-reviews content through
iterative critique-refinement loops.

---

## Architecture

```
POST /api/pipeline/start/
        │
        ▼
  Orchestrator  ──── chord(group) ────►  Planner  ──┐
                                      ►  Reasoner ──┤
                                                     ▼
                                             Final Critique
                                             (merge + checklist)
                                                     │
                                                  Writer
                                                     │
                                                  Editor  ← 3-pass refine
                                                  (absorbed into Writer log)
                                                     │
                                                 Reviewer
                                              ┌────┴────┐
                                            MATCH    MISMATCH
                                              │          │
                                           status=   iteration++
                                            DONE    ┌───┴───────────────┐
                                                  cap?          re-chord()
                                                    │         (structural → Planner,
                                              status=         logical → Reasoner,
                                             MAX_ITER         or both)
```

### Agent responsibilities

| Agent | Role | LLM output |
|-------|------|-----------|
| **Orchestrator** | Entry point; fans out to Planner + Reasoner | none (dispatch only) |
| **Planner** | Structured plan + structural critique | JSON |
| **Reasoner** | Logic validation + reasoning critique | JSON |
| **Final Critique** | Merges critiques → gold-standard checklist | JSON |
| **Writer** | Polished draft satisfying the checklist | plain text |
| **Editor** | Three-pass edit (Modify / Add / Delete) | plain text |
| **Reviewer** | Compares draft vs checklist → MATCH or MISMATCH | JSON |

---

## Project layout

```
agentic_review/               Django project config
  __init__.py                 ← registers Celery app
  celery.py                   ← Celery app definition
  settings.py                 ← DRF, Celery, Anthropic, logging
  urls.py                     ← /admin  /api/  /  routes

  orchestration/              Multi-agent Django app
    models.py                 ← PipelineRun, AgentLog, CritiqueSnapshot
    tasks.py                  ← 7 Celery agent tasks
    serializers.py            ← DRF serializers
    views.py                  ← 4 REST API views
    urls.py                   ← /api/pipeline/* routes
    template_views.py         ← 3 template-based views
    template_urls.py          ← / /pipeline/new/ /pipeline/<pk>/
    signals.py                ← PipelineRun status transition logger
    apps.py                   ← AppConfig (auto-imports signals)
    migrations/
      0001_initial.py
    management/commands/
      run_pipeline.py         ← CLI (eager/synchronous mode)

templates/orchestration/      Django templates (dark glassmorphic UI)
  base.html
  dashboard.html
  pipeline_create.html
  pipeline_detail.html

requirements.txt
.env.example
```

---

## Setup

### Prerequisites

- Python 3.11+ (tested with conda env `ara`)
- Redis (broker + result backend transport)
- Anthropic API key

### 1 — Clone & environment

```bash
git clone <repo-url>
cd Agentic-Review-Architecture

# activate the ara conda env (or create your own)
conda activate ara
pip install -r requirements.txt
```

### 2 — Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```ini
SECRET_KEY=your-very-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
ANTHROPIC_API_KEY=sk-ant-...        # required
REDIS_URL=redis://localhost:6379/0  # default
```

### 3 — Migrate database

```bash
python manage.py migrate
```

### 4 — Start Redis

```bash
# Option A: native
redis-server

# Option B: Docker (one-liner)
docker run --rm -p 6379:6379 redis:7-alpine
```

### 5 — Start a Celery worker

```bash
celery -A agentic_review worker --loglevel=info --concurrency=4
```

> The worker must be running for the API endpoints to process pipelines
> asynchronously. For the CLI command (`manage.py run_pipeline`), the
> worker is not required — tasks run in-process (eager mode).

### 6 — Start Django

```bash
python manage.py runserver
```

Open **http://localhost:8000/** in your browser.

---

## API reference

### Start a pipeline

```
POST /api/pipeline/start/
Content-Type: application/json

{
    "task_description": "Write a comprehensive guide to Django REST Framework",
    "max_iterations": 7
}
```

Response `201`:
```json
{ "pipeline_id": "uuid", "status": "PENDING" }
```

### Poll status

```
GET /api/pipeline/{id}/status/
```

Response `200`:
```json
{
  "id": "uuid",
  "status": "RUNNING",
  "iteration_count": 2,
  "max_iterations": 7,
  "latest_logs": {
    "ORCHESTRATOR": { "status": "SUCCESS", "iteration": 0, ... },
    "PLANNER":      { "status": "SUCCESS", "iteration": 0, ... },
    ...
  }
}
```

### Full audit log

```
GET /api/pipeline/{id}/logs/
GET /api/pipeline/{id}/logs/?agent_name=PLANNER
```

### Final output

```
GET /api/pipeline/{id}/output/
```

Returns `200` with `{ "final_output": "...", ... }` when `status=DONE`.
Returns `404` while still running.

---

## Frontend (template UI)

| Page | URL |
|------|-----|
| Dashboard | `http://localhost:8000/` |
| New Pipeline | `http://localhost:8000/pipeline/new/` |
| Pipeline Detail (live) | `http://localhost:8000/pipeline/<uuid>/` |

The detail page polls `/api/pipeline/<id>/status/` every 3 seconds and
updates the agent status grid, logs, and critique panels without a page
reload.

---

## CLI — synchronous mode (no Celery/Redis needed)

```bash
python manage.py run_pipeline "Write a guide to async Python" --max-iterations 5
```

Celery tasks execute eagerly in-process. The final output is printed to stdout.

---

## Configuration reference

| Setting | Default | Description |
|---------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required**. Your Claude API key |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker URL |
| `AGENT_MAX_ITERATIONS` | `7` | Default iteration cap per pipeline |
| `CELERY_TASK_TIME_LIMIT` | `600` | Max seconds per Celery task |

---

## Pipeline statuses

| Status | Meaning |
|--------|---------|
| `PENDING` | Created, not yet picked up by worker |
| `RUNNING` | Actively processing |
| `DONE` | Reviewer accepted the draft — `final_output` is set |
| `FAILED` | Unrecoverable error in an agent (check `AgentLog`) |
| `MAX_ITER` | Hit the iteration cap without a MATCH |

---

## Design decisions

- **DB-only inter-agent comms** — every task reads its inputs from `AgentLog`
  / `CritiqueSnapshot` and writes results back. Tasks are fully restartable.
- **Editor absorbs into Writer** — `run_editor` overwrites
  `writer_log.output_text` so the Reviewer always evaluates the
  Editor-refined draft while a single `WRITER` log is maintained.
- **Chord callbacks** — `run_final_critique` accepts `results` as its first
  positional arg (required by Celery chord) but reads state from DB rather
  than relying on in-memory return values.
- **JSON retry** — `_call_llm_json()` strips Markdown fences, then retries
  once with an explicit *"respond ONLY in valid JSON"* suffix before propagating
  the `JSONDecodeError`.
- **Conditional re-routing** — on MISMATCH the Reviewer inspects
  `structural_issues` and `logical_issues` flags to decide whether to re-run
  only Planner, only Reasoner, or both in parallel before Final Critique.
