from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

class GoalAIPlannerPersistenceTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        from learning_insights.models import AIPlanRun, Goal

        self.AIPlanRun = AIPlanRun
        self.Goal = Goal
        self.user = get_user_model().objects.create_user(username="learner", password="test-pass")
        self.client.force_login(self.user)

    def _build_prompt_plan_run(self):
        today = timezone.localdate()
        return self.AIPlanRun.objects.create(
            user=self.user,
            kind=self.AIPlanRun.KIND_PROMPT_PLAN,
            period_type=self.Goal.PERIOD_DAILY,
            period_start=today,
            period_end=today,
            input_payload={"user_prompt": "plan for exam prep"},
            output_payload={
                "type": "plan",
                "period": {"start": today.isoformat(), "end": today.isoformat()},
                "plan": [
                    {
                        "date": today.isoformat(),
                        "items": [
                            {
                                "task": "Review chapter 1",
                                "minutes": 20,
                                "notes": "Focus on key definitions",
                            }
                        ],
                    }
                ],
            },
            status=self.AIPlanRun.STATUS_SUCCESS,
            summary_text="Generated plan",
        )

    @patch("learning_insights.views.generate_prompt_plan_run")
    def test_generate_does_not_create_goal_rows(self, mock_generate_prompt_plan_run):
        run = self._build_prompt_plan_run()
        mock_generate_prompt_plan_run.return_value = run

        response = self.client.post(
            reverse("learning_insights:goal_ai_planner"),
            {"action": "start", "prompt": "Plan 20 minutes of study"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"ai_run={run.id}", response.url)
        self.assertEqual(self.Goal.objects.filter(user=self.user).count(), 0)

    def test_apply_creates_goal_rows(self):
        run = self._build_prompt_plan_run()

        response = self.client.post(reverse("learning_insights:goal_ai_apply", kwargs={"pk": run.id}))

        self.assertEqual(response.status_code, 302)
        self.assertGreater(self.Goal.objects.filter(user=self.user).count(), 0)

    def test_clear_deletes_unapplied_prompt_plan_run(self):
        run = self._build_prompt_plan_run()

        response = self.client.post(reverse("learning_insights:goal_ai_clear", kwargs={"pk": run.id}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("learning_insights:goal_create"))
        self.assertFalse(self.AIPlanRun.objects.filter(pk=run.id).exists())

    def test_clear_keeps_applied_prompt_plan_run(self):
        run = self._build_prompt_plan_run()
        run.applied_at = timezone.now() - timedelta(minutes=1)
        run.save(update_fields=["applied_at", "updated_at"])

        response = self.client.post(reverse("learning_insights:goal_ai_clear", kwargs={"pk": run.id}))

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"ai_run={run.id}", response.url)
        self.assertTrue(self.AIPlanRun.objects.filter(pk=run.id).exists())
