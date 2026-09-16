"""CAN-X bounded historical query domain.

Public surface: typed query models, the query failure contract, segment
planning and the query service. Internal modules own DuckDB; no SQL, no
``DuckDBPyConnection`` and no ``DuckDBPyRelation`` ever reaches a caller.
"""
