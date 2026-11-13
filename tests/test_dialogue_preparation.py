"""The existing Dialogue package remains local and keeps native source lineage."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from gaia.controlled_intake import ControlledIntake
from gaia.context_compiler import ContextCompiler, ContextService
from gaia.orchestrator import create_package
from gaia.provenance import ProvenanceStore
from gaia.review import ReviewService, readonly_review


class DialoguePreparationTests(unittest.TestCase):
    def package(self, root, reader=None, *, strict=True, query="резервный"):
        with (
            patch("gaia.orchestrator.SETTINGS", SimpleNamespace(runs_dir=root / "runs")),
            patch("gaia.orchestrator.select_project_memory", return_value=None),
            patch("gaia.orchestrator.write_run_journal"),
            patch("gaia.orchestrator.journal_path", return_value=""),
            patch("gaia.orchestrator.safety_audit_path", return_value=""),
            patch("gaia.module_assist.call_lm_studio_with_deadline", side_effect=AssertionError("No model for preparation")),
        ):
            return create_package("synthetic", query, [], strict_dialog_privacy=strict, dialogue_context_reader=reader)

    def test_real_compiler_accepted_context_and_source_are_the_same_in_existing_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=ProvenanceStore(root / "store");intake=ControlledIntake(store)
            candidates=[]
            for index,text in enumerate(["Использовать резервный маршрут.","Резервный бюджет пока не согласован."]):
                admitted=intake.admit("synthetic", [(f"letter-{index}.txt", text.encode())])
                workspace=admitted["workspace_id"];current=admitted["materials"][0]["sanitized_id"]
                review=ReviewService(store, workspace, lambda _: {"status":"completed","findings":[]})
                review.start(current);cleaned=review.confirm(current)
                if index==0:artifact=current;accepted_text=cleaned
                def model(fragment):
                    return {"candidates":[{"type":"risk" if index else "decision", "title":"Резервный бюджет" if index else "Резервный маршрут", "statement":fragment.strip(), "block":{"start":0,"end":len(fragment)}, "confidence":"high", "requires_review":True}]}
                candidates.extend(ContextCompiler(store, workspace, model).compile(current))
            context=ContextService(store, workspace);context.decide(candidates[0]["id"],"confirm")
            before={str(p.relative_to(store.root)):p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
            package=self.package(root, context)
            included=package.dialogue_context.current_authority
            self.assertEqual(len(included),1)
            self.assertEqual(included[0].id,candidates[0]["id"])
            self.assertEqual(included[0].source_links,(artifact,))
            self.assertTrue(included[0].block_links)
            self.assertIn(included[0].statement,package.prompt)
            self.assertNotIn(candidates[1]["statement"],package.prompt)
            self.assertEqual(readonly_review(store,workspace,artifact)["cleaned_text"],accepted_text)
            self.assertEqual(before,{str(p.relative_to(store.root)):p.read_bytes() for p in store.root.rglob("*") if p.is_file()})
            self.assertTrue(package.local_fallback_required)
            self.assertFalse(package.safe_for_codex_after_confirmation)

    def test_clear_pii_check_does_not_grant_dialogue_disclosure_even_without_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            ordinary=self.package(root,strict=False,query="Синтетический вопрос")
            self.assertTrue(ordinary.safe_for_codex_after_confirmation)
            local=self.package(root,query="Синтетический вопрос")
            self.assertFalse(local.safe_for_codex_after_confirmation)
            self.assertTrue(local.local_fallback_required)
            self.assertIsNone(local.dialogue_context)
            self.assertIn("Разрешение внешней передачи не установлено", " ".join(local.policy_notes))

    def test_empty_confirmed_selection_stays_distinct_from_missing_reader(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=ProvenanceStore(root / "store");workspace=store.create_workspace()
            empty=self.package(root,ContextService(store,workspace))
            self.assertEqual(empty.dialogue_context.current_authority,())
            self.assertEqual(empty.dialogue_context.metadata.included_context_count,0)
            self.assertIsNone(self.package(root).dialogue_context)


if __name__=="__main__":unittest.main()
