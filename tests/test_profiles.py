from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.orchestrator import create_package
from gaia.packaging import build_prompt
from gaia.models import MemorySelection
from gaia.profiles import DEFAULT_PROFILE_ID, PROFILES, get_profile, profile_payloads


class ProfilePromptTests(unittest.TestCase):
    def test_all_profiles_build_distinct_prompt_instructions(self) -> None:
        prompts = {
            profile.id: build_prompt("Проект", "Память", "Запрос", [], profile.id)
            for profile in PROFILES
        }

        self.assertEqual(set(prompts), {profile.id for profile in PROFILES})
        for profile in PROFILES:
            prompt = prompts[profile.id]
            self.assertIn(profile.title, prompt)
            self.assertIn(profile.template, prompt)

        self.assertNotEqual(prompts["general"], prompts["risk_review"])
        self.assertIn("реестр рисков", prompts["risk_review"])
        self.assertIn("кандидаты для обновления проектной памяти", prompts["memory_candidates"])

    def test_unknown_profile_falls_back_to_default(self) -> None:
        default = get_profile(DEFAULT_PROFILE_ID)
        self.assertEqual(get_profile("missing").id, default.id)

    def test_profile_payloads_hide_templates_from_selection_api(self) -> None:
        payloads = profile_payloads()

        self.assertGreaterEqual(len(payloads), 4)
        self.assertTrue(all("id" in item and "title" in item and "description" in item for item in payloads))
        self.assertTrue(all("template" not in item for item in payloads))

    def test_profile_descriptions_are_short_ui_hints(self) -> None:
        descriptions = {item["title"]: item["description"] for item in profile_payloads()}

        self.assertEqual(descriptions["Общий анализ"], "Универсальный разбор.")
        self.assertEqual(descriptions["Записка для решения"], "Управленческий формат.")
        self.assertEqual(descriptions["Риск-анализ"], "Реестр рисков.")
        self.assertEqual(descriptions["Кандидаты в память"], "Подготовка к Scribe.")
        self.assertEqual(descriptions["План действий"], "Задачи и критерии готовности.")

    def test_orchestrator_stores_selected_profile_in_package(self) -> None:
        empty_memory = MemorySelection("", [], 0, [])
        with patch("gaia.orchestrator.write_run_journal"), patch("gaia.orchestrator.select_project_memory", return_value=empty_memory):
            package = create_package("Автопретензии", "Найди риски", [], "risk_review")

        self.assertEqual(package.profile_id, "risk_review")
        self.assertEqual(package.profile_title, "Риск-анализ")
        self.assertIn("реестр рисков", package.prompt)


if __name__ == "__main__":
    unittest.main()
