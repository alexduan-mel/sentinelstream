"""Links to filings on EDGAR, shared by the API and the alert notifiers."""

from __future__ import annotations

SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def sec_index_url(cik: str, accession_no: str) -> str:
    """EDGAR's index page for a filing, which links the primary document and every exhibit."""
    return f"{SEC_ARCHIVES}/{int(cik)}/{accession_no.replace('-', '')}/{accession_no}-index.htm"
