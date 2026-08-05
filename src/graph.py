from langgraph.graph import StateGraph, START, END
from typing import List

from .state import AgentState
from .nodes import (
    coordinator_agent,
    order_agent,
    customer_agent,
    delivery_agent,
    payment_agent,
    coordinator_review_agent,
    policy_agent,
    verifier_agent,
    invalid_order_agent
)

def route_dispatch(state: AgentState) -> List[str]:
    """Trả về danh sách các tác vụ (agent) cần chạy song song."""
    agents = state.get("dispatched_agents", [])
    if not agents:
        return ["invalid_order"]
        
    # Map tên string sang tên node thực tế
    mapping = {
        "order_agent": "order",
        "customer_agent": "customer_product",
        "delivery_agent": "delivery",
        "payment_agent": "payment"
    }
    mapped_agents = [mapping[a] for a in agents if a in mapping]
    return mapped_agents

def route_verifier(state: AgentState) -> str:
    """Rẽ nhánh: nếu có lỗi verifier và retry < 3 thì thử lại."""
    errors = state.get("verifier_errors", [])
    retry_count = state.get("retry_count", 0)
    
    if errors and retry_count < 3:
        return "policy" # Quay lại policy để thử lại
    return END

def build_graph():
    workflow = StateGraph(AgentState)
    
    # Định nghĩa các nodes
    workflow.add_node("coordinator", coordinator_agent)
    workflow.add_node("order", order_agent)
    workflow.add_node("customer_product", customer_agent)
    workflow.add_node("delivery", delivery_agent)
    workflow.add_node("payment", payment_agent)
    workflow.add_node("coordinator_review", coordinator_review_agent)
    workflow.add_node("policy", policy_agent)
    workflow.add_node("verifier", verifier_agent)
    workflow.add_node("invalid_order", invalid_order_agent)
    
    # Luồng bắt đầu
    workflow.add_edge(START, "coordinator")
    
    # Coordinator rẽ nhánh song song tới các domain agents dựa trên quyết định của LLM
    workflow.add_conditional_edges(
        "coordinator",
        route_dispatch,
        ["order", "customer_product", "delivery", "payment", "invalid_order"]
    )
    
    workflow.add_edge("invalid_order", END)
    
    # Các domain agents đi về điểm tổng hợp
    workflow.add_edge("order", "coordinator_review")
    workflow.add_edge("customer_product", "coordinator_review")
    workflow.add_edge("delivery", "coordinator_review")
    workflow.add_edge("payment", "coordinator_review")
    
    # Từ review sang policy
    workflow.add_edge("coordinator_review", "policy")
    
    # Từ policy đi tới verifier
    workflow.add_edge("policy", "verifier")
    
    # Verifier rẽ nhánh: Lặp (Retry) hoặc Kết thúc
    workflow.add_conditional_edges(
        "verifier",
        route_verifier,
        {"policy": "policy", END: END}
    )
    
    return workflow.compile()
