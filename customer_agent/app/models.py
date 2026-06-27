"""Pydantic request/response schemas."""

from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


# ----------------------------- Customers -----------------------------

class CustomerBase(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    stage: str = "prospect"
    tags: List[str] = Field(default_factory=list)
    socials: Dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    preferred_channel: str = "text"


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    stage: Optional[str] = None
    tags: Optional[List[str]] = None
    socials: Optional[Dict[str, str]] = None
    notes: Optional[str] = None
    preferred_channel: Optional[str] = None


# ----------------------------- Progress -----------------------------

class ProgressCreate(BaseModel):
    title: str
    status: str = "in_progress"
    note: str = ""


# ----------------------------- Orders -----------------------------

class OrderCreate(BaseModel):
    product: str
    amount: float = 0.0
    status: str = "paid"
    ordered_at: Optional[str] = None  # ISO date; defaults to now
    notes: str = ""


# ----------------------------- Activities -----------------------------

class ActivityCreate(BaseModel):
    platform: str = "instagram"
    kind: str = "comment"
    content: str = ""
    sentiment: Optional[str] = None  # auto-detected if omitted
    url: str = ""
    needs_response: bool = False
    occurred_at: Optional[str] = None


# ----------------------------- Interactions -----------------------------

class InteractionCreate(BaseModel):
    channel: str = "call"
    summary: str = ""
    occurred_at: Optional[str] = None


# ----------------------------- Reminders -----------------------------

class ReminderCreate(BaseModel):
    kind: str = "follow_up"
    reason: str = ""
    draft_message: str = ""
    priority: str = "medium"
    due_date: Optional[str] = None


class ReminderUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    reason: Optional[str] = None
    draft_message: Optional[str] = None
    due_date: Optional[str] = None


# ----------------------------- Agent -----------------------------

class ChatRequest(BaseModel):
    message: str
    customer_id: Optional[int] = None


class DraftRequest(BaseModel):
    channel: Optional[str] = None  # call|text; defaults to customer preference
    goal: str = "check in and offer support"
