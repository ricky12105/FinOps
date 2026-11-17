import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from prepare_billing import attribution, money, read_mapping


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.config = {"validFrom": "2026-06-01", "validToExclusive": "2026-10-01"}
        self.mapping = {"/resource/demo": {"businessUnit": "Finance", "initiative": "Modernization", "project": "Analytics", "tool": "Fabric"}}
        self.row = {"ResourceId": "/RESOURCE/DEMO", "Tags": None, "ChargePeriodStart": datetime(2026, 9, 1)}

    def test_case_insensitive_resource_mapping(self):
        self.assertEqual(attribution(self.row, self.config, self.mapping), ["Finance", "Modernization", "Analytics", "Fabric", "Demo mapping"])

    def test_complete_source_tags_take_precedence(self):
        self.row["Tags"] = '{"BusinessUnit":"Real BU","Initiative":"Real initiative","Project":"Real project","Tool":"Real tool"}'
        self.assertEqual(attribution(self.row, self.config, self.mapping)[::4], ["Real BU", "Source tags"])

    def test_unknown_resource_is_unassigned(self):
        self.row["ResourceId"] = "/unknown"
        self.assertEqual(attribution(self.row, self.config, self.mapping), ["Unassigned"] * 5)

    def test_mapping_expires(self):
        self.row["ChargePeriodStart"] = datetime(2026, 10, 1)
        self.assertEqual(attribution(self.row, self.config, self.mapping)[-1], "Unassigned")

    def test_signed_cost_and_rounding(self):
        self.assertEqual(money(Decimal("-12.345678")), Decimal("-12.3457"))
        self.assertEqual(money(Decimal("0.00005")), Decimal("0.0000"))

    def test_duplicate_mapping_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            path.write_text('{"mappings":[{"resourceId":"/x","businessUnit":"a","initiative":"b","project":"c","tool":"d"},{"resourceId":"/X","businessUnit":"a","initiative":"b","project":"c","tool":"d"}]}')
            with self.assertRaises(ValueError):
                read_mapping(path)


if __name__ == "__main__":
    unittest.main()
