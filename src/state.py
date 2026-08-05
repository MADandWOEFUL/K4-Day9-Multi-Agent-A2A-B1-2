from typing import TypedDict, List, Dict, Any, Optional, Annotated
import operator

class AgentState(TypedDict):
    # Input ban đầu
    case_id: str
    claimed_order_id: str
    customer_request_msg: str
    investigation_scope: Dict[str, bool]
    
    # Kế hoạch của Coordinator (LLM tự quyết định dispatch ai)
    dispatched_agents: List[str]
    
    # Finding trả về từ các domain agent
    customer_finding: Optional[Dict[str, Any]]
    order_finding: Optional[Dict[str, Any]]
    payment_finding: Optional[Dict[str, Any]]
    delivery_finding: Optional[Dict[str, Any]]
    
    # Tổng hợp bằng chứng & Nhận định mâu thuẫn (Coordinator LLM làm)
    conflict_report: Optional[str]
    evidence_bundle: Optional[Dict[str, Any]]
    
    # Kết quả từ Policy Agent
    policy_decision: Optional[Dict[str, Any]]
    
    # Output cuối cùng và trạng thái verify
    draft_output: Optional[Dict[str, Any]]
    verifier_errors: List[str]
    
    # Guardrail kỹ thuật
    retry_count: int
    system_confidence: float
    trace_log: Annotated[List[Dict[str, Any]], operator.add]