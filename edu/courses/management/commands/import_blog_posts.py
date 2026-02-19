from pathlib import Path
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from courses.models import Text


COPY_HEADER_RE = re.compile(
    r"^COPY\s+public\.blog_post\s*\((?P<columns>.+?)\)\s+FROM\s+stdin;$"
)

COPY_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
}


def decode_copy_field(value):
    """
    Decode PostgreSQL COPY text-format escapes.
    """
    if value is None or value == r"\N":
        return None

    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue

        i += 1
        if i >= len(value):
            out.append("\\")
            break

        esc = value[i]

        if esc in COPY_ESCAPES:
            out.append(COPY_ESCAPES[esc])
            i += 1
            continue

        if esc in "01234567":
            oct_digits = [esc]
            i += 1
            for _ in range(2):
                if i < len(value) and value[i] in "01234567":
                    oct_digits.append(value[i])
                    i += 1
                else:
                    break
            out.append(chr(int("".join(oct_digits), 8)))
            continue

        if esc == "x":
            i += 1
            hex_digits = []
            while i < len(value) and len(hex_digits) < 2 and value[i] in "0123456789abcdefABCDEF":
                hex_digits.append(value[i])
                i += 1
            out.append(chr(int("".join(hex_digits), 16)) if hex_digits else "x")
            continue

        out.append(esc)
        i += 1

    return "".join(out)


class Command(BaseCommand):
    help = "Import title/body from COPY public.blog_post into courses.Text."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sql-path",
            default="my_backup.sql",
            help="Path to SQL dump file.",
        )
        parser.add_argument(
            "--owner-id",
            type=int,
            default=None,
            help="User id for Text.owner. If omitted, first user by id is used.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Import only first N rows.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse only, do not write to DB.",
        )

    def _required_fk_names(self):
        names = set()
        for field in Text._meta.get_fields():
            if not getattr(field, "many_to_one", False):
                continue
            if getattr(field, "auto_created", False):
                continue
            if getattr(field, "null", False):
                continue
            if hasattr(field, "has_default") and field.has_default():
                continue
            names.add(field.name)
        return names

    def _resolve_owner(self, owner_id):
        User = get_user_model()

        if owner_id is not None:
            try:
                return User.objects.get(pk=owner_id)
            except User.DoesNotExist as exc:
                raise CommandError(f"--owner-id={owner_id} does not exist.") from exc

        owner = User.objects.order_by("id").first()
        if owner is None:
            raise CommandError(
                "Text.owner is required and no users exist. "
                "Create a user first or provide --owner-id."
            )

        self.stdout.write(
            self.style.WARNING(
                f"No --owner-id provided, using first user id={owner.pk} ({owner})."
            )
        )
        return owner

    def _iter_blog_rows(self, sql_path):
        in_copy = False
        columns = []

        with sql_path.open("r", encoding="utf-8", errors="replace") as fh:
            for lineno, raw_line in enumerate(fh, start=1):
                line = raw_line.rstrip("\r\n")

                if not in_copy:
                    m = COPY_HEADER_RE.match(line)
                    if not m:
                        continue
                    columns = [c.strip() for c in m.group("columns").split(",")]
                    if "title" not in columns or "body" not in columns:
                        raise CommandError("COPY blog_post found, but title/body columns missing.")
                    in_copy = True
                    continue

                if line == r"\.":
                    return

                parts = line.split("\t")
                if len(parts) != len(columns):
                    raise CommandError(
                        f"Malformed row at line {lineno}: expected {len(columns)} columns, got {len(parts)}."
                    )

                yield lineno, dict(zip(columns, parts))

        if not in_copy:
            raise CommandError("COPY public.blog_post block not found.")
        raise CommandError("COPY public.blog_post block did not end with '\\.'.")

    def handle(self, *args, **options):
        sql_path = Path(options["sql_path"]).expanduser()
        if not sql_path.exists():
            raise CommandError(f"SQL file not found: {sql_path}")

        required_fks = self._required_fk_names()

        if "module" in required_fks:
            raise CommandError(
                "Your Text model requires module FK in this project. "
                "Add module mapping first."
            )

        owner = self._resolve_owner(options["owner_id"]) if "owner" in required_fks else None

        title_max = Text._meta.get_field("title").max_length
        limit = options["limit"]
        dry_run = options["dry_run"]

        processed = 0
        created = 0

        for lineno, row in self._iter_blog_rows(sql_path):
            if limit is not None and processed >= limit:
                break

            processed += 1

            title = (decode_copy_field(row.get("title")) or "").strip()
            body = decode_copy_field(row.get("body")) or ""

            if not title:
                source_id = decode_copy_field(row.get("id")) or f"line-{lineno}"
                title = f"Imported post {source_id}"

            if len(title) > title_max:
                self.stdout.write(
                    self.style.WARNING(
                        f"Line {lineno}: title too long ({len(title)}), truncating to {title_max}."
                    )
                )
                title = title[:title_max]

            payload = {
                "title": title,
                "content": body,
            }
            if owner is not None:
                payload["owner"] = owner

            if not dry_run:
                Text.objects.create(**payload)

            created += 1

        verb = "Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(f"{verb} {created} Text rows from {processed} blog rows."))
