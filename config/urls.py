from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.common.api_views import LandingPageView

admin.site.site_header = 'لوحة إدارة برّاق'
admin.site.site_title = 'Baraq Admin'
admin.site.index_title = 'إدارة منصة برّاق'

urlpatterns = [
    path('', LandingPageView.as_view(), name='landing'),
    path('admin/', admin.site.urls),
    path('api/schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='api-docs'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='api-schema'), name='api-redoc'),
    path('api/v1/', include('config.api_urls')),
    path('api/internal/v1/ai/', include('apps.ai_integration.internal_urls')),
    # Backward-compatible routes. New clients must use /api/v1/.
    path('api/', include('config.api_urls')),
]

handler404 = 'apps.common.error_views.handler404'
handler500 = 'apps.common.error_views.handler500'
