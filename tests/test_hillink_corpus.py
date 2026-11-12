import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import validate

from wise_sim.hillink_corpus import (
    build_hillink_contract,
    build_stable_prompt_context,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SCHEMA = ROOT / "wise_sim" / "schemas" / "hillink_repository_contract.schema.json"


class HillinkCorpusContractTests(unittest.TestCase):
    def test_extracts_source_manifest_and_structured_contracts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "instagram_persona_sample.csv").write_text(
                "segment,party_size,comment\nfamily,4,raw review text\nadult,2,\n",
                encoding="utf-8",
            )
            (root / "ontology.json").write_text(
                json.dumps({"version": 1, "fields": [{"name": "guest_id"}]}),
                encoding="utf-8",
            )
            (root / "README.md").write_text("# Corpus\n\n## Limits\n", encoding="utf-8")
            (root / "photo.png").write_bytes(b"opaque image")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("not a tracked artifact", encoding="utf-8")

            contract = build_hillink_contract(root)
            validate(instance=contract, schema=json.loads(CONTRACT_SCHEMA.read_text()))

        self.assertEqual(contract["coverage"]["repository_file_count"], 4)
        self.assertEqual(contract["coverage"]["indexed_file_count"], 4)
        self.assertFalse(contract["coverage"]["inventory_complete"])
        self.assertEqual(contract["coverage"]["extraction_status_counts"]["opaque"], 1)
        artifacts = {artifact["path"]: artifact for artifact in contract["artifacts"]}
        table_contract = artifacts["instagram_persona_sample.csv"]["content_contract"]
        self.assertEqual(table_contract["row_count"], 2)
        self.assertEqual(
            table_contract["columns"],
            [
                {"name": "segment", "inferred_type": "string", "nullable": False},
                {"name": "party_size", "inferred_type": "integer", "nullable": False},
                {"name": "comment", "inferred_type": "string", "nullable": True},
            ],
        )
        self.assertEqual(
            artifacts["ontology.json"]["content_contract"]["shape"]["type"],
            "object",
        )
        self.assertEqual(artifacts["photo.png"]["extraction_status"], "opaque")
        self.assertEqual(artifacts["README.md"]["extraction_status"], "metadata_only")

    def test_stable_prompt_context_contains_contract_not_raw_rows(self):
        contract = {
            "contract_version": "1.0.0",
            "source": {"repository": "https://github.com/BK8909/Hillink", "revision": "abc"},
            "coverage": {
                "repository_file_count": 1,
                "indexed_file_count": 1,
                "format_counts": {"csv": 1},
                "domain_counts": {"persona_evidence": 1},
                "inventory_complete": True,
            },
            "artifacts": [{
                "path": "personas.csv",
                "domain": "persona_evidence",
                "format": "csv",
                "content_contract": {
                    "kind": "tabular",
                    "columns": [{"name": "segment", "inferred_type": "string"}],
                },
            }],
        }

        context = build_stable_prompt_context(contract)

        self.assertIn("structured_artifact_count", context)
        self.assertNotIn("personas.csv", context)
        self.assertNotIn("raw review text", context)


if __name__ == "__main__":
    unittest.main()
