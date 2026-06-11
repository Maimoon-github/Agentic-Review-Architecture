# Multi-Agent Orchestration System

A Django + Celery + Anthropic multi-agent pipeline for collaborative content generation with iterative critique-refinement loops.

## Architecture

```
Orchestrator
    ├── Planner ──────────────────────────┐
    │   (structured plan + critique)      │
    └── Reasoner ─────────────────────────┤
        (logic validation + critique)     │
                                          ▼
                               Final Critique (merge)
                                          │
                                       Writer
                                          │
                                       Editor  ←── 3-pass: Modify / Add / Delete
                                          │
                                       Reviewer
                                    ┌─────┴──────┐
                                  MATCH        MISMATCH
                                    │              │
                                  DONE      re-route → Planner/Reasoner
```

## Setup

### 1. Create a `.env` file

```bash
cp .env.example .env
# Edit ANTHROPIC_API_KEY, SECRET_KEY, REDIS_URL
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run migrations

```bash
python manage.py migrate
```

### 4. Start Redis

```bash
redis-server  # or via Docker: docker run -p 6379:6379 redis:alpine
```

### 5. Start Celery worker

```bash
celery -A agentic_review worker --loglevel=info --concurrency=4
```

### 6. Start Django dev server

```bash
python manage.py runserver
```

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/api/pipeline/start/` | Start a new pipeline |
| `GET` | `/api/pipeline/{id}/status/` | Poll status + latest per-agent logs |
| `GET` | `/api/pipeline/{id}/logs/` | Full audit log (filterable by `?agent_name=PLANNER`) |
| `GET` | `/api/pipeline/{id}/output/` | Final output (404 until DONE) |

### Start a pipeline

```bash
curl -X POST http://localhost:8000/api/pipeline/start/ \
  -H "Content-Type: application/json" \
  -d '{"task_description": "Write a comprehensive guide to Django REST Framework", "max_iterations": 5}'
```

### Poll the status

```bash
curl http://localhost:8000/api/pipeline/{pipeline_id}/status/
```

## Management Command (Synchronous / CLI)

Run a pipeline eagerly (no Redis/worker needed):

```bash
python manage.py run_pipeline "Write a guide to Python async programming" --max-iterations 3
```

## Models

| Model | Purpose |
|-------|---------|
| `PipelineRun` | Top-level run: status, iteration count, final output |
| `AgentLog` | Per-agent invocation record: input, output, success/failure |
| `CritiqueSnapshot` | Per-iteration merged critique from Planner + Reasoner |

## Pipeline Statuses

| Status | Meaning |
|--------|---------|
| `PENDING` | Created, not yet started |
| `RUNNING` | Actively processing |
| `DONE` | Reviewer accepted the draft |
| `FAILED` | Unrecoverable error in an agent |
| `MAX_ITER` | Hit the iteration cap without MATCH |

## Agent Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required. Claude API key |
| `AGENT_MAX_ITERATIONS` | 7 | Per-pipeline default max iterations |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis URL |

## Project Structure

```
agentic_review/          Django project config
  ├── __init__.py        Celery app registration
  ├── celery.py          Celery app definition
  ├── settings.py        Django settings
  └── urls.py            Root URL conf

orchestration/           Multi-agent app
  ├── models.py          PipelineRun, AgentLog, CritiqueSnapshot
  ├── tasks.py           7 Celery agent tasks
  ├── serializers.py     DRF serializers
  ├── views.py           4 API views
  ├── urls.py            App-level URL routes
  ├── signals.py         Status transition logging
  ├── apps.py            AppConfig (signals import)
  └── management/
      └── commands/
          └── run_pipeline.py  CLI command
```
