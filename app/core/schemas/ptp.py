"""
presentation/schemas/ptp.py
─────────────────────────────
Pydantic models for the Promises to Pay endpoints.
"""
from pydantic import BaseModel, Field
from datetime import date

class CreatePTPRequest(BaseModel):
    promised_date: date = Field(..., description="The date the client promised to pay.")
    promised_amount: float = Field(..., description="The amount agreed upon.")
    notes: str = Field(default="", description="Optional context from the officer's call.")

class PTPResponse(BaseModel):
    id: str
    client_id: str
    promised_date: date
    promised_amount: float
    status: str