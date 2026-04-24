from typing import Any, cast
from contextlib import ExitStack
from unittest.mock import Mock, patch
from types import SimpleNamespace
import sys
from types import ModuleType

import requests
from django.test import RequestFactory, SimpleTestCase, override_settings

from .services import GeminiError, generate_ai_response, stream_ai_response

# Create your tests here.


class GeminiServiceTests(SimpleTestCase):
	@override_settings(API1_KEY="test-key")
	@patch("assistant.services.requests.post")
	def test_generate_ai_response_surfaces_network_error(self, mock_post):
		mock_post.side_effect = requests.exceptions.ConnectionError("connection reset")

		with self.assertRaises(GeminiError) as context:
			generate_ai_response(
				{"prompt": "Hello"},
				"You are a helpful assistant.",
			)

		self.assertIn("Network error while contacting Gemini", context.exception.message)
		self.assertIn("connection reset", context.exception.message)

	@override_settings(API1_KEY="test-key")
	@patch("assistant.services.requests.post")
	def test_generate_ai_response_surfaces_overload_error(self, mock_post):
		response = Mock()
		response.status_code = 503
		response.json.return_value = {
			"error": {
				"message": (
					"This model is currently experiencing high demand. "
					"Spikes in demand are usually temporary. Please try again later."
				)
			}
		}
		mock_post.return_value = response

		with self.assertRaises(GeminiError) as context:
			generate_ai_response(
				{"prompt": "Hello"},
				"You are a helpful assistant.",
			)

		self.assertEqual(
			"Gemini is temporarily overloaded. Please try again in a moment.",
			context.exception.message,
		)
		details = context.exception.details or {}
		self.assertEqual(503, details["status"])

	@override_settings(API1_KEY="test-key")
	@patch("assistant.services.requests.post")
	def test_generate_ai_response_surfaces_high_demand_429(self, mock_post):
		response = Mock()
		response.status_code = 429
		response.json.return_value = {
			"error": {
				"message": (
					"This model is currently experiencing high demand. "
					"Spikes in demand are usually temporary. Please try again later."
				)
			}
		}
		mock_post.return_value = response

		with self.assertRaises(GeminiError) as context:
			generate_ai_response(
				{"prompt": "Hello"},
				"You are a helpful assistant.",
			)

		self.assertEqual(
			"Gemini is temporarily overloaded. Please try again in a moment.",
			context.exception.message,
		)
		details = context.exception.details or {}
		self.assertEqual(429, details["status"])

	@override_settings(API1_KEY="test-key")
	@patch("assistant.services.requests.post")
	def test_generate_ai_response_surfaces_prompt_too_large(self, mock_post):
		response = Mock()
		response.status_code = 400
		response.json.return_value = {
			"error": {"message": "The prompt is too large for this model."}
		}
		mock_post.return_value = response

		with self.assertRaises(GeminiError) as context:
			generate_ai_response(
				{"prompt": "Hello"},
				"You are a helpful assistant.",
			)

		self.assertEqual(
			"The prompt is too long for the current model. Split it into smaller parts and try again.",
			context.exception.message,
		)
		details = context.exception.details or {}
		self.assertEqual(400, details["status"])


class GeminiStreamTests(SimpleTestCase):
	@override_settings(API1_KEY="test-key")
	@patch("assistant.services.requests.post")
	def test_stream_ai_response_yields_tokens(self, mock_post):
		response = Mock()
		response.status_code = 200
		response.iter_lines.return_value = [
			'data: {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]}',
			"",
			'data: {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}',
			"",
		]
		mock_post.return_value = response

		chunks = list(
			stream_ai_response(
				{"prompt": "Hello"},
				"You are a helpful assistant.",
			)
		)

		self.assertEqual(["Hel", "lo"], chunks)
		mock_post.assert_called_once()

	@override_settings(API1_KEY="test-key")
	@patch("assistant.services.requests.post")
	def test_stream_ai_response_preserves_long_output(self, mock_post):
		response = Mock()
		response.status_code = 200
		response.iter_lines.return_value = [
			'data: {"candidates": [{"content": {"parts": [{"text": "This is a detailed explanation with multiple steps. "}]}}]}',
			"",
			'data: {"candidates": [{"content": {"parts": [{"text": "This is a detailed explanation with multiple steps. It keeps going with more context and examples."}]}}]}',
			"",
		]
		mock_post.return_value = response

		chunks = list(
			stream_ai_response(
				{"prompt": "Explain in detail."},
				"You are a helpful assistant.",
			)
		)

		self.assertEqual(
			[
				"This is a detailed explanation with multiple steps. ",
				"It keeps going with more context and examples.",
			],
			chunks,
		)
		self.assertEqual(
			"This is a detailed explanation with multiple steps. It keeps going with more context and examples.",
			"".join(chunks),
		)


class AssistantStreamingViewTests(SimpleTestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.user = SimpleNamespace(is_authenticated=True)

	@override_settings(API1_KEY="test-key")
	def test_llm_generate_streams_sse_events(self):
		fake_markdown = ModuleType("markdown")
		cast(Any, fake_markdown).markdown = lambda value, *args, **kwargs: value
		with patch.dict(sys.modules, {"markdown": fake_markdown}):
			from . import views as views_module

			with ExitStack() as stack:
				mock_stream = stack.enter_context(
					patch.object(
						views_module,
						"stream_ai_response",
						return_value=iter(["Hel", "lo"]),
					)
				)
				mock_turn_query = stack.enter_context(
					patch.object(
						views_module.AssistantTurn.objects,
						"filter",
					)
				)
				mock_build_state = stack.enter_context(
					patch.object(views_module, "build_chat_state")
				)
				mock_set_active_chat = stack.enter_context(
					patch.object(views_module, "set_active_chat")
				)
				mock_get_active_chat = stack.enter_context(
					patch.object(views_module, "get_active_chat")
				)
				mock_turn_create = stack.enter_context(
					patch.object(views_module.AssistantTurn.objects, "create")
				)
				mock_chat_create = stack.enter_context(
					patch.object(views_module.AssistantChat.objects, "create")
				)

				mock_chat = Mock(id=1, title="", is_pinned=False)
				mock_chat.save = Mock()
				mock_chat.pk = 1
				mock_chat_create.return_value = mock_chat
				mock_turns_queryset = Mock()
				mock_turns_queryset.order_by.return_value = []
				mock_turn_query.return_value = mock_turns_queryset
				mock_get_active_chat.return_value = None
				mock_set_active_chat.return_value = None
				mock_build_state.side_effect = lambda user, chat: {
					"active_chat_id": getattr(chat, "id", None),
					"pinned_chats": [],
					"temp_chat": {
						"id": getattr(chat, "id", None),
						"title": getattr(chat, "title", ""),
						"is_pinned": False,
					},
					"max_pins": 6,
				}

				request = self.factory.post("/assistant/llm/generate/", {"prompt": "Hello"})
				request_any = cast(Any, request)
				request_any.user = self.user
				request_any.session = {}

				llm_generate = views_module.llm_generate

				response = llm_generate(request)

				self.assertEqual(200, response.status_code)
				self.assertTrue(response.streaming)
				body = b"".join(cast(Any, response).streaming_content).decode("utf-8")
				self.assertIn('event: token\ndata: {"text": "Hel"}', body)
				self.assertIn('event: token\ndata: {"text": "lo"}', body)
				self.assertIn('event: state', body)
				self.assertIn('event: done', body)
				mock_chat_create.assert_called_once()
				mock_turn_create.assert_called_once()
				mock_stream.assert_called_once()
				stream_args, stream_kwargs = mock_stream.call_args
				self.assertNotIn("maxOutputTokens", stream_args[0])
				self.assertEqual(0.2, stream_args[0]["temperature"])
