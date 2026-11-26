"""Builds Form 4 ownership XML shaped like EDGAR's (checked against a live AMZN Form 4, Nov 2026)."""

from __future__ import annotations


def transaction(code: str, shares: str, price: str | None, *, ad: str = "A", when: str = "2026-11-21") -> str:
    price_xml = f"<value>{price}</value>" if price is not None else '<footnoteId id="F1"/>'
    return f"""
        <nonDerivativeTransaction>
            <securityTitle><value>Common Stock, par value $.01  per share</value></securityTitle>
            <transactionDate><value>{when}</value></transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>{code}</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares><value>{shares}</value></transactionShares>
                <transactionPricePerShare>{price_xml}</transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>2285766</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
        </nonDerivativeTransaction>"""


def owner(name: str, *, director: bool = False, officer: str | None = None, ten_percent: bool = False) -> str:
    return f"""
    <reportingOwner>
        <reportingOwnerId><rptOwnerCik>0001374545</rptOwnerCik><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>{int(director)}</isDirector>
            <isOfficer>{int(officer is not None)}</isOfficer>
            <isTenPercentOwner>{"true" if ten_percent else "false"}</isTenPercentOwner>
            <isOther>0</isOther>
            {f"<officerTitle>{officer}</officerTitle>" if officer else ""}
        </reportingOwnerRelationship>
    </reportingOwner>"""


def form4(
    issuer_cik: str = "0001018724",
    *transactions: str,
    owners: tuple[str, ...] | None = None,
    document_type: str = "4",
) -> bytes:
    owners_xml = "".join(owners) if owners else owner("Jassy Andrew R", director=True, officer="President and CEO")
    return f"""<?xml version="1.0"?>
<ownershipDocument>
    <schemaVersion>X0609</schemaVersion>
    <documentType>{document_type}</documentType>
    <periodOfReport>2026-11-21</periodOfReport>
    <issuer>
        <issuerCik>{issuer_cik}</issuerCik>
        <issuerName>AMAZON COM INC</issuerName>
        <issuerTradingSymbol>AMZN</issuerTradingSymbol>
    </issuer>
    {owners_xml}
    <nonDerivativeTable>{"".join(transactions)}
    </nonDerivativeTable>
</ownershipDocument>
""".encode()
