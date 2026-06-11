"""
Template-view URL routes for the orchestration dashboard UI.
"""

from django.urls import path
from agentic_review.orchestration.template_views import (
    DashboardView,
    PipelineDetailView,
    PipelineCreateView,
)

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('pipeline/new/', PipelineCreateView.as_view(), name='pipeline-create'),
    path('pipeline/<uuid:pk>/', PipelineDetailView.as_view(), name='pipeline-detail'),
]
