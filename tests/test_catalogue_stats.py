import sqlite3
import unittest

import ingredient_store


class CatalogueStatsTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE ingredient(ingredient_id INTEGER PRIMARY KEY, category TEXT, review_status TEXT);
            CREATE TABLE product(product_id INTEGER PRIMARY KEY);
            CREATE TABLE product_ingredient(product_id INTEGER, ingredient_id INTEGER);
            CREATE TABLE formulations(Formulation_ID TEXT);
            CREATE TABLE ingredient_review_queue(queue_id INTEGER PRIMARY KEY);
            CREATE TABLE ingredient_duplicate_flag(duplicate_flag_id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE source_record(source_record_id INTEGER PRIMARY KEY, source_system TEXT, licence_note TEXT);
        """)
        self.con.executemany("INSERT INTO ingredient VALUES(?,?,?)", [
            (1, "drug", "verified"),
            (2, "drug", "auto_retrieved_pending_review"),
            (3, "botanical_phytochemical", "partially_verified"),
            (4, "mineral", "rejected"),
        ])
        self.con.execute("INSERT INTO product VALUES(1)")
        self.con.execute("INSERT INTO product_ingredient VALUES(1,1)")
        self.con.executemany("INSERT INTO formulations VALUES(?)", [("F1",), ("F1",), ("F2",)])
        self.con.executemany("INSERT INTO ingredient_review_queue VALUES(?)", [(1,), (2,)])
        self.con.execute("INSERT INTO ingredient_duplicate_flag VALUES(1,'pending_review')")
        self.con.executemany("INSERT INTO source_record VALUES(?,?,?)", [
            (1, "PubChem", None), (2, "legacy formulation catalogue", ""), (3, "Unrelated source", None),
        ])

    def tearDown(self):
        self.con.close()

    def test_stats_report_only_human_verified_entities(self):
        stats = ingredient_store.catalogue_stats(self.con)
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["verified"], 1)
        self.assertEqual(stats["verified_share"], 0.25)
        self.assertEqual(stats["categories"]["drug"]["verified_share"], 0.5)
        self.assertEqual(stats["categories"]["botanical_phytochemical"]["verified"], 0)
        self.assertEqual(stats["products"], 1)
        self.assertEqual(stats["product_ingredient_links"], 1)
        self.assertEqual(stats["review_queue_items"], 2)
        self.assertEqual(stats["pending_duplicate_flags"], 1)
        self.assertEqual(stats["formulation_batches"], 2)
        self.assertEqual(stats["ingredient_catalogue_import_adds_formulation_batches"], 0)

    def test_migrated_source_licence_notes_are_cautious_and_idempotent(self):
        changed = ingredient_store.backfill_source_licence_notes(self.con)
        self.assertEqual(changed, 2)
        rows = dict(self.con.execute("SELECT source_system,licence_note FROM source_record"))
        self.assertIn("No blanket licence is assumed.", rows["PubChem"])
        self.assertIn("does not grant downstream reuse rights", rows["legacy formulation catalogue"])
        self.assertIsNone(rows["Unrelated source"])
        self.assertEqual(ingredient_store.backfill_source_licence_notes(self.con), 0)


if __name__ == "__main__":
    unittest.main()
