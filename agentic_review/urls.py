"""
Root URL configuration - includes the orchestration API under /api/.
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('orchestration.urls')),
]
