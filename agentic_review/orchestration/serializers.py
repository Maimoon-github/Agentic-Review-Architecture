"""
DRF serializers for the orchestration pipeline API.
"""

from rest_framework import serializers
from orchestration.models import PipelineRun, AgentLog, CritiqueSnapshot


class AgentLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentLog
        fields = [
            'id', 'agent_name', 'iteration', 'input_text',
            'output_text', 'status', 'created_at',
        ]


class CritiqueSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CritiqueSnapshot
        fields = [
            'id', 'iteration', 'planner_critique',
            'reasoner_critique', 'merged_critique', 'created_at',
        ]


class PipelineRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineRun
        fields = [
            'id', 'task_description', 'status', 'iteration_count',
            'max_iterations', 'final_output', 'created_at', 'updated_at',
        ]


class PipelineStatusSerializer(serializers.ModelSerializer):
    """
    Extended serializer for the /status/ endpoint.
    Includes the latest AgentLog per agent_name for quick at-a-glance views.
    """
    latest_logs = serializers.SerializerMethodField()

    class Meta:
        model = PipelineRun
        fields = [
            'id', 'task_description', 'status', 'iteration_count',
            'max_iterations', 'created_at', 'updated_at', 'latest_logs',
        ]

    def get_latest_logs(self, obj):
        """Return a dict keyed by agent_name with the latest log per agent."""
        logs_by_agent = {}
        for log in obj.logs.order_by('agent_name', '-created_at'):
            if log.agent_name not in logs_by_agent:
                logs_by_agent[log.agent_name] = AgentLogSerializer(log).data
        return logs_by_agent
