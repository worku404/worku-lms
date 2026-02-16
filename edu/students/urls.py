from django.views.decorators.cache import cache_page
from django.urls import path
from . import views

urlpatterns = [
    path(
        'courses/',
        cache_page(60 * 15 )(
        views.StudentCourseListView.as_view()),
        name='student_course_list'
    ),
    path(
        'course/<pk>/',
        cache_page(60 * 15 )(
        views.StudentCourseDetailView.as_view()),
        name='student_course_detail'
    ),
    path(
      'course/<pk>/<int:module_id>/',
      cache_page(60 * 15 )(
      views.StudentCourseDetailView.as_view()),
      name='student_course_detail_module'
    ),
    path(
        'register/',
        views.StudentRegistrationView.as_view(),
        name='student_registration'
    ),
    path(
        'enroll-course/',
        views.StudentEnrollCourseView.as_view(),
        name='student_enroll_course'
    ),
]
