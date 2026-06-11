"""
Django ORM models for the multi-agent orchestration pipeline.

Three models persist all pipeline state:
  - PipelineRun: top-level run record, tracks overall status & iteration count
  - AgentLog: per-agent invocation record (input/output, success/failure)
  - CritiqueSnapshot: merged artefact from Planner + Reasoner per iteration
"""

import uuid
from django.db import models


class PipelineRun(models.Model):
    """
    Represents one full orchestration pipeline execution.

    Lifecycle:
        PENDING → RUNNING → DONE (success)
                          → FAILED (unrecoverable error)
                          → MAX_ITER (hit iteration cap)
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        RUNNING = 'RUNNING', 'Running'
        DONE = 'DONE', 'Done'
        FAILED = 'FAILED', 'Failed'
        MAX_ITER = 'MAX_ITER', 'Max Iterations Reached'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_description = models.TextField(help_text="The original task prompt given to the pipeline.")
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    iteration_count = models.IntegerField(default=0)
    max_iterations = models.IntegerField(default=7)
    final_output = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pipeline Run'
        verbose_name_plural = 'Pipeline Runs'

    def __str__(self):
        return f"PipelineRun({self.id}, {self.status}, iter={self.iteration_count})"


class AgentLog(models.Model):
    """
    Records a single agent invocation within a pipeline run.

    Every task writes an AgentLog on both success and failure, preserving a
    full audit trail of what each agent received and produced.
    """

    class AgentName(models.TextChoices):
        ORCHESTRATOR = 'ORCHESTRATOR', 'Orchestrator'
        PLANNER = 'PLANNER', 'Planner'
        REASONER = 'REASONER', 'Reasoner'
        REVIEWER = 'REVIEWER', 'Reviewer'
        WRITER = 'WRITER', 'Writer'
        EDITOR = 'EDITOR', 'Editor'
        CRITIQUE = 'CRITIQUE', 'Final Critique'

    class LogStatus(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        SKIPPED = 'SKIPPED', 'Skipped'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.ForeignKey(
        PipelineRun, on_delete=models.CASCADE, related_name='logs'
    )
    agent_name = models.CharField(max_length=20, choices=AgentName.choices, db_index=True)
    iteration = models.IntegerField()
    input_text = models.TextField()
    output_text = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=LogStatus.choices, default=LogStatus.SUCCESS)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Agent Log'
        verbose_name_plural = 'Agent Logs'

    def __str__(self):
        return f"AgentLog({self.agent_name}, iter={self.iteration}, {self.status})"


class CritiqueSnapshot(models.Model):
    """
    Stores the synthesised critique artefacts for one iteration.

    planner_critique   — structural criticism from the Planner agent
    reasoner_critique  — logical gap analysis from the Reasoner agent
    merged_critique    — gold-standard checklist produced by Final Critique
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.ForeignKey(
        PipelineRun, on_delete=models.CASCADE, related_name='critiques'
    )
    iteration = models.IntegerField()
    planner_critique = models.TextField(blank=True, default='')
    reasoner_critique = models.TextField(blank=True, default='')
    merged_critique = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-iteration']
        verbose_name = 'Critique Snapshot'
        verbose_name_plural = 'Critique Snapshots'

    def __str__(self):
        return f"CritiqueSnapshot(pipeline={self.pipeline_id}, iter={self.iteration})"
