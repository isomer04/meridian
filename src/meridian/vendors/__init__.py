"""Mock external vendors. Every side effect in the system originates here.

Five vendors plus the LLM — which is a vendor too, and is treated like one: it has a
latency, a cost, a failure mode, and a record/replay cassette.
"""

from . import amc, aus, credit_bureau, fixtures, pricing, voe

__all__ = ["amc", "aus", "credit_bureau", "fixtures", "pricing", "voe"]
