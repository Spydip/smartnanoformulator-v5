"""Public provenance-aware ingredient and literature source adapters."""
from .base import CACHE_SCHEMA, RequestClient
from .pubchem import PubChemAdapter
from .rxnorm import RxNormAdapter
from .dailymed import DailyMedAdapter
from .openfda import OpenFDAAdapter
from .dsld import DSLDAdapter
from .pubmed import PubMedAdapter
from .europepmc import EuropePMCAdapter


def initialize(con):
    con.executescript(CACHE_SCHEMA)
    con.commit()


def build_adapters(con=None, *, transport=None, timeout=15.0):
    client = RequestClient(cache_con=con, transport=transport, timeout=timeout)
    return {
        "pubchem": PubChemAdapter(client),
        "rxnorm": RxNormAdapter(client),
        "dailymed": DailyMedAdapter(client),
        "openfda": OpenFDAAdapter(client),
        "dsld": DSLDAdapter(client),
        "pubmed": PubMedAdapter(client),
        "europepmc": EuropePMCAdapter(client),
    }

__all__ = ["RequestClient", "PubChemAdapter", "RxNormAdapter", "DailyMedAdapter",
           "OpenFDAAdapter", "DSLDAdapter", "PubMedAdapter", "EuropePMCAdapter",
           "build_adapters", "initialize"]
