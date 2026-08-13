from __future__ import annotations

import http.client
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from gaia.controlled_intake import ControlledIntake
from gaia.operational_context import OperationalContextStore, SafeProvenance, new_evidence, new_item
from gaia.operational_context_retrieval import AuthorityAmbiguity, AuthorityReference
from gaia.operational_context_review import (
    OperationalContextCandidateStore,
    OperationalContextReviewService,
    new_candidate,
)
from gaia.provenance import ProvenanceStore
from gaia.server import Handler


class OperationalContextReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = OperationalContextStore(root)
        self.candidates = OperationalContextCandidateStore(root)
        self.service = OperationalContextReviewService(self.store, self.candidates)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add_candidate(self, *, candidate_id="candidate_1", value="Synthetic state", sensitivity="standard", replaces_id=""):
        candidate = new_candidate(
            scope="project", scope_ref="project_a", kind="requirement", subject_ref="subject_1",
            value=value, provenance=SafeProvenance(candidate_ref=candidate_id), sensitivity=sensitivity,
            reason="Подтверждено локальной проверкой.", candidate_id=candidate_id, replaces_id=replaces_id,
        )
        self.candidates.add(candidate)
        return candidate

    def test_new_candidate_is_reviewed_then_promoted_with_matched_evidence(self) -> None:
        candidate = self.add_candidate()
        before = self.service.view(scope="project", scope_ref="project_a")
        self.assertEqual(before["current_context"], [])
        self.assertEqual(before["requires_decision"][0]["sensitivity"], "Обычный")
        saved = self.service.confirm(candidate.id, actor_ref="actor_1")
        self.assertEqual(saved["content"], "Synthetic state")
        stored = self.store.list_scope(scope="project", scope_ref="project_a")[0]
        evidence = self.store.evidence(scope="project", scope_ref="project_a", evidence_id=stored["confirmation_ref"])
        self.assertEqual(evidence["candidate_ref"], candidate.id)
        self.assertEqual(self.candidates.get(candidate.id).state, "confirmed")
        after = self.service.view(scope="project", scope_ref="project_a")
        self.assertEqual(after["requires_decision"], [])
        self.assertEqual(after["current_context"][0]["content"], "Synthetic state")

    def test_replacement_is_atomic_and_never_shows_two_current_items(self) -> None:
        old = self.add_candidate(candidate_id="candidate_old")
        old_card = self.service.confirm(old.id, actor_ref="actor_1")
        replacement = self.add_candidate(candidate_id="candidate_new", value="New synthetic state", replaces_id=old_card["id"])
        pending = self.service.view(scope="project", scope_ref="project_a")["requires_decision"][0]
        self.assertEqual((pending["previous_content"], pending["content"]), ("Synthetic state", "New synthetic state"))
        self.service.confirm(replacement.id, actor_ref="actor_1")
        values = self.store.list_scope(scope="project", scope_ref="project_a")
        self.assertEqual({(item["value"], item["lifecycle"]) for item in values}, {("Synthetic state", "superseded"), ("New synthetic state", "active")})
        self.assertEqual([item["content"] for item in self.service.view(scope="project", scope_ref="project_a")["current_context"]], ["New synthetic state"])

    def test_rejection_never_creates_runtime_visible_operational_context(self) -> None:
        candidate = self.add_candidate()
        self.service.reject(candidate.id)
        self.assertEqual(self.store.list_scope(scope="project", scope_ref="project_a"), [])
        self.assertEqual(self.service.view(scope="project", scope_ref="project_a")["requires_decision"], [])

    def test_retirement_removes_current_item_without_delete(self) -> None:
        candidate = self.add_candidate()
        card = self.service.confirm(candidate.id, actor_ref="actor_1")
        self.service.retire(scope="project", scope_ref="project_a", item_id=card["id"], actor_ref="actor_1")
        self.assertEqual(self.service.view(scope="project", scope_ref="project_a")["current_context"], [])
        self.assertEqual(self.store.get(scope="project", scope_ref="project_a", item_id=card["id"])["lifecycle"], "retired")
        self.assertEqual(self.service.view(scope="project", scope_ref="project_a")["history"][0]["status"], "Больше не актуально")

    def test_conflict_is_shown_as_needing_clarification_without_winner_action(self) -> None:
        project = self.add_candidate(candidate_id="candidate_a", value="Project alternative")
        project_card = self.service.confirm(project.id, actor_ref="actor_1")
        system_item = new_item(scope="system", scope_ref="system_a", kind="requirement", subject_ref="subject_1", value="System alternative", provenance=SafeProvenance(candidate_ref="candidate_b"), confirmation_ref="oce_b", item_id="oc_b", sensitivity="restricted")
        self.store.create(system_item, new_evidence(scope="system", scope_ref="system_a", action="promotion", target_item_id="oc_b", actor_ref="actor_1", candidate_ref="candidate_b", evidence_id="oce_b"))
        ambiguity = AuthorityAmbiguity("requirement", "subject_1", "restricted", (
            AuthorityReference(project_card["id"], "project", "project_a", {"candidate_ref": "candidate_a"}, "ignored"),
            AuthorityReference("oc_b", "system", "system_a", {"candidate_ref": "candidate_b"}, "oce_b"),
        ))
        card = self.service.view(scope="project", scope_ref="project_a", ambiguities=(ambiguity,))["ambiguities"][0]
        self.assertEqual(card["kind"], "Требование")
        self.assertEqual(card["sensitivity"], "Ограниченный")
        self.assertEqual([item["content"] for item in card["alternatives"]], ["Project alternative", "System alternative"])
        self.assertNotIn("oc_b", str(card))

    def test_retiring_conflict_alternative_resolves_ambiguity(self) -> None:
        project = self.add_candidate(candidate_id="candidate_project", value="Project conflict")
        project_card = self.service.confirm(project.id, actor_ref="actor_1")
        system_item = new_item(scope="system", scope_ref="system_a", kind="requirement", subject_ref="subject_1", value="System conflict", provenance=SafeProvenance(candidate_ref="candidate_system"), confirmation_ref="oce_system", item_id="oc_system")
        self.store.create(system_item, new_evidence(scope="system", scope_ref="system_a", action="promotion", target_item_id="oc_system", actor_ref="actor_1", candidate_ref="candidate_system", evidence_id="oce_system"))
        ambiguity = AuthorityAmbiguity("requirement", "subject_1", "standard", (AuthorityReference(project_card["id"], "project", "project_a", {}, ""), AuthorityReference("oc_system", "system", "system_a", {}, "")))
        card = self.service._ambiguity_card(ambiguity)
        self.service.retire_ambiguity_alternative(card["review_ref"], 1, (ambiguity,), actor_ref="actor_1")
        self.assertEqual(self.store.get(scope="system", scope_ref="system_a", item_id="oc_system")["lifecycle"], "retired")
        view = self.service.view(
            scope="project", scope_ref="project_a", history_scopes=(("system", "system_a"),),
        )
        self.assertIn(
            {"content": "System conflict", "status": "Больше не актуально", "source": "Локальный подтверждённый источник", "sensitivity": "Обычный"},
            view["history"],
        )

    def test_deferred_conflict_is_discoverable_but_not_active(self) -> None:
        first = self.add_candidate(candidate_id="candidate_first", value="First")
        first_card = self.service.confirm(first.id, actor_ref="actor_1")
        second = new_item(scope="system", scope_ref="system_a", kind="requirement", subject_ref="subject_1", value="Second", provenance=SafeProvenance(candidate_ref="candidate_second"), confirmation_ref="oce_second", item_id="oc_second")
        self.store.create(second, new_evidence(scope="system", scope_ref="system_a", action="promotion", target_item_id="oc_second", actor_ref="actor_1", candidate_ref="candidate_second", evidence_id="oce_second"))
        ambiguity = AuthorityAmbiguity("requirement", "subject_1", "standard", (AuthorityReference(first_card["id"], "project", "project_a", {}, ""), AuthorityReference("oc_second", "system", "system_a", {}, "")))
        ref = self.service._ambiguity_ref(ambiguity); self.service.defer_ambiguity(ref, (ambiguity,))
        view = self.service.view(scope="project", scope_ref="project_a", ambiguities=(ambiguity,))
        self.assertEqual(view["ambiguities"], [])
        self.assertEqual(view["deferred_ambiguities"][0]["review_ref"], ref)

    def test_restricted_cards_are_locally_labeled_without_raw_provenance(self) -> None:
        self.add_candidate(candidate_id="candidate_restricted", sensitivity="restricted")
        card = self.service.view(scope="project", scope_ref="project_a")["requires_decision"][0]
        self.assertEqual(card["sensitivity"], "Ограниченный")
        self.assertNotIn("candidate_restricted", card["source"])
        self.assertNotIn("provenance", card)

    def test_current_view_excludes_pending_rejected_superseded_and_retired_items(self) -> None:
        old = self.add_candidate(candidate_id="candidate_old")
        old_card = self.service.confirm(old.id, actor_ref="actor_1")
        replacement = self.add_candidate(candidate_id="candidate_new", replaces_id=old_card["id"])
        self.service.confirm(replacement.id, actor_ref="actor_1")
        rejected = self.add_candidate(candidate_id="candidate_rejected", value="Never current", replaces_id="")
        self.service.reject(rejected.id)
        active = self.service.view(scope="project", scope_ref="project_a")["current_context"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["content"], "Synthetic state")

    def test_http_review_view_uses_the_isolated_v0_candidate_queue(self) -> None:
        temporary = tempfile.TemporaryDirectory(); provenance = ProvenanceStore(Path(temporary.name) / "store")
        workspace = ControlledIntake(provenance)._workspace_for("demo")
        candidate_store = OperationalContextCandidateStore(provenance.root)
        candidate_store.add(new_candidate(
            scope="project", scope_ref=workspace, kind="risk", subject_ref="demo_risk",
            value="Synthetic pending risk", provenance=SafeProvenance(candidate_ref="demo_candidate"),
            candidate_id="demo_candidate",
        ))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch("gaia.controlled_intake.default_store", return_value=provenance):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("GET", "/api/operational-context/review?project=demo")
                response = connection.getresponse(); payload = __import__("json").loads(response.read()); connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["requires_decision"][0]["content"], "Synthetic pending risk")
            self.assertEqual(payload["current_context"], [])
        finally:
            server.shutdown(); server.server_close(); temporary.cleanup()

    def test_confirm_and_reject_views_do_not_depend_on_legacy_summary(self) -> None:
        confirm = self.add_candidate(candidate_id="candidate_confirm", value="Confirmed now")
        reject = self.add_candidate(candidate_id="candidate_reject", value="Rejected now", replaces_id="")
        self.service.confirm(confirm.id, actor_ref="actor_1")
        self.service.reject(reject.id)
        view = self.service.view(scope="project", scope_ref="project_a")
        self.assertEqual(view["requires_decision"], [])
        self.assertEqual([item["content"] for item in view["current_context"]], ["Confirmed now"])
        self.assertEqual({item["status"] for item in view["history"]}, {"Подтверждено", "Отклонено"})

    def test_http_confirm_returns_updated_review_and_current_view(self) -> None:
        temporary = tempfile.TemporaryDirectory(); provenance = ProvenanceStore(Path(temporary.name) / "store")
        workspace = ControlledIntake(provenance)._workspace_for("demo")
        candidates = OperationalContextCandidateStore(provenance.root)
        candidates.add(new_candidate(scope="project", scope_ref=workspace, kind="risk", subject_ref="demo_confirm", value="Confirmed through HTTP", provenance=SafeProvenance(candidate_ref="demo_confirm"), candidate_id="demo_confirm"))
        candidates.add(new_candidate(scope="project", scope_ref=workspace, kind="risk", subject_ref="demo_reject", value="Rejected through HTTP", provenance=SafeProvenance(candidate_ref="demo_reject"), candidate_id="demo_reject"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch("gaia.controlled_intake.default_store", return_value=provenance):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("GET", "/"); initial = connection.getresponse(); initial.read(); cookie = initial.getheader("Set-Cookie").split(";", 1)[0]
                host = f"127.0.0.1:{server.server_address[1]}"
                connection.request("POST", "/api/operational-context/review/demo_confirm/confirm", body='{"project":"demo"}', headers={"Content-Type": "application/json", "Cookie": cookie, "Host": host, "Origin": f"http://{host}"})
                response = connection.getresponse(); payload = __import__("json").loads(response.read()); connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["current_context"][0]["content"], "Confirmed through HTTP")
            with patch("gaia.controlled_intake.default_store", return_value=provenance):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("POST", "/api/operational-context/review/demo_reject/reject", body='{"project":"demo"}', headers={"Content-Type": "application/json", "Cookie": cookie, "Host": host, "Origin": f"http://{host}"})
                response = connection.getresponse(); payload = __import__("json").loads(response.read()); connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["requires_decision"], [])
            self.assertIn({"content": "Rejected through HTTP", "status": "Отклонено", "source": "Локальный подтверждаемый источник", "sensitivity": "Обычный"}, payload["history"])
        finally:
            server.shutdown(); server.server_close(); temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
