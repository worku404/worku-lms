from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from .markdown_utils import render_llm_markdown
from .models import AssistantChat, AssistantTurn
from .services import GeminiError, generate_ai_response
from .utils import (
    PIN_LIMIT,
    build_chat_state,
    delete_unpinned_chat,
    get_active_chat,
    serialize_history,
    set_active_chat,
    title_from_prompt,
    toggle_pin,
)

@require_POST
@login_required
def llm_generate(request):
    prompt = (request.POST.get("prompt") or "").strip()
    
    if not prompt:
        return JsonResponse({"error": "Prompt is required."}, status=400)

    system_prompt = (
        "You are a helpful AI study assistant inside an e-learning platform. "
        "Be direct and practical. When relevant, include short actionable steps, "
        "and use Markdown for readability."
    )

    active_chat = get_active_chat(request)
    max_turns = 3
    contents = []
    if active_chat:
        recent_turns = list(
            AssistantTurn.objects.filter(chat=active_chat)
            .order_by("-created_at")[:max_turns]
        )
        for turn in reversed(recent_turns):
            if turn.prompt:
                contents.append(
                    {"role": "user", "parts": [{"text": turn.prompt}]}
                )
            if turn.response:
                contents.append(
                    {"role": "model", "parts": [{"text": turn.response}]}
                )
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    try:
        generated = generate_ai_response({"contents": contents}, system_prompt)
    except GeminiError as exc:
        return JsonResponse(
            {
                "error": exc.message,
                "details": exc.details,
            },
            status=500,
        )

    if not active_chat:
        active_chat = AssistantChat.objects.create(user=request.user)
        set_active_chat(request, active_chat)

    AssistantTurn.objects.create(
        chat=active_chat,
        prompt=prompt,
        response=generated,
    )

    if not (active_chat.title or "").strip():
        active_chat.title = title_from_prompt(prompt)
        active_chat.save(update_fields=["title", "updated_at"])
    else:
        active_chat.save(update_fields=["updated_at"])

    chat_state = build_chat_state(request.user, active_chat)
    return JsonResponse(
        {
            "generated": render_llm_markdown(generated),
            "chat_state": chat_state,
        }
    )


@require_POST
@login_required
def llm_new_chat(request):
    active_chat = get_active_chat(request)
    if active_chat:
        delete_unpinned_chat(active_chat)

    new_chat = AssistantChat.objects.create(user=request.user)
    set_active_chat(request, new_chat)

    return JsonResponse(
        {
            "history": [],
            "chat_state": build_chat_state(request.user, new_chat),
        }
    )


@require_GET
@login_required
def llm_open_chat(request, chat_id):
    active_chat = get_active_chat(request)
    target_chat = get_object_or_404(
        AssistantChat, id=chat_id, user=request.user
    )
    if active_chat and active_chat.id != target_chat.id:
        delete_unpinned_chat(active_chat)
    set_active_chat(request, target_chat)

    return JsonResponse(
        {
            "history": serialize_history(target_chat),
            "chat_state": build_chat_state(request.user, target_chat),
        }
    )


@require_POST
@login_required
def llm_toggle_pin(request, chat_id):
    chat = get_object_or_404(AssistantChat, id=chat_id, user=request.user)
    active_chat = get_active_chat(request)

    if not chat.is_pinned:
        pinned_count = AssistantChat.objects.filter(
            user=request.user, is_pinned=True
        ).count()
        if pinned_count >= PIN_LIMIT:
            return JsonResponse(
                {
                    "error": "You can only pin up to 6 chats. Unpin one to add another.",
                },
                status=400,
            )
        toggle_pin(chat, True)
    else:
        toggle_pin(chat, False)
        if not active_chat or active_chat.id != chat.id:
            chat.delete()

    active_chat = get_active_chat(request)
    return JsonResponse(
        {
            "chat_state": build_chat_state(request.user, active_chat),
        }
    )


@login_required
def llm_page(request):
    return render(request, "assistant/page.html")
