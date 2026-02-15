from django.views.generic.list import ListView
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic.base import TemplateResponseMixin, View

from .models import Course
from .forms import ModuleFormSet


class CourseModuleUpdateView(TemplateResponseMixin, View):
    # Template that shows the module formset for one course.
    template_name = "courses/manage/module/formset.html"

    # Will store the current course object after permission-safe lookup.
    course = None

    def get_formset(self, data=None):
        # Build a formset bound to THIS course.
        # data=None -> empty/unbound formset for GET
        # data=request.POST -> bound formset for POST validation
        return ModuleFormSet(instance=self.course, data=data)

    def dispatch(self, request, *args, **kwargs):
        # Runs before get/post.
        # Load the course from URL and ensure the logged-in user owns it.
        # If not found or not owned, return 404.
        self.course = get_object_or_404(
            Course,
            id=kwargs["pk"],
            owner=request.user,
        )

        # Continue normal request flow (GET/POST/etc).
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        # User opened the page: show current modules + extra empty forms.
        formset = self.get_formset()
        return self.render_to_response(
            {"course": self.course, "formset": formset}
        )

    def post(self, request, *args, **kwargs):
        # User submitted the module forms: validate and save.
        formset = self.get_formset(data=request.POST)

        if formset.is_valid():
            formset.save()  # create/update/delete Module rows
            return redirect("manage_course_list")  # back to "My courses"

        # Validation failed: show same page with error messages.
        return self.render_to_response(
            {"course": self.course, "formset": formset}
        )



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


class OwnerCourseMixin(
    OwnerMixin, LoginRequiredMixin, PermissionRequiredMixin
    ):
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
    permission_required = 'courses.view_course'


class CourseCreateView(OwnerCourseEditMixin, CreateView):
    # Uses OwnerEditMixin to set owner automatically.
    permission_required = 'courses.add_course'


class CourseUpdateView(OwnerCourseEditMixin, UpdateView):
    # Users can update only their own courses (via OwnerMixin.get_queryset).
    permission_required = 'courses.change_course'


class CourseDeleteView(OwnerCourseMixin, DeleteView):
    # Users can delete only their own courses (via OwnerMixin.get_queryset).
    template_name = "courses/manage/course/delete.html"
    permission_required = 'courses.delete_course'