from django.views.generic.list import ListView
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from .models import Course


class OwnerMixin:
    # Restrict querysets to objects owned by the current user.
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(owner=self.request.user)


class OwnerEditMixin:
    # On create/update, force ownership to the current user.
    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class OwnerCourseMixin(OwnerMixin):
    # Shared configuration for views that work with Course objects.
    model = Course
    fields = ["subject", "title", "slug", "overview"]
    success_url = reverse_lazy("manage_course_list")


class OwnerCourseEditMixin(OwnerCourseMixin, OwnerEditMixin):
    # Shared template for create and update forms.
    template_name = "courses/manage/course/form.html"


class ManageCourseListView(OwnerCourseMixin, ListView):
    # Shows only the current user's courses (via OwnerMixin.get_queryset).
    template_name = "courses/manage/course/list.html"


class CourseCreateView(OwnerCourseEditMixin, CreateView):
    # Uses OwnerEditMixin to set owner automatically.
    pass


class CourseUpdateView(OwnerCourseEditMixin, UpdateView):
    # Users can update only their own courses (via OwnerMixin.get_queryset).
    pass


class CourseDeleteView(OwnerCourseMixin, DeleteView):
    # Users can delete only their own courses (via OwnerMixin.get_queryset).
    template_name = "courses/manage/course/delete.html"
