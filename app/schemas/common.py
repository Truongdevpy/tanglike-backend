from typing import Generic, TypeVar, Optional, Any, Dict
from pydantic import BaseModel, Field

T = TypeVar("T")

class ErrorDetail(BaseModel):
    code: str
    message: str

class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    message: str = "Thành công"
    meta: Optional[Dict[str, Any]] = None

class ApiErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
