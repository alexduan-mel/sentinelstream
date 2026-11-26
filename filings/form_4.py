"""Form 4 (statement of changes in beneficial ownership) as SEC's ownership XML gives it.

EDGAR lists a Form 4's primary document under an XSL rendering folder, for example
``xslF345X06/wk-form4_1795563957.xml``. The raw XML is the same file name at the top of the filing
folder. Only what the materiality rules and alerts need is read: the issuer, the reporting owners
and the non-derivative transactions. Values are kept as filed: no unit conversion, and a value given
only as a footnote reference is None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

OPEN_MARKET_PURCHASE = "P"


class Form4Error(ValueError):
    """The document is not a readable Form 4."""


@dataclass(frozen=True)
class Form4Transaction:
    line_no: int
    security_title: str | None
    transaction_date: date | None
    code: str
    acquired_disposed: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    shares_owned_after: Decimal | None
    direct_indirect: str | None


@dataclass(frozen=True)
class Form4:
    issuer_cik: str
    issuer_name: str | None
    reporting_owners: list[str]
    owner_roles: list[str]
    transactions: list[Form4Transaction]

    @property
    def open_market_purchases(self) -> list[Form4Transaction]:
        """Non-derivative purchases on the open market or in private deals (transaction code P)."""
        return [t for t in self.transactions if t.code == OPEN_MARKET_PURCHASE and t.acquired_disposed != "D"]


def raw_xml_name(primary_document: str | None) -> str | None:
    """The raw ownership XML's file name, from the primary document EDGAR lists."""
    if not primary_document:
        return None
    name = primary_document.strip().rsplit("/", 1)[-1]
    return name if name.lower().endswith(".xml") else None


def _text(parent: Element | None, path: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(path)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _decimal(parent: Element, path: str) -> Decimal | None:
    raw = _text(parent, path)
    if raw is None:
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise Form4Error(f"{path} is not a number: {raw!r}") from exc


def _date(parent: Element, path: str) -> date | None:
    raw = _text(parent, path)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise Form4Error(f"{path} is not a date: {raw!r}") from exc


def _flag(parent: Element | None, path: str) -> bool:
    return (_text(parent, path) or "").lower() in ("1", "true")


def _roles(owner: Element) -> list[str]:
    relationship = owner.find("reportingOwnerRelationship")
    roles = []
    if _flag(relationship, "isDirector"):
        roles.append("Director")
    if _flag(relationship, "isOfficer"):
        roles.append(_text(relationship, "officerTitle") or "Officer")
    if _flag(relationship, "isTenPercentOwner"):
        roles.append("10% owner")
    if _flag(relationship, "isOther"):
        roles.append(_text(relationship, "otherText") or "Other")
    return roles


def parse_form4(document: bytes) -> Form4:
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise Form4Error(f"not well-formed XML: {exc}") from exc
    except Exception as exc:  # defusedxml refuses entity tricks with its own exception types
        raise Form4Error(f"XML refused: {exc.__class__.__name__}") from exc
    if root.tag != "ownershipDocument":
        raise Form4Error(f"root element is {root.tag!r}, not ownershipDocument")
    document_type = _text(root, "documentType")
    if document_type not in ("4", "4/A"):
        raise Form4Error(f"documentType is {document_type!r}, not 4")

    issuer_cik = _text(root, "issuer/issuerCik")
    if not issuer_cik or not issuer_cik.isdigit() or len(issuer_cik) > 10:
        raise Form4Error(f"issuerCik is missing or invalid: {issuer_cik!r}")

    owners = root.findall("reportingOwner")
    names = [name for owner in owners if (name := _text(owner, "reportingOwnerId/rptOwnerName"))]
    roles: list[str] = []
    for owner in owners:
        roles.extend(role for role in _roles(owner) if role not in roles)

    transactions = []
    for line_no, row in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction"), start=1):
        code = _text(row, "transactionCoding/transactionCode")
        if not code:
            raise Form4Error(f"transaction {line_no} has no transaction code")
        transactions.append(
            Form4Transaction(
                line_no=line_no,
                security_title=_text(row, "securityTitle/value"),
                transaction_date=_date(row, "transactionDate/value"),
                code=code.upper(),
                acquired_disposed=_text(row, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                shares=_decimal(row, "transactionAmounts/transactionShares/value"),
                price_per_share=_decimal(row, "transactionAmounts/transactionPricePerShare/value"),
                shares_owned_after=_decimal(row, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                direct_indirect=_text(row, "ownershipNature/directOrIndirectOwnership/value"),
            )
        )
    return Form4(
        issuer_cik=issuer_cik.zfill(10),
        issuer_name=_text(root, "issuer/issuerName"),
        reporting_owners=names,
        owner_roles=roles,
        transactions=transactions,
    )
