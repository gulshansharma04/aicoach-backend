"""
The AI layer.

Every function here has two paths:

1. **AI path** — when ``OPENAI_API_KEY`` is set, we ask an LLM to turn the
   deterministic signals (from scoring.py) into warm, human, distributor-ready
   language: personalized tips, ready-to-send messages, and a daily catch-up
   briefing.
2. **Fallback path** — when no key is configured (or the call fails), we return
   high-quality templated text built from the same signals. The product is
   fully functional offline; AI simply makes it sparkle.

This keeps the agent reliable: prioritization is deterministic, wording is AI.
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional

CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

_client = None
_client_ready = False


def _get_client():
    """Lazily build the OpenAI client; return None if unavailable."""
    global _client, _client_ready
    if _client_ready:
        return _client
    _client_ready = True
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        _client = None
        return None
    try:
        from openai import OpenAI
        _client = OpenAI(api_key=api_key)
    except Exception:
        _client = None
    return _client


def ai_available() -> bool:
    return _get_client() is not None


def _chat_json(system: str, user: str, max_tokens: int = 500) -> Optional[Dict[str, Any]]:
    """Call the LLM expecting a JSON object back. Returns None on any failure."""
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        return json.loads(text)
    except Exception:
        return None


def _chat_text(system: str, user: str, max_tokens: int = 400) -> Optional[str]:
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.5,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception:
        return None


# ============================================================
# Sentiment (for social activities)
# ============================================================

_NEG_WORDS = {
    "bad", "worst", "hate", "disappointed", "refund", "broken", "late", "angry",
    "scam", "terrible", "awful", "cancel", "complaint", "problem", "issue",
    "not working", "never", "poor", "slow", "rude", "wrong", "damaged", "unhappy",
}
_POS_WORDS = {
    "love", "great", "amazing", "thank", "awesome", "best", "happy", "excellent",
    "perfect", "recommend", "wonderful", "fantastic", "good", "nice", "obsessed",
    "incredible", "beautiful", "works", "fast",
}


_TRAVEL_WORDS = {
    "in town", "back in town", "visiting", "traveling", "travelling", "on vacation",
    "vacation", "trip", "landed in", "arrived in", "flying to", "heading to",
    "just moved", "now in", "weekend in", "exploring", "road trip", "back home",
}


def detect_travel(text: str) -> bool:
    """True if a social post hints the customer is traveling / in a new town."""
    t = (text or "").lower()
    return any(w in t for w in _TRAVEL_WORDS)


def detect_sentiment(text: str) -> str:
    """Lightweight, dependency-free sentiment for social comments."""
    t = (text or "").lower()
    if not t.strip():
        return "neutral"
    neg = sum(1 for w in _NEG_WORDS if w in t)
    pos = sum(1 for w in _POS_WORDS if w in t)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


# ============================================================
# Personalized tips
# ============================================================

def generate_tips(customer: Dict[str, Any], health: Dict[str, Any],
                  recent_activities: List[Dict[str, Any]]) -> List[str]:
    name = customer.get("name", "this customer")
    sig = health.get("signals", {})

    fallback = _fallback_tips(customer, health)

    system = (
        "You are an expert sales & relationship coach for an independent "
        "distributor. Give short, specific, actionable tips to nurture ONE "
        "customer relationship. Be warm and practical. Output JSON only."
    )
    user = (
        f"Customer: {name} (stage: {customer.get('stage')}, "
        f"preferred channel: {customer.get('preferred_channel')}).\n"
        f"Health: {health.get('score')}/100 ({health.get('status')}).\n"
        f"Reasons: {health.get('reasons')}\n"
        f"Signals: days since contact={sig.get('days_since_contact')}, "
        f"days since order={sig.get('days_since_order')}, "
        f"orders={sig.get('order_count')}, revenue=${sig.get('total_revenue')}, "
        f"negative social={sig.get('recent_negative_count')}, "
        f"unanswered comments={sig.get('unanswered_comments')}.\n"
        f"Recent social: "
        f"{[{'platform': a.get('platform'), 'kind': a.get('kind'), 'content': a.get('content'), 'sentiment': a.get('sentiment')} for a in recent_activities[:5]]}\n"
        'Return JSON exactly: {"tips": ["tip 1", "tip 2", "tip 3"]}. '
        "3 to 4 tips, each one sentence, no numbering."
    )
    data = _chat_json(system, user, max_tokens=400)
    if data and isinstance(data.get("tips"), list):
        tips = [str(t).strip() for t in data["tips"] if str(t).strip()]
        if tips:
            return tips[:4]
    return fallback


def _fallback_tips(customer: Dict[str, Any], health: Dict[str, Any]) -> List[str]:
    sig = health.get("signals", {})
    name = customer.get("name", "your customer")
    tips: List[str] = []

    if sig.get("unanswered_comments"):
        tips.append(f"Reply to {name}'s open social comment today — fast responses build trust.")
    if sig.get("recent_negative_count"):
        tips.append(f"Call {name} personally to address recent frustration before it grows.")
    dsc = sig.get("days_since_contact")
    if dsc is None:
        tips.append(f"Send {name} a friendly intro message and ask what they're hoping to achieve.")
    elif dsc > 14:
        tips.append(f"It's been {dsc} days — a quick check-in keeps {name} from going cold.")
    dso = sig.get("days_since_order")
    if dso is not None and dso > 60:
        tips.append(f"Suggest a reorder or a complementary product to {name}.")
    if customer.get("stage") == "vip":
        tips.append(f"Reward {name} with a small exclusive perk — VIPs drive referrals.")
    if not tips:
        tips.append(f"Share a quick value tip or success story with {name} to stay top of mind.")
        tips.append(f"Ask {name} for feedback — it deepens the relationship and surfaces needs.")
    return tips[:4]


# ============================================================
# Draft outreach message (text / call script)
# ============================================================

def draft_message(customer: Dict[str, Any], health: Dict[str, Any],
                  channel: str, goal: str) -> str:
    name = customer.get("name", "there")
    first = name.split()[0] if name else "there"
    fallback = _fallback_message(customer, health, channel, goal)

    system = (
        "You write warm, concise, non-pushy outreach for an independent "
        "distributor reaching out to a customer. Match the channel: a 'text' "
        "is 1-3 short sentences; a 'call' is a brief talking-points script. "
        "Never sound like spam. Output JSON only."
    )
    user = (
        f"Channel: {channel}. Goal: {goal}.\n"
        f"Customer first name: {first}. Stage: {customer.get('stage')}.\n"
        f"Context — health {health.get('score')}/100, {health.get('support_reason') or 'routine check-in'}. "
        f"Signals: {health.get('signals')}\n"
        'Return JSON exactly: {"message": "..."}'
    )
    data = _chat_json(system, user, max_tokens=300)
    if data and str(data.get("message", "")).strip():
        return str(data["message"]).strip()
    return fallback


def _fallback_message(customer: Dict[str, Any], health: Dict[str, Any],
                      channel: str, goal: str) -> str:
    name = customer.get("name", "there")
    first = name.split()[0] if name else "there"
    sig = health.get("signals", {})
    reason = health.get("support_reason") or ""

    if channel == "call":
        points = [f"Open warmly: ask {first} how things are going.",
                  "Listen first — let them share before you pitch."]
        if sig.get("recent_negative_count"):
            points.append("Acknowledge their recent frustration and offer to make it right.")
        elif sig.get("days_since_order") and sig["days_since_order"] > 60:
            points.append("Mention it might be time for a reorder and ask if they're running low.")
        else:
            points.append("Share one quick, relevant tip or update they'd value.")
        points.append("Close by confirming a small next step.")
        return "Call script for " + first + ":\n- " + "\n- ".join(points)

    # text / default
    if sig.get("recent_negative_count"):
        return (f"Hi {first}, I saw your recent note and I want to make sure "
                f"everything's right. Do you have a few minutes to chat? I'm here to help. 🙏")
    if sig.get("days_since_order") and sig["days_since_order"] > 60:
        return (f"Hey {first}! Just checking in — are you running low? "
                f"Happy to set up a reorder or recommend what's working well for others. 😊")
    if sig.get("days_since_contact") is None:
        return (f"Hi {first}! So glad to connect. I'd love to learn what you're "
                f"hoping to achieve so I can point you to the right options. 😊")
    return (f"Hi {first}! Thinking of you — how are things going? "
            f"Anything I can help with this week? 💬")


# ============================================================
# Daily catch-up briefing
# ============================================================

def daily_briefing(focus: List[Dict[str, Any]], totals: Dict[str, Any]) -> str:
    """A short narrated summary of the day's priorities."""
    fallback = _fallback_briefing(focus, totals)
    system = (
        "You are the distributor's proactive AI assistant. Write a short, "
        "energizing daily briefing (3-5 sentences) summarizing who needs "
        "attention today and why. Be specific and motivating, not generic."
    )
    compact = [
        {
            "name": f.get("name"),
            "status": f.get("health", {}).get("status"),
            "reason": f.get("health", {}).get("support_reason"),
            "channel": f.get("health", {}).get("suggested_channel"),
        }
        for f in focus[:6]
    ]
    user = (
        f"Totals: {totals}.\nTop focus customers: {compact}.\n"
        "Write the briefing as plain text (no markdown headers)."
    )
    text = _chat_text(system, user, max_tokens=300)
    return text or fallback


def _fallback_briefing(focus: List[Dict[str, Any]], totals: Dict[str, Any]) -> str:
    n = totals.get("needs_attention", 0)
    if n == 0:
        return ("All clear today — no customers are flagged for urgent attention. "
                "Great time to nurture relationships: share a tip or celebrate a win "
                "with a healthy customer.")
    names = ", ".join(f.get("name", "?") for f in focus[:3])
    urgent = totals.get("urgent", 0)
    parts = [f"You have {n} customer(s) who need attention today"]
    if urgent:
        parts.append(f", including {urgent} urgent")
    parts.append(f". Start with {names}.")
    parts.append(" I've prepared draft messages and reminders for each — "
                 "knock out the urgent calls first, then the check-ins.")
    return "".join(parts)


# ============================================================
# Social reply drafting
# ============================================================

def draft_reply(customer: Dict[str, Any], activity: Dict[str, Any]) -> str:
    """Draft a public reply/comment to a customer's social post."""
    name = (customer.get("name") or "there").split()[0]
    content = activity.get("content", "")
    platform = activity.get("platform", "social")
    sentiment = activity.get("sentiment", "neutral")
    fallback = _fallback_reply(name, content, sentiment)

    system = (
        "You write short, warm, on-brand public replies to a customer's social "
        "media post for an independent distributor. 1-2 sentences, friendly, "
        "human, never salesy or robotic. If the post is negative, be empathetic "
        "and move the conversation to DM/call. Output JSON only."
    )
    user = (
        f"Platform: {platform}. Customer first name: {name}. Sentiment: {sentiment}.\n"
        f"Their post: \"{content}\"\n"
        'Return JSON exactly: {"reply": "..."}'
    )
    data = _chat_json(system, user, max_tokens=160)
    if data and str(data.get("reply", "")).strip():
        return str(data["reply"]).strip()
    return fallback


def _fallback_reply(first: str, content: str, sentiment: str) -> str:
    if sentiment == "negative":
        return (f"So sorry to hear that, {first} — I want to make this right. "
                f"I'll send you a quick DM so we can sort it out. 🙏")
    if sentiment == "positive":
        return f"Love this, {first}! 🙌 Thank you so much for sharing — you're amazing!"
    return f"Thanks for sharing, {first}! 😊 Let me know if you ever need anything."


# ============================================================
# Morning briefing narration (voice-ready)
# ============================================================

def narrate_briefing(data: Dict[str, Any]) -> str:
    """Turn the structured briefing into a spoken-style paragraph for TTS."""
    fallback = _fallback_narration(data)
    system = (
        "You are Jarvis — a warm, concise, upbeat personal AI assistant for an "
        "independent distributor. Read them a short spoken morning briefing "
        "(4-7 sentences) from the data. Sound natural and human, like you're "
        "talking, not reading a report. No markdown, no bullet points, no emojis."
    )
    user = f"Briefing data:\n{data}\n\nSpeak the briefing now."
    text = _chat_text(system, user, max_tokens=320)
    return text or fallback


def _fallback_narration(d: Dict[str, Any]) -> str:
    s = d.get("stats", {})
    parts = ["Good morning! Here's your rundown."]
    if s.get("new_customers"):
        parts.append(f"You've got {s['new_customers']} new customer(s) since we last spoke.")
    if s.get("ordered_customers"):
        parts.append(
            f"{s['ordered_customers']} customer(s) visited your site and placed orders, "
            f"totaling {s.get('order_revenue', 0):.0f} dollars.")
    if s.get("in_town"):
        names = ", ".join(c["name"] for c in d.get("in_town", [])[:3])
        parts.append(f"{s['in_town']} customer(s) look like they're traveling or in town right now, including {names}.")
    if s.get("posts_reviewed"):
        parts.append(
            f"I reviewed {s['posts_reviewed']} social post(s) and replied to "
            f"{s.get('posts_replied', 0)} of them automatically.")
    if s.get("pending_approvals"):
        parts.append(f"{s['pending_approvals']} message(s) are waiting for your approval before I send them.")
    ns = d.get("next_steps", [])
    if ns:
        first = ns[0]
        parts.append(f"Your top priority is {first['name']}: {first['action']}.")
        if len(ns) > 1:
            parts.append(f"After that, check in on {', '.join(n['name'] for n in ns[1:3])}.")
    if len(parts) == 1:
        parts.append("Everything's quiet — a great day to nurture your healthy customers.")
    return " ".join(parts)


# ============================================================
# Content inspiration (e.g. repost based on Herbalife CEO news)
# ============================================================

def content_ideas(topic: str, source_post: str = "", platforms: Optional[List[str]] = None,
                  n: int = 3) -> List[Dict[str, str]]:
    """
    Turn a trending topic / source post (e.g. the latest Herbalife CEO post)
    into ready-to-publish repost drafts for the distributor's own socials.
    """
    platforms = platforms or ["instagram", "facebook"]
    fallback = _fallback_content(topic, platforms, n)

    system = (
        "You are a social-media ghostwriter for an independent Herbalife "
        "distributor. Turn the source/topic into original, authentic, "
        "compliant repost ideas in the distributor's own voice — inspirational "
        "and community-focused, NOT making income or health claims. Each idea "
        "has a platform, a caption, and 3-5 hashtags. Output JSON only."
    )
    user = (
        f"Topic: {topic}\n"
        f"Source post (for inspiration, do NOT copy verbatim): \"{source_post}\"\n"
        f"Platforms: {platforms}. Produce {n} ideas.\n"
        'Return JSON exactly: {"ideas": [{"platform": "...", "caption": "...", "hashtags": "#a #b"}]}'
    )
    data = _chat_json(system, user, max_tokens=600)
    if data and isinstance(data.get("ideas"), list) and data["ideas"]:
        out = []
        for it in data["ideas"][:n]:
            out.append({
                "platform": str(it.get("platform", platforms[0])),
                "caption": str(it.get("caption", "")).strip(),
                "hashtags": str(it.get("hashtags", "")).strip(),
            })
        if any(o["caption"] for o in out):
            return out
    return fallback


def _fallback_content(topic: str, platforms: List[str], n: int) -> List[Dict[str, str]]:
    templates = [
        ("Loved seeing the latest from our leadership on {t}. "
         "It's a great reminder of why I do what I do — helping people feel their best. "
         "DM me if you want to start your own journey! 💪",
         "#herbalifelife #wellnessjourney #community #motivation"),
        ("Big inspiration today around {t}. Proud to be part of a community focused on "
         "healthy, active lifestyles. What's one healthy habit you're building this week? 👇",
         "#healthyliving #nutrition #goals #mindset"),
        ("{t} got me thinking about consistency. Small steps every day add up. "
         "Here to support you — let's do this together! 🌱",
         "#consistency #wellness #support #lifestyle"),
    ]
    out = []
    for i in range(min(n, len(templates))):
        cap, tags = templates[i]
        out.append({
            "platform": platforms[i % len(platforms)],
            "caption": cap.format(t=topic or "our mission"),
            "hashtags": tags,
        })
    return out


# ============================================================
# Conversational agent
# ============================================================

def agent_chat(message: str, context: str) -> str:
    fallback = ("I can help you manage customers, spot who needs attention, draft "
                "messages, and surface tips. (Connect an OpenAI API key to enable "
                "full conversational answers.) Here's what I know right now:\n\n" + context)
    system = (
        "You are a helpful AI assistant embedded in a customer-management app for "
        "an independent distributor. Answer using ONLY the provided context about "
        "their customers. Be concise, practical, and action-oriented. If asked to "
        "draft outreach, write it ready-to-send."
    )
    user = f"Context about the distributor's customers:\n{context}\n\nQuestion: {message}"
    text = _chat_text(system, user, max_tokens=500)
    return text or fallback
