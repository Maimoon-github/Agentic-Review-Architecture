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
from google import genai
from google.genai import types
from typing import Type
from pydantic import BaseModel, ValidationError
from celery import shared_task, group, chord
from django.conf import settings
from agentic_review.orchestration.schemas import (
    PlanOutput, ReasonerOutput, FinalCritiqueOutput, 
    WriterOutput, EditorOutput, ReviewerOutput
)


logger = logging.getLogger('orchestration')

# ─── Model imports (deferred-safe) ────────────────────────────────────────────
# Imported inline to avoid AppRegistryNotReady during worker startup if needed.


def _get_models():
    from agentic_review.orchestration.models import PipelineRun, AgentLog, CritiqueSnapshot
    return PipelineRun, AgentLog, CritiqueSnapshot


MODEL = "gemini-2.0-flash"
MAX_TOKENS = 2000



# ─── Helpers ──────────────────────────────────────────────────────────────────

def _call_agent(system_prompt: str, user_prompt: str, schema: Type[BaseModel]) -> dict:
    """
    Call Gemini in JSON mode and validate against a Pydantic schema using the new google-genai SDK.
    """
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                max_output_tokens=MAX_TOKENS,
                temperature=0.2,
                response_schema=schema,
            )
        )
        
        if response.parsed:
            # If the SDK successfully parsed the response into our Pydantic model
            if isinstance(response.parsed, schema):
                return response.parsed.model_dump()
            return schema.model_validate(response.parsed).model_dump()
            
        # Fallback to manual parsing if .parsed is empty
        text = response.text.strip()
        data = json.loads(text)
        return schema.model_validate(data).model_dump()

    except (json.JSONDecodeError, ValidationError, Exception) as e:
        logger.error(f"Agent validation failed or SDK error: {e}")
        # Retry once with explicit schema hint
        retry_prompt = f"{user_prompt}\n\nIMPORTANT: Respond ONLY in valid JSON matching this schema: {schema.model_json_schema()}"
        
        response2 = client.models.generate_content(
            model=MODEL,
            contents=retry_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                max_output_tokens=MAX_TOKENS,
                response_schema=schema,
            )
        )
        
        if response2.parsed:
             if isinstance(response2.parsed, schema):
                 return response2.parsed.model_dump()
             return schema.model_validate(response2.parsed).model_dump()

        data2 = json.loads(response2.text.strip())
        return schema.model_validate(data2).model_dump()



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


def _check_iteration_guard(pipeline):
    """
    Check if the pipeline has exceeded max_iterations.
    If so, marks as MAX_ITER and returns True.
    """
    if pipeline.iteration_count >= pipeline.max_iterations:
        pipeline.status = pipeline.Status.MAX_ITER
        pipeline.save(update_fields=['status'])
        logger.warning("Pipeline %s hit iteration limit.", pipeline.id)
        return True
    return False



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
      2. Initiates the iterative Planning & Reasoning cycle.
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)
    pipeline.status = PipelineRun.Status.RUNNING
    pipeline.save(update_fields=['status'])

    # Initialize first snapshot
    CritiqueSnapshot.objects.get_or_create(pipeline=pipeline, iteration=pipeline.iteration_count)

    input_text = pipeline.task_description
    try:
        # Start sequential sub-loop: Planner -> Reasoner
        run_planner.delay(pipeline_run_id)

        _log_agent(
            pipeline, AgentLog.AgentName.ORCHESTRATOR, pipeline.iteration_count,
            input_text, "Pipeline started. Dispatched to Planner.",
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
def run_planner(self, pipeline_run_id: str, sub_iteration: int = 0, feedback: str = ""):
    """
    PLANNER agent.

    Produces a structured plan. If called from Reasoner feedback, incorporates details.
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    if _check_iteration_guard(pipeline):
        return

    # Mismatch context from Reviewer (if any)
    reviewer_log = _latest_log(pipeline, AgentLog.AgentName.REVIEWER)
    mismatch_context = ""
    if reviewer_log and reviewer_log.output_text and pipeline.iteration_count > 0:
        mismatch_context = f"\n\nReviewer mismatch context:\n{reviewer_log.output_text}"

    # Reasoner feedback context (if any)
    reasoner_feedback = f"\n\nReasoner Feedback (sub-iteration {sub_iteration}):\n{feedback}" if feedback else ""

    user_prompt = pipeline.task_description + mismatch_context + reasoner_feedback
    system_prompt = (
        "You are a Strategic Planner. Produce a structured plan with sub-tasks and success criteria. "
        "Address any previous feedback in your revised plan."
    )

    try:
        result = _call_agent(system_prompt, user_prompt, PlanOutput)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS

        snapshot = _latest_critique(pipeline)
        if snapshot:
            snapshot.planner_critique = result['structural_critique']
            snapshot.save(update_fields=['planner_critique'])

    except Exception as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])


    _log_agent(
        pipeline, AgentLog.AgentName.PLANNER, pipeline.iteration_count,
        user_prompt, output_text, status
    )

    if status == AgentLog.LogStatus.SUCCESS:
        run_reasoner.delay(pipeline_run_id, sub_iteration=sub_iteration)



@shared_task(bind=True, name='orchestration.run_reasoner')
def run_reasoner(self, pipeline_run_id: str, sub_iteration: int = 0):
    """
    REASONER agent.

    Validates the plan. If issues found, loops back to Planner.
    Output JSON: { analysis: str, gaps: [str], reasoning_critique: str, sound: bool }
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    planner_log = _latest_log(pipeline, AgentLog.AgentName.PLANNER)
    user_prompt = planner_log.output_text if planner_log else pipeline.task_description

    system_prompt = "You are an Analytical Reasoner. Validate the logical soundness of the given plan."

    try:
        result = _call_agent(system_prompt, user_prompt, ReasonerOutput)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS

        snapshot = _latest_critique(pipeline)
        if snapshot:
            snapshot.reasoner_critique = result['reasoning_critique']
            snapshot.save(update_fields=['reasoner_critique'])

        is_sound = result['sound']
        
        # Iterative Loop logic
        if not is_sound and sub_iteration < 2:
            _log_agent(pipeline, AgentLog.AgentName.REASONER, pipeline.iteration_count, user_prompt, output_text, status)
            run_planner.delay(pipeline_run_id, sub_iteration=sub_iteration + 1, feedback=result['analysis'])
            return

    except Exception as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])


    _log_agent(
        pipeline, AgentLog.AgentName.REASONER, pipeline.iteration_count,
        user_prompt, output_text, status
    )

    if status == AgentLog.LogStatus.SUCCESS:
        run_final_critique.delay(None, pipeline_run_id)



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
    system_prompt = "You are a Synthesis Engine. Merge the Planner's structural critique and the Reasoner's reasoning critique into a single gold-standard review checklist."

    try:
        result = _call_agent(system_prompt, user_prompt, FinalCritiqueOutput)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS

        if snapshot:
            snapshot.merged_critique = result['merged_critique']
            snapshot.save(update_fields=['merged_critique'])

    except Exception as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
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
def run_writer(self, pipeline_run_id: str, editor_feedback: str = ""):
    """
    WRITER agent.

    Generates content. If editor_feedback is provided, it reviews and incorporates/rejects it.
    Decides whether to submit to Reviewer or call Editor again.
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    if _check_iteration_guard(pipeline):
        return

    snapshot = _latest_critique(pipeline)
    merged_critique = snapshot.merged_critique if snapshot else ""
    
    # Context: original task + critique + optional editor feedback
    user_prompt = (
        f"Original task:\n{pipeline.task_description}\n\n"
        f"Critique checklist:\n{merged_critique}"
    )
    if editor_feedback:
        user_prompt += f"\n\nEditor Feedback:\n{editor_feedback}\n\nPlease review this feedback and produce the final draft."

    system_prompt = "You are a Content Writer. Write a polished draft satisfying the checklist."

    try:
        result = _call_agent(system_prompt, user_prompt, WriterOutput)
        output_text = result['draft']
        is_ready = result['ready_for_review']
        status = AgentLog.LogStatus.SUCCESS
    except Exception as exc:
        output_text = str(exc)
        is_ready = False
        status = AgentLog.LogStatus.FAILED
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])


    _log_agent(
        pipeline, AgentLog.AgentName.WRITER, pipeline.iteration_count,
        user_prompt, json.dumps(result) if status == AgentLog.LogStatus.SUCCESS else output_text, status
    )

    if status == AgentLog.LogStatus.SUCCESS:
        if is_ready:
            run_reviewer.delay(pipeline_run_id)
        else:
            run_editor.delay(pipeline_run_id, notes=result.get('notes_for_editor', ''))



@shared_task(bind=True, name='orchestration.run_editor')
def run_editor(self, pipeline_run_id: str, notes: str = ""):
    """
    EDITOR agent (sub-agent of Writer).

    Executes fine-grained content manipulation and returns to Writer for review.
    """
    PipelineRun, AgentLog, CritiqueSnapshot = _get_models()
    pipeline = PipelineRun.objects.get(id=pipeline_run_id)

    writer_log = _latest_log(pipeline, AgentLog.AgentName.WRITER)
    if not writer_log:
        return

    # Extract draft from writer's JSON output
    try:
        writer_data = json.loads(writer_log.output_text)
        current_draft = writer_data.get('draft', '')
    except:
        current_draft = writer_log.output_text

    user_prompt = f"Current Draft:\n{current_draft}\n\nNotes from Writer:\n{notes}"
    system_prompt = "You are a Content Editor. Perform three passes: MODIFY, ADD, DELETE. Return the refined text and notes."

    try:
        result = _call_agent(system_prompt, user_prompt, EditorOutput)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS
    except Exception as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])


    _log_agent(
        pipeline, AgentLog.AgentName.EDITOR, pipeline.iteration_count,
        user_prompt, output_text, status
    )

    if status == AgentLog.LogStatus.SUCCESS:
        run_writer.delay(pipeline_run_id, editor_feedback=result.get('refined_text', ''))



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
    
    # Extract draft from writer's JSON output
    writer_output = ""
    if writer_log:
        try:
            writer_data = json.loads(writer_log.output_text)
            writer_output = writer_data.get('draft', writer_log.output_text)
        except:
            writer_output = writer_log.output_text
            
    merged_critique = snapshot.merged_critique if snapshot else ""


    user_prompt = (
        f"Draft to review:\n{writer_output}\n\n"
        f"Final Critique checklist:\n{merged_critique}"
    )
    system_prompt = "You are a Quality Reviewer. Compare the draft against the Final Critique checklist."

    try:
        result = _call_agent(system_prompt, user_prompt, ReviewerOutput)
        output_text = json.dumps(result)
        status = AgentLog.LogStatus.SUCCESS
    except Exception as exc:
        output_text = str(exc)
        status = AgentLog.LogStatus.FAILED
        pipeline.status = PipelineRun.Status.FAILED
        pipeline.save(update_fields=['status'])


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

    if _check_iteration_guard(pipeline):
        return

    # Create a new CritiqueSnapshot for the next iteration, PRESERVING previous critiques
    new_snapshot = CritiqueSnapshot.objects.create(
        pipeline=pipeline, 
        iteration=pipeline.iteration_count,
        planner_critique=snapshot.planner_critique if snapshot else '',
        reasoner_critique=snapshot.reasoner_critique if snapshot else ''
    )

    structural = result.get('structural_issues', False)
    logical = result.get('logical_issues', False)

    # Routing logic remains similar but triggers the new sequential/looped tasks
    if structural or logical or True: # Default to re-planning if not specified
        run_planner.delay(pipeline_run_id)

