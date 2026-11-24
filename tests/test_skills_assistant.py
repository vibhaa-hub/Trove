from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from trove_agent.agents.assistant import SkillAssistant
from trove_agent.discovery.bootstrap import DiscoveryBootstrap
from trove_agent.services.legacy import LegacyService
from trove_agent.agents.skill_registry import SkillRegistry
from trove_agent.core.store import TroveService, ValidationError


def call(name: str, args: dict, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class ScriptedMistral:
    """Fake Mistral client that replays tool calls and records what it was sent."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(complete=self.complete)

    def complete(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        step = self.script.pop(0)
        message = (SimpleNamespace(content="", tool_calls=step) if isinstance(step, list)
                   else SimpleNamespace(content=step, tool_calls=None))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class SkillRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills = SkillRegistry()

    def test_catalog_is_one_line_per_public_skill_and_excludes_bodies(self) -> None:
        catalog = self.skills.catalog_text()
        self.assertIn("discovery-playbook:", catalog)
        self.assertNotIn("document-extraction", catalog)  # internal skill
        self.assertNotIn("## Procedure", catalog)
        self.assertEqual(len(catalog.splitlines()), len(self.skills.public()))

    def test_resources_are_listed_and_traversal_is_blocked(self) -> None:
        card = self.skills.cards["discovery-playbook"]
        self.assertIn("india.md", card.resources)
        self.assertIn("UDGAM", self.skills.resource("discovery-playbook", "reference/india.md"))
        for bad in ("../SKILL.md", "../../legacy-release/SKILL.md", "..\\x.md", "/etc/passwd"):
            with self.assertRaises(KeyError):
                self.skills.resource("discovery-playbook", bad)

    def test_router_and_offline_summary(self) -> None:
        self.assertEqual(self.skills.route("Why is the vault still sealed? which gates")[0], "legacy-release")
        self.assertIn("nominee-verification", self.skills.route("my nominee is locked out of MFA"))
        self.assertTrue(self.skills.offline_summary("fraud-signals"))


class SkillAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.service = TroveService(Path(self.tmp.name) / "a.db", llm_agent=object(),
                                    assistant_agent=object())
        self.legacy = LegacyService(self.service)
        self.discovery = DiscoveryBootstrap(self.service)
        self.case = self.service.create_case(
            {"mode": "discovery", "subject_name": "Asha Rao", "jurisdiction": "India"})
        self.legacy.add_nominee(self.case["id"], {
            "full_name": "Ravi Secretname", "relationship": "son", "email": "ravi@example.test",
            "phone": "+919812345678"})
        sms = "VM-HDFCBK: Rs 500 debited from A/c XX4321"
        self.discovery.ingest(self.case["id"], {
            "source_type": "sms_export", "file_name": "s.txt",
            "content_base64": base64.b64encode(sms.encode()).decode()})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_local_mode_uses_skills_and_never_calls_llm(self) -> None:
        client = ScriptedMistral([])
        assistant = SkillAssistant(self.service, self.legacy, self.discovery, client=client)
        result = assistant.ask(self.case["id"], {"question": "What assets are still missing?"})
        self.assertFalse(result["llm_used"])
        self.assertEqual(client.requests, [])
        self.assertIn("discovery-playbook", result["trace"]["skills_loaded"])
        self.assertIn("HDFC Bank", result["answer"])
        self.assertIn("Still missing", result["answer"])

    def test_llm_requires_consent(self) -> None:
        assistant = SkillAssistant(self.service, self.legacy, self.discovery, client=ScriptedMistral([]))
        with self.assertRaises(ValidationError):
            assistant.ask(self.case["id"], {"question": "hi", "use_llm": True})

    def test_progressive_disclosure_with_llm(self) -> None:
        client = ScriptedMistral([
            [call("get_discovery_overview", {}, "c0")],          # not unlocked yet
            [call("load_skill", {"name": "discovery-playbook"}, "c1")],
            [call("read_skill_resource", {"skill": "discovery-playbook", "file": "reference/india.md"}, "c2"),
             call("get_discovery_overview", {}, "c3")],
            "1. Export SMS. 2. Search RBI UDGAM.",
        ])
        assistant = SkillAssistant(self.service, self.legacy, self.discovery, client=client)
        result = assistant.ask(self.case["id"], {
            "question": "Where else should we look?", "use_llm": True,
            "consent_to_send_case_summary_to_mistral": True})
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["trace"]["skills_loaded"], ["discovery-playbook"])
        self.assertEqual(result["trace"]["tools_called"], ["get_discovery_overview"])
        self.assertEqual(result["trace"]["resources_read"], ["discovery-playbook/reference/india.md"])

        first = client.requests[0]
        self.assertNotIn("## Procedure", first["messages"][0]["content"])   # level 1 only
        self.assertEqual({t["function"]["name"] for t in first["tools"]},
                         {"load_skill", "read_skill_resource", "search_knowledge"})
        locked_reply = client.requests[1]["messages"][-1]["content"]
        self.assertIn("not available", locked_reply)
        self.assertIn("get_discovery_overview",
                      {t["function"]["name"] for t in client.requests[2]["tools"]})

        everything_sent = json.dumps(client.requests)
        for secret in ("Asha Rao", "Ravi Secretname", "ravi@example.test", "9812345678", "4321"):
            self.assertNotIn(secret, everything_sent)

    def test_missing_api_key_falls_back_to_local(self) -> None:
        assistant = SkillAssistant(self.service, self.legacy, self.discovery, client=None)
        import os
        saved = os.environ.pop("MISTRAL_API_KEY", None)
        try:
            result = assistant.ask(self.case["id"], {
                "question": "nominee verification progress", "use_llm": True,
                "consent_to_send_case_summary_to_mistral": True})
        finally:
            if saved is not None:
                os.environ["MISTRAL_API_KEY"] = saved
        self.assertFalse(result["llm_used"])
        self.assertIn("MISTRAL_API_KEY", result["note"])
        self.assertIn("0/1 fully verified", result["answer"])


if __name__ == "__main__":
    unittest.main()
