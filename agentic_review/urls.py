"""
Root URL configuration - includes the orchestration API under /api/
and the template-based dashboard UI under /.
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('agentic_review.orchestration.urls')),
    path('', include('agentic_review.orchestration.template_urls')),
]
