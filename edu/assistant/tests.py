from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from .services import GeminiError, generate_ai_response

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
