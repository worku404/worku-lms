from django.urls import path

from . import views

app_name = "learning_insights"

urlpatterns = [
    path("", views.InsightsOverviewView.as_view(), name="overview"),
    path("weekly/", views.WeeklySummaryView.as_view(), name="weekly_summary"),
    path("monthly/", views.MonthlySummaryView.as_view(), name="monthly_summary"),
    path("goals/", views.GoalListView.as_view(), name="goal_list"),
    path("goals/new/", views.GoalCreateView.as_view(), name="goal_create"),
    path("goals/<int:pk>/edit/", views.GoalUpdateView.as_view(), name="goal_update"),
    path("goals/<int:pk>/delete/", views.GoalDeleteView.as_view(), name="goal_delete"),
    path(
        "goals/<int:pk>/action/",
        views.GoalQuickActionView.as_view(),
        name="goal_action",
    ),
    path(
        "notifications/",
        views.NotificationCenterView.as_view(),
        name="notification_center",
    ),
    path(
        "notifications/bootstrap/",
        views.NotificationBootstrapView.as_view(),
        name="notifications_bootstrap",
    ),
    path(
        "notifications/read-all/",
        views.NotificationMarkAllReadView.as_view(),
        name="notifications_mark_all_read",
    ),
    path(
        "notifications/<int:pk>/read/",
        views.NotificationMarkReadView.as_view(),
        name="notification_mark_read",
    ),
    path(
        "notifications/<int:pk>/dismiss/",
        views.NotificationDismissView.as_view(),
        name="notification_dismiss",
    ),
    path(
        "preferences/",
        views.NotificationPreferenceView.as_view(),
        name="preferences",
    ),
]
