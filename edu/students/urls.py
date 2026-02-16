# Adds per-view caching wrapper for URL routes.
from django.views.decorators.cache import cache_page

# URL pattern helper.
from django.urls import path

# Student app views used by these routes.
from . import views


# Cache timeout used for read-only pages: 15 minutes.
COURSE_PAGE_CACHE_SECONDS = 60 * 15


urlpatterns = [
    path(
        "courses/",
        # Enrolled courses list for current user.
        # Cached for 15 min to reduce repeated DB work.
        cache_page(COURSE_PAGE_CACHE_SECONDS)(
            views.StudentCourseListView.as_view()
        ),
        name="student_course_list",
    ),
    path(
        "course/<pk>/",
        # Course detail default view (first module).
        # Cached for 15 min.
        cache_page(COURSE_PAGE_CACHE_SECONDS)(
            views.StudentCourseDetailView.as_view()
        ),
        name="student_course_detail",
    ),
    path(
        "course/<pk>/<int:module_id>/",
        # Course detail with a specific module selected.
        # Cached for 15 min.
        cache_page(COURSE_PAGE_CACHE_SECONDS)(
            views.StudentCourseDetailView.as_view()
        ),
        name="student_course_detail_module",
    ),
    path(
        "register/",
        # User registration (not cached).
        views.StudentRegistrationView.as_view(),
        name="student_registration",
    ),
    path(
        "enroll-course/",
        # POST enrollment endpoint (not cached).
        views.StudentEnrollCourseView.as_view(),
        name="student_enroll_course",
    ),
]