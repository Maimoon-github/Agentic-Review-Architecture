"""
URL routes for the orchestration app.
"""

from django.urls import path
from orchestration.views import (
    PipelineStartView,
    PipelineStatusView,
    PipelineLogsView,
    PipelineOutputView,
)

urlpatterns = [
    path('pipeline/start/', PipelineStartView.as_view(), name='pipeline-start'),
    path('pipeline/<uuid:pk>/status/', PipelineStatusView.as_view(), name='pipeline-status'),
    path('pipeline/<uuid:pk>/logs/', PipelineLogsView.as_view(), name='pipeline-logs'),
    path('pipeline/<uuid:pk>/output/', PipelineOutputView.as_view(), name='pipeline-output'),
]
