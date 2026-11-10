import json
import unittest
from pathlib import Path
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_PATH = ROOT / "wise_sim" / "wise_hillink_ontology_map.json"
SCHEMA_PATH = ROOT / "wise_sim" / "schemas" / "reservation_exposure.schema.json"


def build_validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    ontology = json.loads(ONTOLOGY_PATH.read_text(encoding="utf-8"))
    ontology_uri = urljoin(schema["$id"], "../wise_hillink_ontology_map.json")
    registry = Registry().with_resource(
        ontology_uri,
        Resource(contents=ontology, specification=DRAFT202012),
    )
    return Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def valid_randomized_record():
    return {
        "schema_version": "1.0.0",
        "record_id": "exp-record-001",
        "experiment": {
            "experiment_id": "price-test-2026-01",
            "design": "randomized",
            "assignment_level": "customer",
            "assignment_unit_id": "unit-001",
            "assigned_arm": "treatment",
            "assignment_probability": 0.5,
            "randomization_strata": ["WISE:StayDate"],
            "assigned_at": "2026-01-01T10:00:00+09:00",
            "protocol_id": "protocol-001",
        },
        "unit": {
            "unit_id": "unit-001",
            "eligible": True,
            "offer_time": "2026-01-01T10:01:00+09:00",
            "stay_date": "2026-02-01",
            "stay_end_date": "2026-02-03",
        },
        "offer": {
            "exposure_status": "shown",
            "exposed": True,
            "exposed_at": "2026-01-01T10:01:00+09:00",
            "product": {
                "source_system": "pms",
                "source_code": "SKU_THE_BUFFET_ADULT_2P",
                "display": "Breakfast for two",
                "canonical_id": "WISE:SKU:SKU_THE_BUFFET_ADULT_2P",
                "mapping_status": "mapped",
                "mapping_basis": "WISE:Ontology:SKU",
            },
            "room_type": {
                "source_system": "pms",
                "source_code": "DLX-K",
                "display": "Deluxe King",
                "canonical_id": None,
                "mapping_status": "unmapped",
                "mapping_basis": None,
            },
            "building": {
                "source_system": "pms",
                "source_code": "GR",
                "display": "Grand",
                "canonical_id": "WISE:Building:GRAND",
                "mapping_status": "mapped",
                "mapping_basis": "WISE:Crosswalk:Building:v1",
            },
            "promotion": {
                "source_system": "experiment",
                "source_code": "breakfast",
                "display": "Breakfast offer",
                "canonical_id": "WISE:Promotion:breakfast",
                "mapping_status": "mapped",
                "mapping_basis": "WISE:Ontology:Promotion",
            },
            "price": {
                "amount": 200000,
                "currency": "KRW",
                "unit": "per_night",
                "tax_included": True,
            },
            "discount": {
                "kind": "percentage",
                "value": 10,
                "unit": "percent",
            },
        },
        "conditions": {
            "lead_time_days": 31,
            "weekday": 7,
            "season": None,
            "events": [],
            "availability": {
                "status": "limited",
                "available": True,
                "remaining_inventory": 5,
                "inventory_unit": "room",
            },
        },
        "outcome": {
            "status": "complete",
            "window_start": "2026-01-01T10:01:00+09:00",
            "window_end": "2026-01-08T10:01:00+09:00",
            "booking_status": "booked",
            "booked_at": "2026-01-03T11:00:00+09:00",
            "booking_id": "booking-001",
            "cancel_status": "kept",
            "room_revenue": {
                "amount": 400000,
                "currency": "KRW",
                "unit": "per_stay",
                "tax_included": True,
            },
            "total_revenue": {
                "amount": 470000,
                "currency": "KRW",
                "unit": "per_stay",
                "tax_included": True,
            },
            "contribution_margin": {
                "amount": 350000,
                "currency": "KRW",
                "unit": "per_stay",
                "tax_included": True,
            },
        },
        "data_quality": {
            "completeness": "complete",
            "missing_fields": [],
            "currency_unit": "KRW",
            "quality_flags": [],
        },
        "provenance": {
            "data_origin": "randomized_experiment",
            "source_system": "approved_experiment_export",
            "source_table": "offer_outcome",
            "source_record_id": "source-row-001",
            "extracted_at": "2026-01-09T12:00:00+09:00",
            "transformation_version": "1.0.0",
            "source_sha256": "a" * 64,
            "consent_or_legal_basis_ref": "privacy-review-001",
            "identifier_protection": {
                "method": "keyed_hmac",
                "identity_mapping_access": "separate_restricted",
                "key_version": "v1",
                "transformation_ref": "pseudonymization-policy-001",
            },
        },
    }


class ExperimentSchemaTests(unittest.TestCase):
    def setUp(self):
        self.validator = build_validator()

    def test_randomized_record_with_unmapped_room_type_is_valid(self):
        self.assertEqual(list(self.validator.iter_errors(valid_randomized_record())), [])

    def test_unknown_room_type_cannot_claim_ontology_mapping(self):
        record = valid_randomized_record()
        record["offer"]["room_type"]["canonical_id"] = "WISE:RoomType:DELUXE_KING"
        record["offer"]["room_type"]["mapping_status"] = "mapped"
        self.assertTrue(list(self.validator.iter_errors(record)))

    def test_raw_personal_identifier_is_rejected(self):
        record = valid_randomized_record()
        record["customer_email"] = "guest@example.com"
        self.assertTrue(list(self.validator.iter_errors(record)))

    def test_randomized_design_requires_randomized_source_provenance(self):
        record = valid_randomized_record()
        record["provenance"]["data_origin"] = "observational_extract"
        self.assertTrue(list(self.validator.iter_errors(record)))

    def test_schema_is_valid_draft_2020_12(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


if __name__ == "__main__":
    unittest.main()
