"""Tests for the integrated literature-calibrated Bayesian design / measured lab loop."""
import sqlite3
import unittest

import design_engine as D


class DesignLoopTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.payload = {
            "drug": "Curcumin", "mw": 368.38, "logp": 3.0, "pka": 8.5,
            "ionization": "Weak acid", "solubility_mg_ml": 0.0006,
            "labile": True, "phyto": True, "route": "Oral",
            "site": "Systemic (absorption)", "organ": "GI tract",
            "release": "Sustained", "vesicle": "Auto", "batch_count": 3,
            "batch_scale": 100,
        }

    def tearDown(self):
        self.con.close()

    def test_literature_calibrated_anchor_values_and_uncertainty(self):
        self.assertEqual(D.K.SIZE_BASE["Liposome"], 128)
        self.assertEqual(D.K.SIZE_BASE["Niosome"], 216)
        self.assertAlmostEqual(D.K.PDI_BASE["Ethosome"], 0.29)
        self.assertGreater(D.K.PRIOR_SD["size_nm"], 0.30)
        self.assertGreater(D.K.PRIOR_SD["pdi"], 0.08)

    def test_no_lab_rows_produces_prior_only_recommendation(self):
        result = D.make_recommendation(self.con, self.payload)
        self.assertEqual(result["model"]["mode"], "prior_only")
        self.assertEqual(len(result["proposals"]), 3)
        first = result["proposals"][0]
        self.assertLess(first["size_nm_low95"], first["size_nm"])
        self.assertGreater(first["size_nm_high95"], first["size_nm"])
        self.assertEqual(result["drug"]["pka"], 8.5)

    def test_measured_results_are_saved_and_update_gp_loop(self):
        result = D.make_recommendation(self.con, self.payload)
        measurements = []
        for i, proposed in enumerate(result["proposals"]):
            measurements.append({
                "modifier_pct": proposed["modifier_pct"],
                "lipid_conc_pct": proposed["lipid_conc_pct"],
                "drug_to_lipid": proposed["drug_to_lipid"],
                "size_nm": 180 + i * 5,
                "pdi": 0.22 + i * 0.01,
                "zeta_mv": -20 - i,
                "ee_pct": 70 + i,
            })
        saved = D.save_lab_results(self.con, {
            **self.payload, "vesicle": result["vesicle"], "measured": measurements, "round": 1
        })
        self.assertEqual(saved["saved"], 3)
        self.assertEqual(saved["summary"]["rows"], 3)
        source = self.con.execute("SELECT DISTINCT source FROM design_lab_runs").fetchone()[0]
        self.assertEqual(source, "user_lab")
        organ = self.con.execute("SELECT DISTINCT target_organ FROM design_lab_runs").fetchone()[0]
        self.assertEqual(organ, "GI tract")
        updated = D.make_recommendation(self.con, self.payload)
        self.assertEqual(updated["model"]["mode"], "prior_plus_gp_lab_learning")
        self.assertEqual(updated["model"]["same_drug_carrier_rows"], 3)

    def test_unmeasured_or_invalid_values_are_not_saved(self):
        with self.assertRaisesRegex(ValueError, "at least one measured output"):
            D.save_lab_results(self.con, {
                **self.payload, "vesicle": "Liposome",
                "measured": [{"modifier_pct": 40, "drug_to_lipid": 0.1}],
            })
        self.assertEqual(D.lab_summary(self.con)["rows"], 0)

    def test_invalid_pdi_is_rejected_transactionally(self):
        with self.assertRaises(ValueError):
            D.save_lab_results(self.con, {
                **self.payload, "vesicle": "Liposome",
                "measured": [{"modifier_pct": 40, "drug_to_lipid": 0.1, "size_nm": 150, "pdi": 1.4}],
            })
        self.assertEqual(D.lab_summary(self.con)["rows"], 0)


if __name__ == "__main__":
    unittest.main()
