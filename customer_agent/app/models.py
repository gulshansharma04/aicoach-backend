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


# ----------------------------- Plans / autonomy -----------------------------

class PlanStepIn(BaseModel):
    day_offset: int = 0
    channel: str = "text"
    goal: str = ""
    risk: str = "low"  # low|sensitive


class PlanCreate(BaseModel):
    name: str
    description: str = ""
    steps: List[PlanStepIn] = Field(default_factory=list)


class EnrollRequest(BaseModel):
    customer_id: int
    plan_id: int


class ActionDecision(BaseModel):
    decision: str  # approve|reject
    edited_draft: Optional[str] = None


class SignupRequest(BaseModel):
    name: str = ""
    email: Optional[str] = None
    phone: Optional[str] = None


class VerifyRequest(BaseModel):
    distributor_id: int
    code: str


class ConsentUpdate(BaseModel):
    consent: Optional[Dict[str, object]] = None
    tracked_platforms: Optional[List[str]] = None
    name: Optional[str] = None
    onboarded: Optional[bool] = None


class ConnectHerbalifeRequest(BaseModel):
    username: str
    password: str
    portal_url: Optional[str] = None


class ContentIdeaRequest(BaseModel):
    topic: str = "the latest from the Herbalife CEO"
    source_post: str = ""
    platforms: List[str] = Field(default_factory=lambda: ["instagram", "facebook"])
    n: int = 3


class WebsiteRequest(BaseModel):
    product: str
    goal: str = ""
    brand_voice: str = ""
    build: bool = False  # if true, also hand off to Google Stitch
