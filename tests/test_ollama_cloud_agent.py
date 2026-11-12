import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from wise_sim.l1_agent import OllamaCloudAgent, Policy


class MockResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "usage": {
                "prompt_tokens": 42,
                "prompt_tokens_details": {"cached_tokens": 18},
            },
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "decisions": [{
                            "id": 17,
                            "booked": True,
                            "abstain": False,
                            "reason": "synthetic test",
                        }]
                    })
                }
            }]
        }).encode("utf-8")


class OllamaCloudAgentTests(unittest.TestCase):
    def test_defaults_to_ollama_cloud_glm_and_openai_compatible_local_endpoint(self):
        with patch.dict(os.environ, {}, clear=True):
            agent = OllamaCloudAgent()
        self.assertEqual(agent.model, "glm-5.3-flash:cloud")
        self.assertEqual(agent.base_url, "http://127.0.0.1:11434/v1")
        self.assertEqual(agent.api_key, "ollama")

    def test_uses_local_ollama_endpoint_and_parses_persona_decision(self):
        persona = SimpleNamespace(
            id=17,
            archetype="synthetic-family",
            spend_index=1.0,
            stay_nights=2,
            describe=lambda: "synthetic persona only",
        )
        agent = OllamaCloudAgent(
            model="glm-5.3-flash:cloud",
            base_url="http://ollama.internal:11434/v1/",
            system_context='{"contract_version":"1.0.0"}',
        )
        with patch(
            "wise_sim.l1_agent.urllib.request.urlopen",
            return_value=MockResponse(),
        ) as urlopen:
            decisions = agent.decide_batch(
                [persona],
                Policy(price_delta=0.0, promo="package"),
                weekend=True,
                seed=23,
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://ollama.internal:11434/v1/chat/completions",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer ollama")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "glm-5.3-flash:cloud")
        self.assertIn('"contract_version":"1.0.0"', body["messages"][0]["content"])
        self.assertIn("synthetic persona only", body["messages"][1]["content"])
        self.assertEqual(
            agent.last_prompt_usage,
            {"prompt_tokens": 42, "cached_prompt_tokens": 18},
        )
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0].booked)
        self.assertFalse(decisions[0].abstained)


if __name__ == "__main__":
    unittest.main()
