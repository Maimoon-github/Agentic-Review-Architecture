"""
DRF API views for the multi-agent orchestration pipeline.

Endpoints:
  POST   /api/pipeline/start/           → Create & dispatch pipeline
  GET    /api/pipeline/{id}/status/     → Polling endpoint with per-agent latest logs
  GET    /api/pipeline/{id}/logs/       → Full audit log, filterable by agent_name
  GET    /api/pipeline/{id}/output/     → Final output (404 if not DONE)
"""

import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.shortcuts import get_object_or_404

from agentic_review.orchestration.models import PipelineRun, AgentLog
from agentic_review.orchestration.serializers import (
    PipelineRunSerializer,
    PipelineStatusSerializer,
    AgentLogSerializer,
)
from agentic_review.orchestration.tasks import run_orchestrator

logger = logging.getLogger('orchestration')


class PipelineStartView(APIView):
    """
    POST /api/pipeline/start/

    Body:
        {
            "task_description": "...",
            "max_iterations": 7  (optional)
        }

    Creates a PipelineRun and enqueues the Orchestrator Celery task.
    Returns the pipeline_id and initial PENDING status immediately.
    """

    def post(self, request):
        task_description = request.data.get('task_description', '').strip()
        if not task_description:
            return Response(
                {'error': 'task_description is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_iterations = int(request.data.get('max_iterations', 7))

        pipeline = PipelineRun.objects.create(
            task_description=task_description,
            max_iterations=max_iterations,
            status=PipelineRun.Status.PENDING,
        )

        # Enqueue the orchestrator (non-blocking)
        run_orchestrator.delay(str(pipeline.id))

        logger.info("Pipeline %s created. Task: %s", pipeline.id, task_description[:60])
        return Response(
            {'pipeline_id': str(pipeline.id), 'status': pipeline.status},
            status=status.HTTP_201_CREATED,
        )


class PipelineStatusView(APIView):
    """
    GET /api/pipeline/{id}/status/

    Returns the current PipelineRun state plus the latest AgentLog per agent.
    Intended for polling until status reaches DONE / FAILED / MAX_ITER.
    """
    permission_classes = [AllowAny]

    def get(self, request, pk):
        pipeline = get_object_or_404(PipelineRun, id=pk)
        serializer = PipelineStatusSerializer(pipeline)
        return Response(serializer.data)


class PipelineLogsView(APIView):
    """
    GET /api/pipeline/{id}/logs/?agent_name=PLANNER

    Returns all AgentLog entries for this pipeline, ordered chronologically.
    Optional query param `agent_name` filters by a specific agent.
    """
    permission_classes = [AllowAny]

    def get(self, request, pk):
        pipeline = get_object_or_404(PipelineRun, id=pk)
        logs = pipeline.logs.order_by('created_at')

        agent_name = request.query_params.get('agent_name')
        if agent_name:
            logs = logs.filter(agent_name=agent_name.upper())

        serializer = AgentLogSerializer(logs, many=True)
        return Response(serializer.data)


class PipelineOutputView(APIView):
    """
    GET /api/pipeline/{id}/output/

    Returns the final polished output once the pipeline has status=DONE.
    Returns 404 if the pipeline has not completed successfully yet.
    """
    permission_classes = [AllowAny]

    def get(self, request, pk):
        pipeline = get_object_or_404(PipelineRun, id=pk)
        if pipeline.status != PipelineRun.Status.DONE:
            return Response(
                {
                    'error': 'Output not available yet.',
                    'status': pipeline.status,
                    'iteration_count': pipeline.iteration_count,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            'final_output': pipeline.final_output,
            'iteration_count': pipeline.iteration_count,
            'status': pipeline.status,
        })
