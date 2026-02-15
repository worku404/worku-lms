from django.views.generic.list import ListView
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic.base import TemplateResponseMixin, View

from django.apps import apps
from django.forms.models import modelform_factory
from django.http import Http404


from braces.views import CsrfExemptMixin, JsonRequestResponseMixin

from .models import Module,Course, Content
from .forms import ModuleFormSet

class ContentCreateUpdateView(TemplateResponseMixin, View):
    """
    Handles both create and update flows for course content models
    (Text, Video, Image, File) attached to a specific module.
    """
    template_name = "courses/manage/content/form.html"

    # These are populated during dispatch for use in get/post.
    module = None
    model = None
    obj = None

    def get_model(self, model_name):
        """
        Resolve the model class safely from URL text.
        Only allow known content model names to avoid arbitrary model access.
        """
        allowed_models = {"text", "video", "image", "file"}
        if model_name not in allowed_models:
            return None
        return apps.get_model(app_label="courses", model_name=model_name)

    def get_form(self, model, *args, **kwargs):
        """
        Build a ModelForm class dynamically for the resolved model.
        Exclude internal/system-managed fields from user editing.
        """
        FormClass = modelform_factory(
            model,
            exclude=["owner", "order", "created", "updated"],  
        )
        return FormClass(*args, **kwargs)

    def dispatch(self, request, *args, **kwargs):
        """
        Common pre-processing for all HTTP methods:
        1) Load module and enforce ownership.
        2) Resolve content model from URL.
        3) If object id exists, load existing object for update flow.
        """
        module_id = kwargs.get("module_id")
        model_name = kwargs.get("model_name")
        object_id = kwargs.get("id")

        # Ensure user can only edit modules in their own courses.
        self.module = get_object_or_404(
            Module,
            id=module_id,
            course__owner=request.user,
        )

        # Resolve model class (Text/Video/Image/File).
        self.model = self.get_model(model_name)
        if self.model is None:
            raise Http404("Unsupported content type")

        # Update mode: fetch existing owned object.
        if object_id is not None:
            self.obj = get_object_or_404(
                self.model,
                id=object_id,
                owner=request.user,
            )

        return super().dispatch(request, *args, **kwargs)
    
    
    def get(self, request, module_id, model_name, id=None):
        # Build the form for display:
        # - create mode: self.obj is None -> empty form     
        # - update mode: self.obj exists -> form prefilled with existing data
        form = self.get_form(self.model, instance=self.obj)

        # Render the template with:
        # - form: the content form to showKMkkk
        # - object: existing object (None in create mode), useful for template logic
        return self.render_to_response(
            {"form": form, "object": self.obj}
        )


    def post(self, request, module_id, model_name, id=None):
        # Build a bound form from submitted data:
        # - request.POST handles text fields
        # - request.FILES handles file/image uploads
        # - instance=self.obj means "update" if object exists, otherwise "create new"
        form = self.get_form(
            self.model,
            instance=self.obj,
            data=request.POST,
            files=request.FILES,
        )

    # Validate submitted data against model/form rules
        if form.is_valid():
            # Don't save yet; we need to set owner first
            obj = form.save(commit=False)

            # Enforce ownership on server side (never trust client input)
            obj.owner = request.user

            # Save the concrete content object (Text/File/Image/Video)
            obj.save()

            # If this is create mode (no existing id), create the link
            # between the current module and this newly created content object.
            if not id:
                Content.objects.create(module=self.module, item=obj)

            # Redirect after successful POST to avoid duplicate form resubmission
            return redirect("module_content_list", self.module.id)

        # If form is invalid, re-render with errors and previously submitted values
        return self.render_to_response(
            {"form": form, "object": self.obj}
        )

    
    
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

class ContentDeleteView(View):
    def post(self, request, id):
        content = get_object_or_404(
            Content,
            id=id,
            module__course__owner = request.user
        )
        module = content.module
        content.item.delete()
        content.delete()
        return redirect('module_content_list', module.id)

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
    
class ModuleContentListView(TemplateResponseMixin, View):
    template_name = 'courses/manage/module/content_list.html'
    
    def get(self, request, module_id):
        module = get_object_or_404(
            Module,
            id=module_id,
            course__owner=request.user
        )
        return self.render_to_response({'module': module})
    
class ModuleOrderView(CsrfExemptMixin, JsonRequestResponseMixin, View):
    def post(self, request):
        for id, order in self.request_json.items():
            Module.objects.filter(
                id=id,
                course__owner=request.user
            ).update(order=order)
        return self.render_json_response({'saved': 'OK'})
    
class ContentOrderview(CsrfExemptMixin, JsonRequestResponseMixin, View):
    def post(self, request):
        for id, order in self.request_json.items():
            Content.objects.filter(
                id=id,
                module__course__owner = request.user
            ).update(order=order)
        return self.render_json_response({'saved': 'OK'})