"""
Template-based views for the orchestration dashboard frontend.

Three pages:
  /             → Dashboard listing all pipeline runs
  /pipeline/new/     → Form to start a new pipeline
  /pipeline/<pk>/    → Live detail view for a single pipeline run
"""

from django.views.generic import TemplateView, ListView, DetailView, FormView
from django.views import View
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse

from agentic_review.orchestration.models import PipelineRun, AgentLog, CritiqueSnapshot
from agentic_review.orchestration.tasks import run_orchestrator


class DashboardView(ListView):
    """
    GET /
    Lists all pipeline runs, newest first.
    """
    model = PipelineRun
    template_name = 'orchestration/dashboard.html'
    context_object_name = 'pipelines'
    ordering = ['-created_at']
    paginate_by = 20

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_count'] = PipelineRun.objects.filter(
            status__in=[PipelineRun.Status.PENDING, PipelineRun.Status.RUNNING]
        ).count()
        return ctx


class PipelineCreateView(View):
    """
    GET  /pipeline/new/  → render the submission form
    POST /pipeline/new/  → create a PipelineRun and redirect to detail
    """

    def get(self, request):
        from django.shortcuts import render
        return render(request, 'orchestration/pipeline_create.html')

    def post(self, request):
        task_description = request.POST.get('task_description', '').strip()
        max_iterations = int(request.POST.get('max_iterations', 7))

        if not task_description:
            from django.shortcuts import render
            return render(request, 'orchestration/pipeline_create.html', {
                'error': 'Task description is required.',
                'task_description': task_description,
                'max_iterations': max_iterations,
            })

        pipeline = PipelineRun.objects.create(
            task_description=task_description,
            max_iterations=max_iterations,
            status=PipelineRun.Status.PENDING,
        )
        run_orchestrator.delay(str(pipeline.id))
        return redirect(reverse('pipeline-detail', kwargs={'pk': pipeline.pk}))


class PipelineDetailView(DetailView):
    """
    GET /pipeline/<pk>/
    Live detail view — the page polls /api/pipeline/<pk>/status/ via JS.
    """
    model = PipelineRun
    template_name = 'orchestration/pipeline_detail.html'
    context_object_name = 'pipeline'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pipeline = self.object
        ctx['logs'] = pipeline.logs.order_by('created_at')
        ctx['latest_critique'] = pipeline.critiques.order_by('-iteration').first()
        ctx['agent_names'] = [a.value for a in AgentLog.AgentName]
        return ctx
