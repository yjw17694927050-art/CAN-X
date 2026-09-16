"""CAN-X data persistence domain.

Public surface: typed session/segment models, the Parquet segment format, and
the data-session service. Internal modules own SQLite and PyArrow; neither
leaks into the public API.
"""
