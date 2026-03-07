from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from .models import File

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional runtime dependency fallback
    PdfReader = None


PDF_INDEX_MAX_PAGES = int(getattr(settings, "PDF_INDEX_MAX_PAGES", 60))
PDF_INDEX_MAX_CHARS = int(getattr(settings, "PDF_INDEX_MAX_CHARS", 180000))
PDF_INDEX_ERROR_MAX = 4000


@dataclass
class PdfIndexResult:
    status: str
    text: str
    page_count: int
    error: str


def _normalize_text(value: str) -> str:
    return " ".join((value or "").split())


def _is_pdf_path(name: str) -> bool:
    return str(name or "").lower().endswith(".pdf")


def extract_pdf_index_data(file_obj: File) -> PdfIndexResult:
    if not file_obj.file or not _is_pdf_path(file_obj.file.name):
        return PdfIndexResult(status="skipped", text="", page_count=0, error="")

    if PdfReader is None:
        return PdfIndexResult(
            status="failed",
            text="",
            page_count=0,
            error="pypdf not installed",
        )

    try:
        with file_obj.file.open("rb") as handle:
            reader = PdfReader(handle)
            page_total = len(reader.pages)
            max_pages = max(1, min(page_total, PDF_INDEX_MAX_PAGES))

            chunks = []
            current_chars = 0
            for idx in range(max_pages):
                raw = reader.pages[idx].extract_text() or ""
                clean = _normalize_text(raw)
                if not clean:
                    continue
                remaining = PDF_INDEX_MAX_CHARS - current_chars
                if remaining <= 0:
                    break
                if len(clean) > remaining:
                    clean = clean[:remaining]
                chunks.append(clean)
                current_chars += len(clean)

            return PdfIndexResult(
                status="indexed",
                text="\n".join(chunks),
                page_count=page_total,
                error="",
            )
    except Exception as exc:
        return PdfIndexResult(
            status="failed",
            text="",
            page_count=0,
            error=str(exc)[:PDF_INDEX_ERROR_MAX],
        )


def update_pdf_index_for_file(file_id: int) -> PdfIndexResult | None:
    try:
        file_obj = File.objects.get(id=file_id)
    except File.DoesNotExist:
        return None

    result = extract_pdf_index_data(file_obj)
    File.objects.filter(id=file_id).update(
        pdf_text_index=result.text,
        pdf_page_count=result.page_count,
        pdf_index_status=result.status,
        pdf_index_error=result.error,
        pdf_indexed_at=timezone.now(),
    )
    return result
