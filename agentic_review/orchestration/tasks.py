"""
Celery tasks implementing the multi-agent orchestration pipeline.

Agent graph:
                   ┌──────────┐
                   │Orchestrat│
                   └────┬─────┘
           ┌────────────┴────────────┐
      ┌────▼────┐              ┌─────▼────┐
      │ Planner │◄────────────►│ Reasoner │
      └────┬────┘              └──────┬───┘
           └────────────┬────────────┘
                   ┌────▼──────────┐
                   │ Final Critique│
                   └────┬──────────┘
                   ┌────▼────┐
                   │  Writer │
                   └────┬────┘
                   ┌────▼────┐
                   │  Editor │
                   └────┬────┘
                   ┌────▼────┐
                   │Reviewer │ ──→ MATCH → DONE
                   └─────────┘ ──→ MISMATCH → re-route

All agents communicate exclusively via the database (AgentLog /
CritiqueSnapshot).  Every task is idempotent and fully restartable.
"""

import json
import logging
from celery import shared_task, group, chord
import anthropic
from django.conf import settings

logger = logging.getLogger('orchestration')

# ─── Model imports (deferred-safe) ────────────────────────────────────────────
# Imported inline to avoid AppRegistryNotReady during worker startup if needed.


def _get_models():
    from agentic_review.orchestration.models import PipelineRun, AgentLog, CritiqueSnapshot
    return PipelineRun, AgentLog, CritiqueSnapshot


def _anthropic_client():
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2000


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call the Anthropic Messages API and return the text response."""
    client = _anthropic_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def _call_llm_json(system_prompt: str, user_prompt: str, retry_on_error: bool = True) -> dict:
    """
    Call the LLM expecting a JSON response.

    On JSONDecodeError, retries once with an explicit JSON-only suffix.
    If the retry also fails, raises the original JSONDecodeError.
    """
    raw = _call_llm(system_prompt, user_prompt)
    try:
        # Strip markdown fences if model wraps output in ```json ... ```
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        if not retry_on_error:
            raise
        retry_prompt = user_prompt + "\n\nIMPORTANT: respond ONLY in valid JSON, no markdown fences."
        raw2 = _call_llm(system_prompt, retry_prompt)
        text2 = raw2.strip()
        if text2.startswith("```"):
            text2 = text2.split("```", 2)[1]
            if text2.startswith("json"):
                text2 = text2[4:]
            text2 = text2.rsplit("```", 1)[0].strip()
        return json.loads(text2)  # Let caller handle this JSONDecodeError


def _log_agent(pipeline, agent_name, iteration, input_text, output_text, status):
    """Persist an AgentLog entry."""
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    return AgentLog.objects.create(
        pipeline=pipeline,
        agent_name=agent_name,
        iteration=iteration,
        input_text=input_text,
        output_text=output_text,
        status=status,
    )


def _latest_log(pipeline, agent_name):
    """Return the most recent AgentLog for a given agent, or None."""
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    return (
        AgentLog.objects
        .filter(pipeline=pipeline, agent_name=agent_name)
        .order_by('-created_at')
        .first()
    )


def _latest_critique(pipeline):
    """Return the most recent CritiqueSnapshot, or None."""
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    return pipeline.critiques.order_by('-iteration').first()


# ─── Tasks ────────────────────────────────────────────────────────────────────

@shared_task(bind=True, name='orchestration.run_orchestrator')
def run_orchestrator(self, pipeline_run_id: str):
    """
    ORCHESTRATOR agent (entry point).

    Responsibilities:
      1. Marks the pipeline as RUNNING.
      2. Fans out to Planner and Reasoner in parallel using a Celery group.
      3. After both complete (via a chord callback), triggers Final Critique.
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)
    pipeline.status = PipelineRun.Status.RUNNING
    pipeline.save(update_fields=['status'])

    # Create an initial CritiqueSnapshot so Planner / Reasoner have something to write to.
    CritiqueSnapshot.objects.create(pipeline=pipeline, iteration=pipeline.iteration_count)

    input_text = pipeline.task_description
    try:
        # Parallel fan-out: Planner + Reasoner
        parallel_tasks = group(
            run_planner.s(pipeline_run_id),
            run_reasoner.s(pipeline_run_id),
        )
        # chord: run parallel tasks, then fire Final Critique when both done
        workflow = chord(parallel_tasks)(run_final_critique.s(pipeline_run_id))
        _log_agent(
            pipeline, AgentLog.AgentName.ORCHESTRATOR, pipeline.iteration_count,
            input_text, f"Dispatched chord → Planner + Reasoner → Final Critique",
            AgentLog.LogStatus.SUCCESS,
        )
    except Exception as exc:
        _log_agent(
            pipeline, AgentLog.AgentName.ORCHESTRATOR, pipeline.iteration_count,
            input_text, str(exc), AgentLog.LogStatus.FAILED,
        )
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])
        raise


@shared_task(bind=True, name='orchestration.run_planner')
def run_planner(self, pipeline_run_id: str):
    """
    PLANNER agent.

    Produces a structured plan with sub-tasks and success criteria.
    Stores its structural_critique on the current CritiqueSnapshot so the
    Reasoner and Final Critique agents can reference it via the database.

    Expected LLM output schema:
        { plan: str, success_criteria: [str], structural_critique: str }
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    # Enrich prompt with any reviewer mismatch context from the previous iteration.
    reviewer_log = _latest_log(pipeline, AgentLog.AgentName.REVIEWER)
    mismatch_context = ""
    if reviewer_log and reviewer_log.output_text:
        mismatch_context = f"\n\nReviewer mismatch context from previous iteration:\n{reviewer_log.output_text}"

    user_prompt = pipeline.task_description + mismatch_context
    system_prompt = (
        "You are a Strategic Planner. Given the task, produce a structured plan "
        "with sub-tasks and success criteria. "
        "Output JSON: { \"plan\": str, \"success_criteria\": [str], \"structural_critique\": str }"
    )

    try:
        result = _call_llm_json(system_prompt, user_prompt)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS

        # Write planner_critique to the latest CritiqueSnapshot
        snapshot = _latest_critique(pipeline)
        if snapshot:
            snapshot.planner_critique = result.get('structural_critique', '')
            snapshot.save(update_fields=['planner_critique'])

    except (json.JSONDecodeError, Exception) as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        logger.error("Planner failed for pipeline %s: %s", pipeline_run_id, exc)
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])

    _log_agent(
        pipeline, AgentLog.AgentName.PLANNER, pipeline.iteration_count,
        user_prompt, output_text, status
    )


@shared_task(bind=True, name='orchestration.run_reasoner')
def run_reasoner(self, pipeline_run_id: str):
    """
    REASONER agent.

    Validates the logical soundness of the Planner's output by identifying
    gaps and contradictions.  Stores its reasoning_critique on the current
    CritiqueSnapshot.

    Expected LLM output schema:
        { analysis: str, gaps: [str], reasoning_critique: str }
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    planner_log = _latest_log(pipeline, AgentLog.AgentName.PLANNER)
    user_prompt = planner_log.output_text if planner_log else pipeline.task_description

    system_prompt = (
        "You are an Analytical Reasoner. Validate the logical soundness of the given plan. "
        "Identify gaps or contradictions. "
        "Output JSON: { \"analysis\": str, \"gaps\": [str], \"reasoning_critique\": str }"
    )

    try:
        result = _call_llm_json(system_prompt, user_prompt)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS

        # Write reasoner_critique to the latest CritiqueSnapshot
        snapshot = _latest_critique(pipeline)
        if snapshot:
            snapshot.reasoner_critique = result.get('reasoning_critique', '')
            snapshot.save(update_fields=['reasoner_critique'])

    except (json.JSONDecodeError, Exception) as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        logger.error("Reasoner failed for pipeline %s: %s", pipeline_run_id, exc)
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])

    _log_agent(
        pipeline, AgentLog.AgentName.REASONER, pipeline.iteration_count,
        user_prompt, output_text, status
    )


@shared_task(bind=True, name='orchestration.run_final_critique')
def run_final_critique(self, results, pipeline_run_id: str):
    """
    FINAL CRITIQUE agent (synthesis node).

    Merges the Planner's structural critique and the Reasoner's reasoning
    critique into a single gold-standard review checklist.  Triggered as
    the chord callback after both Planner and Reasoner complete.

    The `results` argument is required by Celery chord callbacks (it receives
    the return values of all group tasks) but is not used directly; state is
    read from the database instead.

    Expected LLM output schema:
        { merged_critique: str, checklist: [str] }
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    # Guard: if pipeline already failed, do not continue.
    if pipeline.status == PipelineRun.Status.FAILED:
        return

    snapshot = _latest_critique(pipeline)
    planner_crit = snapshot.planner_critique if snapshot else ""
    reasoner_crit = snapshot.reasoner_critique if snapshot else ""

    user_prompt = (
        f"Planner structural critique:\n{planner_crit}\n\n"
        f"Reasoner reasoning critique:\n{reasoner_crit}"
    )
    system_prompt = (
        "You are a Synthesis Engine. Merge the Planner's structural critique and the "
        "Reasoner's reasoning critique into a single gold-standard review checklist. "
        "Output JSON: { \"merged_critique\": str, \"checklist\": [str] }"
    )

    try:
        result = _call_llm_json(system_prompt, user_prompt)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS

        if snapshot:
            snapshot.merged_critique = result.get('merged_critique', '')
            snapshot.save(update_fields=['merged_critique'])

    except (json.JSONDecodeError, Exception) as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        logger.error("Final Critique failed for pipeline %s: %s", pipeline_run_id, exc)
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])
        _log_agent(
            pipeline, AgentLog.AgentName.CRITIQUE, pipeline.iteration_count,
            user_prompt, output_text, status
        )
        return

    _log_agent(
        pipeline, AgentLog.AgentName.CRITIQUE, pipeline.iteration_count,
        user_prompt, output_text, status
    )

    # Chain to Writer
    run_writer.delay(pipeline_run_id)


@shared_task(bind=True, name='orchestration.run_writer')
def run_writer(self, pipeline_run_id: str):
    """
    WRITER agent.

    Generates a complete, polished content draft that satisfies every item
    on the merged critique checklist.  Output is plain text (no JSON).
    After completion, chains to the Editor for a three-pass refinement.
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    snapshot = _latest_critique(pipeline)
    merged_critique = snapshot.merged_critique if snapshot else ""
    user_prompt = (
        f"Original task:\n{pipeline.task_description}\n\n"
        f"Critique checklist to satisfy:\n{merged_critique}"
    )
    system_prompt = (
        "You are a Content Writer. Write a complete, polished draft that satisfies "
        "every item on the critique checklist. Output the full draft text only, no JSON."
    )

    try:
        output_text = _call_llm(system_prompt, user_prompt)
        status = AgentLog.LogStatus.SUCCESS
    except Exception as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        logger.error("Writer failed for pipeline %s: %s", pipeline_run_id, exc)
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])

    _log_agent(
        pipeline, AgentLog.AgentName.WRITER, pipeline.iteration_count,
        user_prompt, output_text, status
    )

    if status == AgentLog.LogStatus.SUCCESS:
        run_editor.delay(pipeline_run_id)


@shared_task(bind=True, name='orchestration.run_editor')
def run_editor(self, pipeline_run_id: str):
    """
    EDITOR agent (sub-agent of Writer).

    Performs three editorial passes over the Writer's draft:
      1. MODIFY — fix clarity, tone, and accuracy issues
      2. ADD    — insert missing detail or examples
      3. DELETE — remove redundancy or off-topic content

    The Editor's refined output overwrites the latest WRITER AgentLog so that
    the Reviewer always sees the Editor-refined version.  This keeps the
    Writer→Editor loop transparent to downstream agents.
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    writer_log = _latest_log(pipeline, AgentLog.AgentName.WRITER)
    if not writer_log:
        logger.error("Editor: no Writer log found for pipeline %s", pipeline_run_id)
        return

    user_prompt = writer_log.output_text
    system_prompt = (
        "You are a Content Editor. Perform three passes:\n"
        "1. MODIFY — fix clarity, tone, and accuracy issues.\n"
        "2. ADD    — insert missing detail or examples.\n"
        "3. DELETE — remove redundancy or off-topic content.\n"
        "Return only the final refined text."
    )

    try:
        output_text = _call_llm(system_prompt, user_prompt)
        status = AgentLog.LogStatus.SUCCESS

        # Absorb editor output into the Writer log so Reviewer sees the refined draft.
        writer_log.output_text = output_text
        writer_log.save(update_fields=['output_text'])

    except Exception as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        logger.error("Editor failed for pipeline %s: %s", pipeline_run_id, exc)
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])

    _log_agent(
        pipeline, AgentLog.AgentName.EDITOR, pipeline.iteration_count,
        user_prompt, output_text, status
    )

    if status == AgentLog.LogStatus.SUCCESS:
        run_reviewer.delay(pipeline_run_id)


@shared_task(bind=True, name='orchestration.run_reviewer')
def run_reviewer(self, pipeline_run_id: str):
    """
    REVIEWER agent (conditional router).

    Compares the (Editor-refined) Writer draft against the merged critique
    checklist from Final Critique.

    Decision logic:
      MATCH    → pipeline succeeds; final_output is persisted.
      MISMATCH → iteration_count incremented; re-routes to Planner/Reasoner
                 based on issue type, or halts at MAX_ITER.

    Expected LLM output schema:
        {
          "decision": "MATCH" | "MISMATCH",
          "structural_issues": bool,
          "logical_issues": bool,
          "annotations": str
        }
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    writer_log = _latest_log(pipeline, AgentLog.AgentName.WRITER)
    snapshot = _latest_critique(pipeline)
    writer_output = writer_log.output_text if writer_log else ""
    merged_critique = snapshot.merged_critique if snapshot else ""

    user_prompt = (
        f"Draft to review:\n{writer_output}\n\n"
        f"Final Critique checklist:\n{merged_critique}"
    )
    system_prompt = (
        'You are a Quality Reviewer. Compare the draft against the Final Critique checklist. '
        'Output ONLY valid JSON: '
        '{ "decision": "MATCH" | "MISMATCH", "structural_issues": bool, '
        '"logical_issues": bool, "annotations": str }'
    )

    try:
        result = _call_llm_json(system_prompt, user_prompt)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS
    except (json.JSONDecodeError, Exception) as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        logger.error("Reviewer failed for pipeline %s: %s", pipeline_run_id, exc)
        _log_agent(
            pipeline, AgentLog.AgentName.REVIEWER, pipeline.iteration_count,
            user_prompt, output_text, status
        )
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])
        return

    _log_agent(
        pipeline, AgentLog.AgentName.REVIEWER, pipeline.iteration_count,
        user_prompt, output_text, status
    )

    decision = result.get('decision', 'MISMATCH').upper()

    if decision == 'MATCH':
        pipeline.final_output = writer_output
        pipeline.status = PipelineRun.Status.DONE
        pipeline.save(update_fields=['final_output', 'status'])
        logger.info("Pipeline %s completed successfully after %d iteration(s).",
                    pipeline_run_id, pipeline.iteration_count)
        return

    # MISMATCH branch
    pipeline.iteration_count += 1
    pipeline.save(update_fields=['iteration_count'])

    if pipeline.iteration_count >= pipeline.max_iterations:
        pipeline.status = PipelineRun.Status.MAX_ITER
        pipeline.save(update_fields=['status'])
        logger.warning("Pipeline %s hit max iterations (%d).",
                       pipeline_run_id, pipeline.max_iterations)
        return

    # Create a new CritiqueSnapshot for the next iteration
    CritiqueSnapshot.objects.create(pipeline=pipeline, iteration=pipeline.iteration_count)

    structural = result.get('structural_issues', False)
    logical = result.get('logical_issues', False)

    if structural and logical:
        # Both issues: re-run planner + reasoner in parallel, then final critique
        parallel_tasks = group(
            run_planner.s(pipeline_run_id),
            run_reasoner.s(pipeline_run_id),
        )
        chord(parallel_tasks)(run_final_critique.s(pipeline_run_id))
    elif structural:
        chord(group(run_planner.s(pipeline_run_id)))(run_final_critique.s(pipeline_run_id))
    elif logical:
        chord(group(run_reasoner.s(pipeline_run_id)))(run_final_critique.s(pipeline_run_id))
    else:
        # Default: re-run both
        parallel_tasks = group(
            run_planner.s(pipeline_run_id),
            run_reasoner.s(pipeline_run_id),
        )
        chord(parallel_tasks)(run_final_critique.s(pipeline_run_id))
