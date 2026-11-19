import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import server
import ingredient_store


ROOT = Path(__file__).resolve().parents[1]


class V5QuickRepairTests(unittest.TestCase):
    def test_bad_library_limit_uses_default_instead_of_500(self):
        self.assertEqual(server.parse_library_limit("abc"), 80)
        self.assertEqual(server.parse_library_limit("-3"), 1)
        self.assertEqual(server.parse_library_limit("999"), 180)

    def test_password_comparison_supports_unicode(self):
        self.assertTrue(server.credentials_match("naïve", "pässwörd", "naïve", "pässwörd"))
        self.assertFalse(server.credentials_match("naïve", "wrong", "naïve", "pässwörd"))

    def test_posts_require_json_content_type(self):
        self.assertEqual(server.post_request_error({"Host": "app.example"})[0], 415)
        self.assertEqual(server.post_request_error({"Host": "app.example", "Content-Type": "text/plain"})[0], 415)
        self.assertIsNone(server.post_request_error({"Host": "app.example", "Content-Type": "application/json; charset=utf-8"}))

    def test_foreign_origin_is_rejected(self):
        headers = {"Host": "app.example", "Origin": "https://evil.example", "Content-Type": "application/json"}
        self.assertEqual(server.post_request_error(headers)[0], 403)

    def test_same_origin_and_host_only_requests_are_allowed(self):
        headers = {"Host": "app.example", "Origin": "https://app.example", "Content-Type": "application/json"}
        self.assertIsNone(server.post_request_error(headers))
        self.assertIsNone(server.post_request_error({"Host": "app.example", "Content-Type": "application/json"}))

    def test_cross_site_fetch_without_origin_is_rejected(self):
        headers = {"Host": "app.example", "Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"}
        self.assertEqual(server.post_request_error(headers)[0], 403)

    def test_e09_batch_study_types_reflect_primary_source(self):
        con = sqlite3.connect(f"file:{ROOT / 'smartnano.db'}?mode=ro", uri=True)
        try:
            rows = {row[0]: row[1:] for row in con.execute(
                "SELECT Formulation_ID, Evidence_Level, Evidence_Location FROM formulations WHERE Paper_ID='E09'")}
        finally:
            con.close()
        self.assertEqual(len(rows), 8)
        for ident in ('E09-F01','E09-F02','E09-F03','E09-F04','E09-F05','E09-F06'):
            self.assertEqual(rows[ident][0], 'Physicochemical only')
        for ident in ('E09-F07','E09-F08'):
            self.assertEqual(rows[ident][0], 'Human clinical')
            self.assertIn('irritation/tolerability only', rows[ident][1])
            self.assertIn('no clinical efficacy', rows[ident][1])


class IngredientMigrationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(f"file:{ROOT / 'smartnano.db'}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.close()

    def test_all_64_profiles_and_every_legacy_property_value_are_preserved(self):
        drugs = self.con.execute("SELECT * FROM drugs ORDER BY name").fetchall()
        self.assertEqual(len(drugs), 64)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient WHERE category='drug'").fetchone()[0], 64)
        for drug in drugs:
            canonical = self.con.execute("SELECT ingredient_id FROM ingredient WHERE preferred_name=?", (drug['name'],)).fetchone()
            self.assertIsNotNone(canonical, drug['name'])
            for field, _unit, _kind, _method in ingredient_store.PROPERTY_MAP:
                observation = self.con.execute(
                    "SELECT property_value_json,status FROM property_observation WHERE ingredient_id=? AND property_code=?",
                    (canonical[0], field),
                ).fetchone()
                self.assertIsNotNone(observation, (drug['name'], field))
                self.assertEqual(json.loads(observation['property_value_json']), drug[field], (drug['name'], field))

    def test_source_text_is_preserved_and_unknown_retrieval_dates_are_not_fabricated(self):
        row = self.con.execute("""SELECT d.source,s.source_label,s.retrieved_at AS source_retrieved_at,o.retrieved_at AS property_retrieved_at
                                  FROM drugs d JOIN ingredient i ON i.preferred_name=d.name
                                  JOIN source_record s ON s.source_record_id=(SELECT source_record_id FROM property_observation WHERE ingredient_id=i.ingredient_id LIMIT 1)
                                  JOIN property_observation o ON o.ingredient_id=i.ingredient_id
                                  WHERE d.name='Acetaminophen' LIMIT 1""").fetchone()
        self.assertEqual(row['source'], row['source_label'])
        self.assertIsNone(row['source_retrieved_at'])
        self.assertIsNone(row['property_retrieved_at'])

    def test_every_batch_has_a_link_or_explicit_unresolved_state(self):
        batch_count = self.con.execute("SELECT COUNT(*) FROM formulations").fetchone()[0]
        link_count = self.con.execute("SELECT COUNT(*) FROM formulation_ingredient_link").fetchone()[0]
        self.assertEqual(batch_count, 1087)
        self.assertEqual(link_count, batch_count)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM formulation_ingredient_link WHERE ingredient_id IS NOT NULL OR link_status='unresolved'").fetchone()[0], batch_count)

    def test_explicit_salt_is_a_separate_form_with_no_inherited_molecular_weight(self):
        link = self.con.execute("""SELECT l.ingredient_id,l.form_id,l.link_status,i.preferred_name,f.form_name
                                   FROM formulation_ingredient_link l
                                   JOIN ingredient i ON i.ingredient_id=l.ingredient_id
                                   JOIN ingredient_form f ON f.form_id=l.form_id
                                   WHERE l.raw_ingredient_name='Diclofenac sodium' LIMIT 1""").fetchone()
        self.assertIsNotNone(link)
        self.assertEqual(link['preferred_name'], 'Diclofenac')
        self.assertEqual(link['form_name'], 'Diclofenac sodium')
        self.assertEqual(link['link_status'], 'matched_form')
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM property_observation WHERE form_id=?", (link['form_id'],)).fetchone()[0], 0)

    def test_prodrug_and_mixture_names_are_not_collapsed_to_parent_ingredient(self):
        for raw in ('Methotrexate (DSPE-MTX prodrug)', 'Curcumin and cyclosporine'):
            row = self.con.execute("SELECT ingredient_id,form_id,link_status FROM formulation_ingredient_link WHERE raw_ingredient_name=? LIMIT 1", (raw,)).fetchone()
            self.assertIsNotNone(row, raw)
            self.assertIsNone(row['ingredient_id'], raw)
            self.assertIsNone(row['form_id'], raw)
            self.assertEqual(row['link_status'], 'unresolved', raw)

    def test_known_abbreviation_does_not_replace_the_canonical_ingredient_name(self):
        row = self.con.execute("""SELECT i.preferred_name,l.match_method
                                   FROM formulation_ingredient_link l JOIN ingredient i USING(ingredient_id)
                                   WHERE l.raw_ingredient_name='Acetaminophen (APAP)' LIMIT 1""").fetchone()
        self.assertEqual(row['preferred_name'], 'Acetaminophen')
        self.assertEqual(row['match_method'], 'canonical_name_plus_short_abbreviation')
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM ingredient_alias WHERE alias='APAP' AND review_status='pending_review'").fetchone())

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'copy.db'
            shutil.copy2(ROOT / 'smartnano.db', db)
            con = sqlite3.connect(db); con.row_factory = sqlite3.Row
            before = tuple(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ('ingredient','ingredient_form','ingredient_alias','property_observation','source_record','formulation_ingredient_link'))
            ingredient_store.initialize(con)
            after = tuple(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ('ingredient','ingredient_form','ingredient_alias','property_observation','source_record','formulation_ingredient_link'))
            con.close()
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
