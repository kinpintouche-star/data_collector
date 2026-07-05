from __future__ import annotations

from ict.data.providers.csv_provider import CSVProvider


class DukascopyCSVProvider(CSVProvider):
    """Dukascopy adapter for already-downloaded CSV files.

    The platform stays generic: this provider only extracts rows from a file.
    Dukascopy-specific column names/timezones are handled by transformers.
    """

    source_name = "dukascopy"
