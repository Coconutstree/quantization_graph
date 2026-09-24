"""Native disk-port registry facade.

Formal adapters are executable ports declared in ``ports.local.json``.  This
module intentionally exposes no Python search implementation: 05 must retain
the official 01/02/03 graph, codec, distance kernel and search semantics.
"""

from diskfair.native_contract import LAYER_METHODS, METHOD_SPECS, MethodSpec

__all__ = ["LAYER_METHODS", "METHOD_SPECS", "MethodSpec"]
