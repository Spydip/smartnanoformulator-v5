import json
import os
import sqlite3
import unittest
from unittest.mock import patch

import llm_explainer


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.data


class LLMExplainerTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def env(self, **extra):
        values = {
            "LLM_ENABLED": "1", "OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-5.4-nano",
            "OPENAI_BASE_URL": "https://provider.invalid/v1", "LLM_DAILY_REQUEST_LIMIT": "1",
            "LLM_MONTHLY_SPEND_USD": "1.00", "LLM_REQUEST_TIMEOUT_SECONDS": "5",
        }
        values.update(extra)
        return patch.dict(os.environ, values, clear=True)

    def test_default_off_never_calls_provider(self):
        with patch.dict(os.environ, {}, clear=True), patch("llm_explainer.urllib.request.urlopen") as request:
            response = llm_explainer.generate(self.con, feature="test", user_key="u",
                system_prompt="Use evidence.", user_prompt="Explain.", evidence=[{"source_id": "R1"}])
        self.assertEqual(response["status"], "disabled")
        request.assert_not_called()

    def test_unpriced_model_fails_closed(self):
        with self.env(OPENAI_MODEL="custom-unknown-model"), patch("llm_explainer.urllib.request.urlopen") as request:
            response = llm_explainer.generate(self.con, feature="test", user_key="u",
                system_prompt="Use evidence.", user_prompt="Explain.", evidence=[{"source_id": "R1"}])
        self.assertEqual(response["status"], "model_has_no_maintained_price")
        request.assert_not_called()

    def test_success_is_logged_and_daily_limit_blocks_second_request(self):
        payload = {"model": "gpt-5.4-nano", "choices": [{"message": {"content": "The record reports a batch observation [E1]."}}],
                   "usage": {"prompt_tokens": 120, "completion_tokens": 14}}
        with self.env(), patch("llm_explainer.urllib.request.urlopen", return_value=FakeResponse(payload)) as request:
            first = llm_explainer.generate(self.con, feature="test", user_key="user:alice",
                system_prompt="Use only evidence.", user_prompt="Explain.", evidence=[{"source_type": "paper", "source_id": "P1", "record_id": "F1"}])
            second = llm_explainer.generate(self.con, feature="test", user_key="user:alice",
                system_prompt="Use only evidence.", user_prompt="Explain.", evidence=[{"source_type": "paper", "source_id": "P1"}])
        self.assertEqual(first["status"], "success")
        self.assertIn("[E1]", first["answer"])
        self.assertGreater(first["usage"]["estimated_cost_usd"], 0)
        self.assertEqual(second["status"], "daily_request_cap")
        self.assertEqual(request.call_count, 1)
        row = self.con.execute("SELECT status,input_tokens,output_tokens,estimated_cost_usd,latency_ms FROM llm_usage").fetchone()
        self.assertEqual(row[0], "success")
        self.assertEqual(row[1:3], (120, 14))
        self.assertGreater(row[3], 0)

    def test_invalid_citation_or_doi_is_rejected(self):
        answer, status = llm_explainer.validate_citations("A claim [E2].", {"E1"})
        self.assertIsNone(answer)
        self.assertEqual(status, "unsupported_citation_id")
        answer, status = llm_explainer.validate_citations("A claim [E1] cites 10.9999/fake.", {"E1"}, set())
        self.assertIsNone(answer)
        self.assertEqual(status, "unsupported_doi")
        answer, status = llm_explainer.validate_citations("A claim [E1] cites 10.1234/source.", {"E1"}, {"10.1234/source"})
        self.assertEqual(status, "validated_supplied_record_ids")
        self.assertNotIn("10.1234/source", answer)

    def test_no_records_means_no_provider_call(self):
        with self.env(), patch("llm_explainer.urllib.request.urlopen") as request:
            result = llm_explainer.explain_analysis(self.con, 5, {"input": {}, "carrier_results": []}, "user:a")
        self.assertEqual(result["status"], "no_retrieved_evidence")
        request.assert_not_called()

    def test_result_evidence_preserves_direct_related_context_and_missing_property(self):
        result = {
            "analysis_id": 4,
            "input": {"ingredient_id": 11},
            "values_used": {"mw": None},
            "provenance": {},
            "property_sources": {"mw": {"status": "No source observation", "value": None}},
            "carrier_results": [{"carrier": "Liposome", "direct_batches": 1,
                "analogue_batches": 2, "other_context_batches": 3,
                "candidates": [
                    {"match_class": "direct", "Formulation_ID": "F1"},
                    {"match_class": "related_same_class", "Formulation_ID": "F2"},
                    {"match_class": "other_context", "Formulation_ID": "F3"},
                ]}],
            "organ_context": {"targeting_examples_by_carrier": [{"carrier": "Liposome",
                "direct_examples": 1, "same_class_examples": 2, "other_drug_context_examples": 3,
                "direct": [], "same_class": [], "context": []}]},
        }
        records = llm_explainer._result_evidence(result)
        branches = {x["source_type"] for x in records}
        self.assertIn("analysis_count_summary", branches)
        self.assertIn("direct", branches)
        self.assertIn("related_same_class", branches)
        self.assertIn("other_context", branches)
        missing = [x for x in records if x.get("source_type") == "missing_property"]
        self.assertTrue(any(x.get("property") == "Molecular weight" for x in missing))
        self.assertEqual(records[0]["counts_by_carrier"][0]["other_ingredient_context_batches"], 3)

    def test_ollama_local_provider_needs_no_api_key_and_has_zero_cost(self):
        payload = {"model": "qwen3:4b", "choices": [{"message": {"content": "Exploratory hypothesis: screen a phospholipid-based vesicle concept and measure vesicle size [not used]."}}],
                   "usage": {"prompt_tokens": 90, "completion_tokens": 30}}
        env = {
            "AI_PROVIDER": "ollama", "LLM_ENABLED": "1", "OLLAMA_MODEL": "qwen3:4b",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1", "LLM_DAILY_REQUEST_LIMIT": "5",
            "LLM_REQUEST_TIMEOUT_SECONDS": "180",
        }
        with patch.dict(os.environ, env, clear=True), patch("llm_explainer.urllib.request.urlopen", return_value=FakeResponse(payload)) as request:
            cfg = llm_explainer.settings()
            response = llm_explainer.generate(self.con, feature="hypothesis", user_key="local",
                system_prompt="Create an exploratory hypothesis.", user_prompt="No evidence was retrieved.",
                evidence=[], allow_hypothesis_without_evidence=True)
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["provider"], "ollama")
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["usage"]["estimated_cost_usd"], 0.0)
        call = request.call_args.args[0]
        self.assertEqual(call.full_url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertIsNone(call.get_header("Authorization"))
        body = json.loads(call.data.decode("utf-8"))
        self.assertEqual(body["model"], "qwen3:4b")
        self.assertIn("max_tokens", body)

    def test_openrouter_free_provider_uses_free_router_and_zero_cost(self):
        payload = {"model": "provider/free-example:free", "choices": [{"message": {"content": "The retrieved paper describes the formulation [E1]."}}],
                   "usage": {"prompt_tokens": 120, "completion_tokens": 20}}
        env = {
            "AI_PROVIDER": "openrouter", "LLM_ENABLED": "1", "OPENROUTER_API_KEY": "or-test-key",
            "OPENROUTER_MODEL": "openrouter/free", "OPENROUTER_BASE_URL": "https://openrouter.invalid/api/v1",
            "LLM_DAILY_REQUEST_LIMIT": "1", "LLM_MONTHLY_SPEND_USD": "0.00", "LLM_REQUEST_TIMEOUT_SECONDS": "10",
        }
        with patch.dict(os.environ, env, clear=True), patch("llm_explainer.urllib.request.urlopen", return_value=FakeResponse(payload)) as request:
            cfg = llm_explainer.settings()
            result = llm_explainer.generate(self.con, feature="rag", user_key="user:mates",
                system_prompt="Use cited records only.", user_prompt="Summarize.", evidence=[{"source_type": "paper", "source_id": "P1"}])
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["provider"], "openrouter")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["usage"]["estimated_cost_usd"], 0.0)
        call = request.call_args.args[0]
        self.assertEqual(call.full_url, "https://openrouter.invalid/api/v1/chat/completions")
        self.assertEqual(call.get_header("Authorization"), "Bearer or-test-key")
        self.assertEqual(call.get_header("X-title"), "SmartNanoFormulator")
        body = json.loads(call.data.decode("utf-8"))
        self.assertEqual(body["model"], "openrouter/free")
        self.assertIn("max_tokens", body)

    def test_openrouter_rejects_paid_model_ids(self):
        env = {"AI_PROVIDER": "openrouter", "LLM_ENABLED": "1", "OPENROUTER_API_KEY": "or-test-key",
               "OPENROUTER_MODEL": "openai/gpt-4o"}
        with patch.dict(os.environ, env, clear=True), patch("llm_explainer.urllib.request.urlopen") as request:
            cfg = llm_explainer.settings()
            result = llm_explainer.generate(self.con, feature="rag", user_key="u",
                system_prompt="Use evidence.", user_prompt="Explain.", evidence=[{"source_id": "P1"}])
        self.assertFalse(cfg["configured"])
        self.assertEqual(cfg["status"], "model_not_marked_free")
        self.assertEqual(result["status"], "model_not_marked_free")
        request.assert_not_called()

    def test_hypothesis_mode_rejects_invented_doi_and_citations(self):
        answer, status = llm_explainer.validate_hypothesis_answer("A proposed idea [E1].")
        self.assertIsNone(answer)
        self.assertEqual(status, "unsupported_reference_in_hypothesis")
        answer, status = llm_explainer.validate_hypothesis_answer("A proposed starting concept; verify it experimentally.")
        self.assertIsNotNone(answer)
        self.assertEqual(status, "hypothesis_only_no_literature_evidence")



if __name__ == "__main__":
    unittest.main()
