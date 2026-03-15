from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Note, Tag
from .search import refresh_note_search_index, delete_note_search_index


@receiver(post_save, sender=Note)
def update_note_search_index(sender, instance, **kwargs):
    refresh_note_search_index(instance.id)


@receiver(post_delete, sender=Note)
def delete_note_search_index_on_delete(sender, instance, **kwargs):
    delete_note_search_index(instance.id)


@receiver(post_save, sender=Tag)
def refresh_notes_for_tag(sender, instance, **kwargs):
    note_ids = list(instance.notes.values_list("id", flat=True))
    for note_id in note_ids:
        refresh_note_search_index(note_id)
