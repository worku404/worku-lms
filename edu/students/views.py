"""
Student-facing views:
- account registration
- course enrollment
- enrolled course list/detail
"""


# URL builder used for redirects after successful actions.
import os
import mimetypes
import subprocess
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.contrib.contenttypes.models import ContentType
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_exempt
from django.utils.cache import patch_response_headers

# Auth helpers and login-protection mixin.
from django.contrib.auth import authenticate, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.forms import UserCreationForm

# Generic class-based views for create/form/list/detail pages.
from django.views.generic.edit import CreateView, FormView
from django.views.generic.list import ListView
from django.views.generic import TemplateView, DetailView, View


# Local enrollment form and Course model.
from .forms import CourseEnrollForm
from courses.models import Content, Course, File, Image, Module
from .services import (add_time_spent, mark_module_completed, 
                       get_overall_progress, get_course_time_spent,
                        touch_user_presence
                    )


import redis


r = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
)



class StudentRegistrationView(CreateView):
    # Registration page template.
    template_name = "students/student/registration.html"

    # Built-in Django user registration form (username + password1/password2).
    form_class = UserCreationForm

    # After successful registration/login, go to enrolled courses page.
    success_url = reverse_lazy("student_course_list")

    def form_valid(self, form):
        # 1) Save the new user via CreateView.
        response = super().form_valid(form)

        # 2) Read validated form data.
        cd = form.cleaned_data

        # 3) Authenticate using submitted credentials.
        user = authenticate(username=cd["username"], password=cd["password1"])

        # 4) Log in user in same request cycle.
        if user is not None:
            login(self.request, user)

        # 5) Continue normal redirect flow.
        return response


class StudentEnrollCourseView(LoginRequiredMixin, FormView):
    # This form sends a selected Course object (hidden input).
    form_class = CourseEnrollForm

    # Temporary storage: filled in form_valid(), used by get_success_url().
    course = None

    def form_valid(self, form):
        # 1) Pull validated Course object from form.
        self.course = form.cleaned_data["course"]

        # 2) Add current user to course.students (ManyToMany relation).
        self.course.students.add(self.request.user)

        # 3) Continue FormView success flow.
        return super().form_valid(form)

    def get_success_url(self):
        # Redirect to detail page of the enrolled course.
        return reverse_lazy("student_course_detail", args=[self.course.id])


class StudentCourseListView(LoginRequiredMixin, ListView):
    # Base model for list.
    model = Course
    template_name = "students/course/list.html"
    context_object_name = "courses"

    def get_queryset(self):
        # Return only courses where current user is enrolled.
        qs = super().get_queryset()
        return qs.filter(students__in=[self.request.user])

class StudentCourseDetailView(LoginRequiredMixin, DetailView):
    model = Course
    template_name = "students/course/detail.html"

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(students__in=[self.request.user])
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course = self.get_object()
        modules = course.modules.all()
        user = self.request.user

        if "module_id" in self.kwargs:
            module = get_object_or_404(Module, id=self.kwargs["module_id"], course=course)
        else:
            module = modules.first()

        context["module"] = module
        context["course_time"] = get_course_time_spent(self.request.user, course)
        return context


class MarkModuleCompleteView(LoginRequiredMixin, View):
    def post(self, request, module_id):
        module = get_object_or_404(
            Module, 
            id=module_id,
            course__students=request.user,
            )

        mark_module_completed(request.user, module)

        return JsonResponse({'status': 'completed'})

class TrackTimeView(LoginRequiredMixin, View):
    def post(self, request, module_id):
        module = get_object_or_404(
            Module,
            id=module_id,
            course__students=request.user,
            
            )
        
        try:
            # Handle both standard POST and JSON/Beacon payloads
            seconds = int(request.POST.get('seconds', 0))
            
            if seconds > 0:
                add_time_spent(request.user, module, seconds)
                return JsonResponse({'status': 'tracked', 'seconds': seconds})
            
            return JsonResponse({'status': 'ignored', 'reason': '0 seconds'})
            
        except ValueError:
            return JsonResponse({'status': 'error', 'reason': 'Invalid seconds value'}, status=400)


class StudentDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'students/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        context['overall_progress'] = get_overall_progress(user)
        context['courses'] = user.courses_joined.all()

        return context


class DownloadModuleFileView(LoginRequiredMixin, View):
    """
    Serves a module file as an attachment for enrolled users.
    This avoids relying on direct /media URLs in production.
    """
    def get(self, request, file_id):
        file_type = ContentType.objects.get_for_model(File)
        content = get_object_or_404(
            Content,
            content_type=file_type,
            object_id=file_id,
            module__course__students=request.user,
        )
        file_obj = content.item

        if not file_obj or not file_obj.file:
            raise Http404("File not found.")

        filename = os.path.basename(file_obj.file.name)

        try:
            return FileResponse(
                file_obj.file.open("rb"),
                as_attachment=True,
                filename=filename,
            )
        except FileNotFoundError as exc:
            raise Http404("File not found.") from exc




class ModuleImageView(LoginRequiredMixin, View):
    """
    Serves module images only to enrolled users.
    This avoids relying on direct /media URLs in production.
    """
    def get(self, request, image_id):
        image_type = ContentType.objects.get_for_model(Image)
        content = get_object_or_404(
            Content,
            content_type=image_type,
            object_id=image_id,
            module__course__students=request.user,
        )
        image_obj = content.item

        if not image_obj or not image_obj.file:
            raise Http404("Image not found.")

        filename = os.path.basename(image_obj.file.name)

        try:
            return FileResponse(
                image_obj.file.open("rb"),
                filename=filename,
            )
        except FileNotFoundError as exc:
            raise Http404("Image not found.") from exc

# pdf previw page

@method_decorator(xframe_options_exempt, name="dispatch")
class ModuleFilePreviewView(LoginRequiredMixin, View):
    """
    Serve module file inline (for PDF browser preview) to enrolled users.
    """
    def get(self, request, file_id):
        file_type = ContentType.objects.get_for_model(File)
        content = get_object_or_404(
            Content,
            content_type=file_type,
            object_id=file_id,
            module__course__students=request.user,
        )
        file_obj = content.item

        if not file_obj or not file_obj.file:
            raise Http404("File not found.")

        filename = os.path.basename(file_obj.file.name)
        content_type, _ = mimetypes.guess_type(filename)
        if not content_type:
            content_type = "application/octet-stream"

        try:
            response = FileResponse(
                file_obj.file.open("rb"),
                as_attachment=False,
                filename=filename,
                content_type=content_type,
            )
        except FileNotFoundError as exc:
            raise Http404("File not found.") from exc

        # Force inline rendering in browser viewers (PDF/image support).
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        patch_response_headers(response, cache_timeout=0)
        return response


# Online count
class PresencePingView(LoginRequiredMixin, View):
    def post(self, request):
        online_count = touch_user_presence(request.user.id)
        return JsonResponse({"online_count": online_count})


class StopLocalStackView(LoginRequiredMixin, View):
    """
    Stop local development services (Django + Redis) by launching stop.ps1.
    This is intentionally restricted to privileged users on localhost only.
    """

    def post(self, request):
        # Guard 1: only staff/superusers can trigger a local shutdown.
        if not (request.user.is_staff or request.user.is_superuser):
            return JsonResponse({"error": "Forbidden"}, status=403)

        # Guard 2: only allow requests coming from local loopback addresses.
        remote_addr = request.META.get("REMOTE_ADDR")
        if remote_addr not in {"127.0.0.1", "::1"}:
            return JsonResponse({"error": "Localhost only"}, status=403)

        # Guard 3: only allow localhost host headers for extra safety.
        host = request.get_host().split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost"}:
            return JsonResponse({"error": "Localhost host only"}, status=403)

        # Resolve the local stop script from project root.
        stop_script = Path(settings.BASE_DIR) / "stop.ps1"
        if not stop_script.exists():
            return JsonResponse({"error": "stop.ps1 not found"}, status=500)

        # Delay execution slightly so this request can return before the server stops itself.
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Start-Sleep -Milliseconds 650; & '{stop_script}'",
        ]

        # Detach process on Windows so it survives independently of this request thread.
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )

        # Launch stop script without inheriting stdio handles.
        subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )

        # Return immediate confirmation; shutdown continues asynchronously.
        return JsonResponse({"status": "stopping"})
