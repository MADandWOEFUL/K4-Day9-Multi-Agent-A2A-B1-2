from pydantic import BaseModel, Field
from typing import List, Optional

class CaseAssessment(BaseModel):
    primary_issue: str
    secondary_issues: List[str] = Field(default_factory=list)
    case_status: str
    confidence: float = Field(ge=0.0, le=1.0)

class AffectedEntities(BaseModel):
    order_ids: List[str] = Field(default_factory=list, max_length=5)
    item_ids: List[str] = Field(default_factory=list, max_length=5)
    seller_ids: List[str] = Field(default_factory=list, max_length=3)
    payment_ids: List[str] = Field(default_factory=list, max_length=5)

class CustomerContext(BaseModel):
    customer_unique_id: str
    related_order_ids: List[str] = Field(default_factory=list, max_length=5)

class ProductContext(BaseModel):
    product_ids: List[str] = Field(default_factory=list, max_length=5)
    category_names: List[str] = Field(default_factory=list, max_length=5)

class SellerHandoffAnalysis(BaseModel):
    seller_id: str
    shipping_limit_at: Optional[str] = None
    handoff_variance_hours: Optional[float] = None
    late_handoff: Optional[bool] = None

class DeliveryAnalysis(BaseModel):
    delivered_at: Optional[str] = None
    estimated_delivery_at: Optional[str] = None
    carrier_handoff_at: Optional[str] = None
    delivery_variance_hours: Optional[float] = None
    seller_handoff_analysis: List[SellerHandoffAnalysis] = Field(default_factory=list)
    late_handoff_seller_ids: List[str] = Field(default_factory=list)

class PaymentReconciliation(BaseModel):
    currency: str = "BRL"
    item_total_brl: Optional[float] = None
    freight_total_brl: Optional[float] = None
    expected_total_brl: Optional[float] = None
    payment_total_brl: Optional[float] = None
    difference_brl: Optional[float] = None
    reconciled: Optional[bool] = None
    payment_types: List[str] = Field(default_factory=list)

class RankedCause(BaseModel):
    cause_code: str
    rank: int

class ResponsibleParty(BaseModel):
    party_type: str
    party_id: str

class RootCauseAnalysis(BaseModel):
    ranked_causes: List[RankedCause] = Field(default_factory=list, max_length=3)
    responsible_parties: List[ResponsibleParty] = Field(default_factory=list, max_length=3)

class FinancialResolution(BaseModel):
    currency: str = "BRL"
    recommended_refund_brl: float

class FinalOutput(BaseModel):
    case_id: str
    case_assessment: CaseAssessment
    affected_entities: AffectedEntities
    customer_context: CustomerContext
    product_context: ProductContext
    delivery_analysis: DeliveryAnalysis
    payment_reconciliation: PaymentReconciliation
    root_cause_analysis: RootCauseAnalysis
    evidence_ids: List[str] = Field(default_factory=list, max_length=20)
    financial_resolution: FinancialResolution
    resolution_actions: List[str] = Field(default_factory=list, max_length=5)