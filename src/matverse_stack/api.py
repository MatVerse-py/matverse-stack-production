from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .constraint_gate import CausalConstraintRule, MutationContext
from .memory import MNB
from .ledger import LedgerEntry
from .service import MatVerseService
from .utils import require_api_key

router = APIRouter()
service = MatVerseService()


class ProcessRequest(BaseModel):
    input: str
    top_k: int = 3
    add_to_memory: bool = False
    metadata: Dict[str, Any] = {}


class MemoryAddRequest(BaseModel):
    content: str
    source: str = "api"
    metadata: Dict[str, Any] = {}


class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 5


class MutationEvaluateRequest(BaseModel):
    mutation: MutationContext
    constraint: CausalConstraintRule
    initial_state: Dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
def health() -> Dict[str, Any]:
    return service.get_health()


@router.post("/process")
def process_api(request: ProcessRequest) -> Dict[str, Any]:
    return service.process_query(
        request.input,
        top_k=request.top_k,
        add_to_memory=request.add_to_memory,
        metadata=request.metadata,
    )


@router.post("/mutation/evaluate")
def mutation_evaluate_api(request: MutationEvaluateRequest, x_api_key: str = Header(None)) -> Dict[str, Any]:
    require_api_key(x_api_key)
    return service.evaluate_mutation(
        request.mutation,
        request.constraint,
        initial_state=request.initial_state or None,
    )


@router.post("/memory/add")
def memory_add_api(request: MemoryAddRequest, x_api_key: str = Header(None)) -> MNB:
    require_api_key(x_api_key)
    return service.add_mnb(request.content, request.source, request.metadata)


@router.post("/memory/search")
def memory_search_api(request: MemorySearchRequest) -> List[MNB]:
    return service.search_memory(request.query, request.top_k)


@router.get("/mnb/{mnb_id}")
def get_mnb_api(mnb_id: str) -> Optional[MNB]:
    mnb = service.get_mnb(mnb_id)
    if not mnb:
        raise HTTPException(status_code=404, detail="MNB not found")
    return mnb


@router.get("/ledger")
def get_ledger_api() -> List[LedgerEntry]:
    return service.get_ledger()


@router.get("/memory/context")
def get_context_api() -> List[MNB]:
    return service.memory.get_context_window()


@router.post("/autogenesis/close")
def close_autogenesis_api(metadata: Dict[str, Any] = {}, x_api_key: str = Header(None)) -> Dict[str, Any]:
    require_api_key(x_api_key)
    return service.close_autogenesis(metadata)


@router.get("/ledger/receipt/{index}")
def get_receipt_api(index: int) -> Dict[str, Any]:
    try:
        return service.ledger.receipt(index)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
