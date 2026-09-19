from django.urls import include, path

urlpatterns = [
    path('', include('apps.common.urls')),
    path('', include('apps.users.urls')),
    path('', include('apps.students.urls')),
    path('', include('apps.subjects.urls')),
    path('', include('apps.projects.urls')),
    path('', include('apps.study_plans.urls')),
    path('', include('apps.quizzes.urls')),
    path('', include('apps.sources.urls')),
    path('subscriptions/', include('apps.subscriptions.urls')),
    path('ai/', include('apps.ai_integration.urls')),
    path('', include('apps.analytics.urls')),
    path('', include('apps.summaries.urls')),
    path('', include('apps.audio.urls')),
    path('', include('apps.notifications.urls')),
    path('', include('apps.support.urls')),
    path('', include('apps.waitlist.urls')),
    path('admin/', include('apps.admin_dashboard.urls')),
    # Scoped organization administration lives under the same admin
    # prefix as the rest of the dashboard surface.
    path('admin/', include('apps.organizations.admin_urls')),
    # Learner-facing join flow: authenticated students, not admins.
    path('', include('apps.organizations.student_urls')),
]
