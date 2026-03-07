from __future__ import annotations

import unicodedata
from functools import lru_cache

from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
    TrigramSimilarity,
)
from django.db.models import F, FloatField, Q, TextField, Value
from django.db.models.expressions import ExpressionWrapper
from django.db.models.functions import Coalesce, Greatest
from django.db.models import Func

from .models import Content, Course, CourseSearchIndex, File

RANK_THRESHOLD = 0.02
SIMILARITY_THRESHOLD = 0.08
COURSE_DOC_MAX_PDF_CHARS = 220000


def _normalize_whitespace(value: str) -> str:
    return " ".join((value or "").split())


def _unaccent_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_text or value or ""


@lru_cache(maxsize=1)
def _file_content_type_id() -> int:
    return ContentType.objects.get_for_model(File).id


def _course_pdf_text_chunks(course_id: int):
    file_ct = _file_content_type_id()
    file_ids = Content.objects.filter(
        module__course_id=course_id,
        content_type_id=file_ct,
    ).values_list("object_id", flat=True)
    return File.objects.filter(
        id__in=file_ids,
        pdf_index_status="indexed",
    ).exclude(pdf_text_index="").values_list("pdf_text_index", flat=True)


def build_course_search_document(course: Course) -> str:
    parts = [
        course.title,
        course.overview,
        course.subject.title if course.subject_id else "",
    ]
    for module in course.modules.all():
        parts.append(module.title)
        parts.append(module.description)

    char_budget = COURSE_DOC_MAX_PDF_CHARS
    for chunk in _course_pdf_text_chunks(course.id):
        if char_budget <= 0:
            break
        clean = _normalize_whitespace(chunk)
        if not clean:
            continue
        if len(clean) > char_budget:
            clean = clean[:char_budget]
        parts.append(clean)
        char_budget -= len(clean)

    return "\n".join(_normalize_whitespace(p) for p in parts if p)


def refresh_course_search_index(course_id: int) -> bool:
    try:
        course = (
            Course.objects.select_related("subject")
            .prefetch_related("modules")
            .get(id=course_id)
        )
    except Course.DoesNotExist:
        return False

    document = build_course_search_document(course)
    CourseSearchIndex.objects.update_or_create(
        course_id=course_id,
        defaults={"document": document},
    )
    return True


def refresh_subject_course_indexes(subject_id: int) -> int:
    updated = 0
    for course_id in Course.objects.filter(subject_id=subject_id).values_list("id", flat=True):
        if refresh_course_search_index(course_id):
            updated += 1
    return updated


def refresh_file_related_course_indexes(file_id: int) -> int:
    file_ct = _file_content_type_id()
    course_ids = (
        Course.objects.filter(
            modules__contents__content_type_id=file_ct,
            modules__contents__object_id=file_id,
        )
        .values_list("id", flat=True)
        .distinct()
    )
    updated = 0
    for course_id in course_ids:
        if refresh_course_search_index(course_id):
            updated += 1
    return updated


def rebuild_course_search_index(course_ids: list[int] | None = None) -> dict[str, int]:
    queryset = Course.objects.select_related("subject").prefetch_related("modules")
    if course_ids:
        queryset = queryset.filter(id__in=course_ids)

    created = 0
    updated = 0
    processed = 0

    for course in queryset:
        processed += 1
        _, row_created = CourseSearchIndex.objects.update_or_create(
            course_id=course.id,
            defaults={
                "document": build_course_search_document(course),
            },
        )
        if row_created:
            created += 1
        else:
            updated += 1

    return {"processed": processed, "created": created, "updated": updated}


def search_courses(queryset, query: str):
    normalized_query = _normalize_whitespace(query)
    if not normalized_query:
        return queryset

    query_text = _unaccent_text(normalized_query)
    document = Coalesce(F("search_index__document"), Value(""), output_field=TextField())
    unaccented_document = Func(
        document,
        function="immutable_unaccent",
        output_field=TextField(),
    )

    unaccented_title = Func(
        F("title"),
        function="immutable_unaccent",
        output_field=TextField(),
    )
    unaccented_subject = Func(
        F("subject__title"),
        function="immutable_unaccent",
        output_field=TextField(),
    )

    search_query = SearchQuery(query_text, config="simple", search_type="websearch")
    search_vector = SearchVector(unaccented_document, config="simple")
    search_rank = SearchRank(search_vector, search_query)

    trigram_document = TrigramSimilarity(unaccented_document, Value(query_text))
    trigram_title = TrigramSimilarity(unaccented_title, Value(query_text))
    trigram_subject = TrigramSimilarity(unaccented_subject, Value(query_text))

    trigram_score = Greatest(trigram_title, trigram_subject, trigram_document)
    combined_score = ExpressionWrapper(
        (search_rank * Value(2.2))
        + (trigram_title * Value(1.2))
        + (trigram_subject * Value(0.7))
        + (trigram_document * Value(1.0)),
        output_field=FloatField(),
    )

    return (
        queryset.select_related("subject")
        .annotate(
            search_rank=search_rank,
            trigram_document=trigram_document,
            trigram_title=trigram_title,
            trigram_subject=trigram_subject,
            trigram_score=trigram_score,
            combined_score=combined_score,
        )
        .filter(
            Q(search_rank__gte=RANK_THRESHOLD)
            | Q(trigram_score__gte=SIMILARITY_THRESHOLD)
        )
        .order_by("-combined_score", "-created")
    )
