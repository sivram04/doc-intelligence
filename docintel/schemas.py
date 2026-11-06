"""Pydantic v2 schemas: one per document type (F5).

Every extracted field carries its value, a confidence score and the source
bounding box so the review UI can highlight it.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class DocType(str, Enum):
    W2 = "w2"
    PAYSTUB = "paystub"
    BANK_STATEMENT = "bank_statement"
    INVOICE = "invoice"
    OTHER = "other"


class BBox(BaseModel):
    """PDF points, top-left origin."""
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 0


class ExtractedField(BaseModel, Generic[T]):
    value: T | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    bbox: BBox | None = None
    source_text: str | None = None  # OCR text of the span the value came from (evidence for review/calibration)


Money = ExtractedField[float]
Text = ExtractedField[str]
Rows = ExtractedField[list]  # table fields: a list of row dicts, columns in TABLE_COLUMNS

TABLE_COLUMNS: dict[str, dict[str, type]] = {
    "line_items": {"description": str, "quantity": float, "unit_price": float, "amount": float},
    "transactions": {"date": str, "description": str, "amount": float},
    # One row per tax printed on an invoice: GST splits (CGST/SGST/IGST), VAT rates, sales tax
    "tax_lines": {"tax_type": str, "rate": float, "taxable_amount": float, "amount": float},
}


class W2(BaseModel):
    employer_name: Text = Text()
    employer_ein: Text = Text()
    employee_name: Text = Text()
    employee_ssn_last4: Text = Text()
    tax_year: ExtractedField[int] = ExtractedField[int]()
    wages: Money = Money()
    federal_tax_withheld: Money = Money()
    social_security_wages: Money = Money()
    social_security_tax: Money = Money()
    medicare_wages: Money = Money()
    medicare_tax: Money = Money()
    state: Text = Text()
    state_wages: Money = Money()
    state_tax_withheld: Money = Money()


class Paystub(BaseModel):
    employer_name: Text = Text()
    employee_name: Text = Text()
    pay_date: Text = Text()
    pay_frequency: Text = Text()
    gross_pay: Money = Money()
    total_deductions: Money = Money()
    net_pay: Money = Money()
    ytd_gross: Money = Money()


class BankStatement(BaseModel):
    bank_name: Text = Text()
    account_holder: Text = Text()
    account_last4: Text = Text()
    statement_period: Text = Text()
    opening_balance: Money = Money()
    total_deposits: Money = Money()
    total_withdrawals: Money = Money()
    closing_balance: Money = Money()
    transactions: Rows = Rows()


class Invoice(BaseModel):
    """Any invoice: US, GST (India), VAT (EU/UK). Fields a layout lacks stay null."""
    vendor_name: Text = Text()
    invoice_number: Text = Text()
    invoice_date: Text = Text()
    due_date: Text = Text()
    bill_to: Text = Text()
    subtotal: Money = Money()
    discount: Money = Money()
    tax: Money = Money()
    shipping: Money = Money()
    total: Money = Money()
    line_items: Rows = Rows()
    # Added for general invoices (after the v1/v2 benchmarks, whose gold labels omit them)
    currency: Text = Text()            # ISO code as printed or implied by the symbol: USD, INR, EUR, GBP
    vendor_tax_id: Text = Text()       # GSTIN, VAT number, EIN, ABN
    buyer_tax_id: Text = Text()
    purchase_order: Text = Text()
    tax_lines: Rows = Rows()


SCHEMAS: dict[DocType, type[BaseModel]] = {
    DocType.W2: W2,
    DocType.PAYSTUB: Paystub,
    DocType.BANK_STATEMENT: BankStatement,
    DocType.INVOICE: Invoice,
}

NUMERIC_TYPES = (float, int)


def field_types(doc_type: DocType) -> dict[str, type]:
    """Field name -> python value type, read off the generic annotation."""
    out: dict[str, type] = {}
    for name, info in SCHEMAS[doc_type].model_fields.items():
        out[name] = info.annotation.model_fields["value"].annotation.__args__[0]
    return out


class ExtractionResult(BaseModel):
    doc_id: str
    doc_type: DocType
    type_confidence: float
    model: str
    fields: dict[str, ExtractedField[Any]]
    latency_ms: float = 0.0
    tokens: dict[str, int] = {}
    flags: list[str] = []
