from pathlib import Path
import json
import sqlite3
import unittest
from urllib.parse import urlsplit, parse_qs

from sources.base import RequestClient
from sources.pubchem import PubChemAdapter
from sources.rxnorm import RxNormAdapter
from sources.dailymed import DailyMedAdapter
from sources.openfda import OpenFDAAdapter
from sources.dsld import DSLDAdapter
from sources.pubmed import PubMedAdapter
from sources.europepmc import EuropePMCAdapter

FIXTURES = Path(__file__).parent / 'fixtures'


class FixtureTransport:
    def __init__(self):
        self.calls = []

    def _fixture(self, name):
        return (FIXTURES / name).read_bytes()

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if '/pug_view/data/' in path:
            body, ct = self._fixture('pubchem_view.json'), 'application/json'
        elif path.endswith('/synonyms/JSON'):
            body, ct = self._fixture('pubchem_synonyms.json'), 'application/json'
        elif '/property/' in path:
            body, ct = self._fixture('pubchem_properties.json'), 'application/json'
        elif '/pug/compound/name/' in path or '/pug/compound/cid/' in path:
            body, ct = self._fixture('pubchem_name.json'), 'application/json'
        elif path.endswith('/approximateTerm.json'):
            body, ct = self._fixture('rxnorm_approx.json'), 'application/json'
        elif path.endswith('/allProperties.json'):
            body, ct = self._fixture('rxnorm_properties.json'), 'application/json'
        elif path.endswith('/properties.json'):
            body, ct = self._fixture('rxnorm_concept.json'), 'application/json'
        elif path.endswith('/rxcui.json'):
            body, ct = self._fixture('rxnorm_name.json'), 'application/json'
        elif path.endswith('/uniis.json'):
            body, ct = self._fixture('dailymed_uniis.json'), 'application/json'
        elif path.endswith('/spls.json'):
            body, ct = self._fixture('dailymed_spls.json'), 'application/json'
        elif path.endswith('/.xml') or path.endswith('555555555555.xml'):
            body, ct = self._fixture('dailymed_spl.xml'), 'application/xml'
        elif path.endswith('/drug/label.json'):
            body = self._fixture('openfda_unii.json' if 'openfda.unii' in query.get('search', [''])[0] else 'openfda_label.json')
            ct = 'application/json'
        elif path.endswith('/search-filter'):
            body, ct = self._fixture('dsld_search.json'), 'application/json'
        elif path.endswith('/ingredient-groups/'):
            body, ct = self._fixture('dsld_groups.json'), 'application/json'
        elif path.endswith('/label/82118'):
            body, ct = self._fixture('dsld_label.json'), 'application/json'
        elif path.endswith('/esearch.fcgi'):
            body, ct = self._fixture('pubmed_esearch.json'), 'application/json'
        elif path.endswith('/esummary.fcgi'):
            body, ct = self._fixture('pubmed_esummary.json'), 'application/json'
        elif path.endswith('/efetch.fcgi'):
            body, ct = self._fixture('pubmed_article.xml'), 'application/xml'
        elif path.endswith('/search'):
            body, ct = self._fixture('europepmc_search.json'), 'application/json'
        else:
            return 404, {'Content-Type':'application/json'}, b'{"error":"not found"}'
        return 200, {'Content-Type':ct}, body


class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:', check_same_thread=False)
        self.transport = FixtureTransport()
        self.client = RequestClient(self.con, self.transport, timeout=1, sleep=lambda _seconds: None,
                                    wall_time=lambda: 1000.0, monotonic=lambda: 0.0)
        self.pubchem = PubChemAdapter(self.client)
        self.rxnorm = RxNormAdapter(self.client)
        self.dailymed = DailyMedAdapter(self.client)
        self.openfda = OpenFDAAdapter(self.client, api_key='top-secret-test-key')
        self.dsld = DSLDAdapter(self.client, api_key='dsld-test-key')
        self.pubmed = PubMedAdapter(self.client, tool='test_tool', email='test@example.org', api_key='ncbi-test-key')
        self.europepmc = EuropePMCAdapter(self.client)

    def tearDown(self):
        self.con.close()

    def test_pubchem_returns_candidates_and_preserves_computed_property_source(self):
        found = self.pubchem.search_name('acetaminophen')
        self.assertEqual(found.status, 'ambiguous')
        self.assertEqual({x['cid'] for x in found.records}, {2244, 12345})
        prop = self.pubchem.get_properties(2244)
        self.assertEqual(prop.status, 'found')
        self.assertEqual(prop.records[0]['MolecularWeight'], 180.16)
        self.assertTrue(prop.records[0]['InChI'].startswith('InChI='))
        self.assertEqual(prop.records[0]['InChIKey'], 'BSYNRYMUTXBXSQ-UHFFFAOYSA-N')
        self.assertTrue(prop.records[0]['SMILES'])
        self.assertIn('source-specific', prop.licence_note)
        annotations = self.pubchem.get_annotations(2244)
        self.assertEqual(annotations.records[0]['Record']['RecordNumber'], 2244)

    def test_pubchem_synonyms_are_source_listed_and_keyed_to_the_cid(self):
        synonyms=self.pubchem.get_synonyms(1983)
        self.assertEqual(synonyms.status,'found')
        self.assertEqual(synonyms.records[0]['CID'],1983)
        self.assertEqual(synonyms.records[0]['Synonym'],['acetaminophen','Paracetamol','APAP','Tylenol','Panadol'])
        self.assertIn('PubChem',synonyms.attribution)

    def test_rxnorm_concept_search_properties_and_scored_candidates(self):
        result = self.rxnorm.search_name('atorvastatin')
        self.assertEqual(result.records[0]['rxcui'], '83367')
        properties = self.rxnorm.get_properties('83367')
        self.assertTrue(any(p['propName'] == 'TTY' for p in properties.records))
        concept = self.rxnorm.get_concept_properties('83367')
        self.assertEqual(concept.records[0]['tty'], 'IN')
        approximate = self.rxnorm.approximate_name('zocor 10 mg')
        self.assertEqual(approximate.status, 'ambiguous')
        self.assertEqual(approximate.records[0]['score'], '100')

    def test_dailymed_searches_identifiers_and_caches_full_spl_xml(self):
        index = self.dailymed.search_ingredients('simvastatin')
        self.assertEqual(index.records[0]['unii_code'], 'RZVAJINKPMORJF-UHFFFAOYSA-N')
        labels = self.dailymed.search_labels('Example')
        self.assertEqual(labels.records[0]['setid'], '11111111-2222-3333-4444-555555555555')
        xml = self.dailymed.get_label_xml('11111111-2222-3333-4444-555555555555')
        self.assertIn('<document>', xml.records[0]['spl_xml'])
        again = self.dailymed.get_label_xml('11111111-2222-3333-4444-555555555555')
        self.assertTrue(again.cache_hit)

    def test_openfda_search_retains_unii_and_label_set_ids(self):
        result = self.openfda.search_ingredient('acetaminophen')
        record = result.records[0]
        self.assertEqual(record['set_id'], '11111111-2222-3333-4444-555555555555')
        self.assertEqual(record['openfda']['unii'], ['669K06KY21'])
        self.assertIn('Food and Drug Administration', result.attribution)

    def test_dsld_label_and_ingredient_group_search_are_label_data(self):
        label = self.dsld.get_label(82118)
        self.assertEqual(label.records[0]['ingredients'][0]['name'], 'Vitamin D3')
        group = self.dsld.ingredient_group_search('Vitamin D')
        self.assertEqual(group.records[0]['name'], 'Vitamin D')
        self.assertIn('CC0', label.licence_note)

    def test_pubmed_search_summary_and_xml_use_recorded_responses(self):
        search = self.pubmed.search('nanovesicles[tiab]')
        self.assertEqual(search.records[0]['pmid'], '12345')
        summary = self.pubmed.summary(['12345'])
        self.assertEqual(summary.records[0]['title'], 'Example ingredient formulation study')
        detail = self.pubmed.fetch_xml(['12345'])
        self.assertEqual(detail.records[0]['pmid'], '12345')
        self.assertIn('source-attributed test abstract', detail.records[0]['abstract'])

    def test_europe_pmc_chemical_query_is_literature_not_identity_resolution(self):
        result = self.europepmc.search_chemical('dihydrocapsaicin')
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.records[0]['doi'], '10.1000/example')
        self.assertEqual(self.europepmc.by_doi('10.1000/example').records[0]['pmid'], '12345')
        self.assertIn('Europe PMC', result.attribution)
        self.assertIn('rights vary by record', result.licence_note)

    def test_successful_responses_are_cached_and_credentials_are_redacted(self):
        first = self.openfda.search_ingredient('acetaminophen')
        count = len(self.transport.calls)
        second = self.openfda.search_ingredient('acetaminophen')
        self.assertEqual(len(self.transport.calls), count)
        self.assertTrue(second.cache_hit)
        urls = [r[0] for r in self.con.execute('SELECT request_url FROM api_response_cache')]
        self.assertTrue(urls)
        self.assertTrue(all('top-secret-test-key' not in u and 'REDACTED' in u for u in urls))
        self.assertNotIn('top-secret-test-key', json.dumps(first.as_dict()))

    def test_retries_are_bounded_to_two_and_permanent_errors_are_not_retried(self):
        calls, sleeps = [], []
        def transient(url, headers, timeout):
            calls.append(url)
            if len(calls) < 3:
                return 503, {'Content-Type':'application/json'}, b'{"busy":true}'
            return 200, {'Content-Type':'application/json'}, b'{"ok":true}'
        client = RequestClient(transport=transient, sleep=sleeps.append, monotonic=lambda:0)
        ok = client.get_json('retry-test','https://example.org/data',{},ttl_seconds=60,min_interval=0)
        self.assertTrue(ok.ok)
        self.assertEqual(ok.attempts,3)
        self.assertEqual(sleeps,[0.5,1.0])
        permanent_calls=[]
        permanent = RequestClient(transport=lambda *args:(permanent_calls.append(1) or (400,{},b'bad request')),
                                  sleep=lambda _:None,monotonic=lambda:0)
        failed = permanent.get_json('bad-request','https://example.org/data',{},ttl_seconds=60,min_interval=0)
        self.assertFalse(failed.ok)
        self.assertEqual(failed.attempts,1)
        self.assertEqual(len(permanent_calls),1)

    def test_ncbis_registered_client_identity_is_sent_without_exposing_api_key(self):
        self.pubmed.search('test')
        url, headers, timeout = next(call for call in self.transport.calls if '/esearch.fcgi' in call[0])
        self.assertIn('tool=test_tool', url)
        self.assertIn('email=test%40example.org', url)
        self.assertIn('ncbi-test-key', url)
        cached_url = self.con.execute("SELECT request_url FROM api_response_cache WHERE provider='pubmed' LIMIT 1").fetchone()[0]
        self.assertNotIn('ncbi-test-key', cached_url)
        self.assertIn('REDACTED', cached_url)
        self.assertGreater(timeout, 0)


if __name__ == '__main__':
    unittest.main()
