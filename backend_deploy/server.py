# server.py
import os
import math
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# OpenAI client (official SDK)
from openai import OpenAI

# ============================================================
# Config / Env
# ============================================================

# Load API key from config.py OR environment
try:
    from config import OPENAI_API_KEY  # recommended: DO NOT hardcode a real key in config.py for production
except Exception:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found. Set OPENAI_API_KEY env var (recommended).")

client = OpenAI(api_key=OPENAI_API_KEY)
CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"


# ============================================================
# LLM helpers
# ============================================================

def coach_rewrite(handedness: str, positives: List[str], improvements: List[str]) -> Dict[str, str]:
    """
    Use LLM to rewrite structured pose findings into friendly coaching language.
    Returns dict with keys: positive, improvement.
    """
    prompt = f"""
You are a friendly baseball hitting coach. Rewrite the feedback in plain, normal English.

Rules:
- Output MUST be valid JSON only. No extra text.
- Keep it SHORT: 1 positive sentence + 1 improvement sentence.
- Do NOT mention "keypoints", "scores", "thresholds", "pixels", or "model".
- Do NOT mention handedness unless it helps clarity.
- Use calm, encouraging tone like a coach.
- If improvements list is empty, say everything looks solid and what to do next.
- If positives list is empty, still give a quick encouragement.

Handedness: {handedness}

Positives (raw): {positives}
Improvements (raw): {improvements}

Return JSON exactly in this schema:
{{
  "positive": "string",
  "improvement": "string"
}}
""".strip()

    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": "You only output valid JSON. No markdown. No extra text."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "").strip()

        import json
        data = json.loads(text)

        return {
            "positive": str(data.get("positive", "")).strip() or "Nice work — you’re in a good setup.",
            "improvement": str(data.get("improvement", "")).strip() or "Hold that stance and say Start when you’re ready."
        }
    except Exception:
        # fallback
        return {
            "positive": positives[0] if positives else "Nice work — you’re in a good setup.",
            "improvement": improvements[0] if improvements else "Hold that stance and say Start when you’re ready."
        }


# ============================================================
# FastAPI app
# ============================================================

app = FastAPI(title="AI Coaches: Voice + Vision")

# Allow your Netlify site and a localhost dev origin. Adjust/add origins as needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://698ab9a8f7b4d12a5ea27653--gentle-blini-1031d6.netlify.app",
        "https://gentle-blini-1031d6.netlify.app",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files (frontend) ONLY if folder exists
if STATIC_DIR.exists() and STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
# Pydantic models
# ============================================================

class ChatRequest(BaseModel):
    question: str
    image_data_url: Optional[str] = None  # "data:image/jpeg;base64,..."


class ChatResponse(BaseModel):
    answer: str


class FoodRequest(BaseModel):
    image_data_url: str  # "data:image/jpeg;base64,..."


class KeypointItem(BaseModel):
    name: str
    x: float
    y: float
    score: Optional[float] = None


class PoseCompareRequest(BaseModel):
    handedness: str = "right"  # "right" or "left"
    live_keypoints: List[KeypointItem]
    ref_keypoints: List[KeypointItem] = []


# ============================================================
# Utility functions
# ============================================================

def calculate_angle(ax, ay, bx, by, cx, cy) -> float:
    """
    Compute the angle ABC (degrees) at point B between A-B and C-B vectors.
    """
    BAx, BAy = ax - bx, ay - by
    BCx, BCy = cx - bx, cy - by
    dot = BAx * BCx + BAy * BCy
    magBA = math.hypot(BAx, BAy)
    magBC = math.hypot(BCx, BCy)
    if magBA == 0 or magBC == 0:
        return 0.0
    cosv = dot / (magBA * magBC)
    cosv = max(min(cosv, 1.0), -1.0)
    return math.degrees(math.acos(cosv))


def to_kp_map(kps: List[KeypointItem]) -> Dict[str, Dict]:
    """Return dict name -> {x,y,score}"""
    return {kp.name: {"x": kp.x, "y": kp.y, "score": kp.score or 0.0} for kp in kps}


def serve_static_or_message(filename: str, title: str) -> HTMLResponse:
    """
    If static HTML exists, serve it. Otherwise return a helpful message.
    This prevents Docker from crashing when you deploy API-only.
    """
    fpath = STATIC_DIR / filename
    if fpath.exists():
        return FileResponse(str(fpath))
    return HTMLResponse(
        content=f"""
<!doctype html>
<html>
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family: Arial, sans-serif; padding: 24px;">
  <h2>{title}</h2>
  <p>This server is running in <b>API-only mode</b> (no static UI bundled).</p>
  <p>Use <code>/docs</code> to test the APIs.</p>
  <p>If you want this backend to serve the web UI too, copy your <code>static/</code> folder into the image and rebuild.</p>
</body>
</html>
""".strip(),
        status_code=200,
    )


# ============================================================
# Page routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
def landing():
    return serve_static_or_message("landing.html", "AI Coach")


@app.get("/batting", response_class=HTMLResponse)
def batting():
    return serve_static_or_message("batting.html", "Batting Coach")


@app.get("/food", response_class=HTMLResponse)
def food():
    return serve_static_or_message("food.html", "Food Analyzer")


@app.get("/coach", response_class=HTMLResponse)
def coach():
    return serve_static_or_message("coach.html", "Coach Chat")


@app.get("/health")
def health():
    return {"ok": True, "static_present": STATIC_DIR.exists()}


# ============================================================
# New STT upload endpoint
# ============================================================

@app.post("/stt_upload")
async def stt_upload(audio: UploadFile = File(...)):
    """
    Accept an uploaded audio file (field name 'audio'), convert to 16k mono WAV using ffmpeg,
    and return a JSON response {"text": "..."}.

    NOTE: This returns a placeholder transcription. Replace the placeholder below
    with your preferred STT implementation (OpenAI Whisper, OpenAI API, Google Speech, etc.).
    """
    if not audio or not audio.filename:
        raise HTTPException(status_code=400, detail="No audio file uploaded")

    tmpdir = tempfile.mkdtemp(prefix="aicoach_stt_")
    try:
        in_path = os.path.join(tmpdir, audio.filename)
        # Save uploaded file
        with open(in_path, "wb") as f:
            f.write(await audio.read())

        # Convert to 16k mono WAV for downstream STT engines
        wav_path = os.path.join(tmpdir, "out.wav")
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", in_path,
            "-ac", "1", "-ar", "16000",
            wav_path
        ]

        try:
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else ''
            raise HTTPException(status_code=500, detail=f"ffmpeg conversion failed: {stderr}")

        # === TRANSCRIPTION PLACEHOLDER ===
        # Replace this section with an actual transcription call.
        # Example options:
        #  - Call OpenAI Speech-to-Text (whisper-1) using client or openai package:
        #      with open(wav_path, "rb") as f:
        #          resp = client.audio.transcriptions.create(file=f, model="whisper-1")
        #          text = resp["text"]
        #  - Run local whisper model
        #  - Call Google Speech-to-Text
        #
        # For now, return a placeholder so you can verify the upload + conversion flow.
        text = "TRANSCRIPTION_PLACEHOLDER"

        return {"text": text}

    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass


# ============================================================
# APIs (existing)
# ============================================================

@app.post("/api/chat", response_model=ChatResponse)
def api_chat(req: ChatRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    instructions = (
        "You are a friendly, helpful general coach. "
        "If a question needs current info (weather, current events, etc.), use web search. "
        "Answer clearly and concisely."
    )

    try:
        # Build multimodal input if image is provided
        if req.image_data_url and req.image_data_url.startswith("data:image"):
            input_items = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question},
                        {"type": "input_image", "image_url": req.image_data_url},
                    ],
                }
            ]
        else:
            input_items = question

        response = client.responses.create(
            model=CHAT_MODEL,
            instructions=instructions,
            tools=[{"type": "web_search"}],
            input=input_items,
            max_output_tokens=350,
        )

        ans = (response.output_text or "").strip()
        return {"answer": ans or "Sorry — I couldn't generate a response."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat error: {e}")


@app.post("/api/food")
def api_food(req: FoodRequest):
    if not req.image_data_url or not req.image_data_url.startswith("data:image"):
        raise HTTPException(status_code=400, detail="image_data_url must be a data:image/... URL")

    system = (
        "You are a nutrition coach providing practical, general nutrition guidance. "
        "Help people make informed food choices for overall health, energy, performance, "
        "and body composition. Be honest about uncertainty."
    )

    user_prompt = """
Analyze the food in the image and provide general nutrition guidance.

Return JSON ONLY. No markdown, no extra text. Use this schema exactly:

{
  "items": ["string", "..."],
  "classification": "Healthy|Mixed|Junk",

  "total_calories_range": "###-### kcal",
  "calories_by_item": [
    { "item": "string", "calories_range": "##-## kcal" }
  ],
  "portion_assumption": "short (e.g., 1 medium avocado / 1 cup rice / cooked with ~1 tbsp oil)",

  "protein_estimate": "Low|Moderate|High",
  "protein_grams_range": "##-## g",
  "fiber_estimate": "Low|Moderate|High",
  "fiber_grams_range": "##-## g",
  "calorie_density": "Low|Medium|High",
  "added_sugar_risk": "None|Low|High",
  "fat_quality": "Mostly healthy|Mixed|Mostly refined",
  "blood_sugar_impact": "Low|Medium|High",
  "portion_risk": "Low|Medium|High",
  "satiety_score": 1,
  "timing_advice": "Anytime|Better earlier|Post-workout friendly|Occasional treat",
  "notes": "short explanation",
  "tips": ["tip 1", "tip 2", "tip 3"],
  "confidence": "Low|Medium|High"
}

Rules:
- satiety_score must be an integer 1-5.
- If you cannot identify the food, still fill fields with best-effort and set confidence Low.
- Keep notes short (1-2 sentences).
- tips should be practical and broadly applicable (portion awareness, balance, pairing foods, etc.).
""".strip()

    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": req.image_data_url}},
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=700,
        )

        text = (resp.choices[0].message.content or "").strip()

        import json
        data = json.loads(text)

        def pick(key, default):
            v = data.get(key, default)
            return default if v is None else v

        sat = pick("satiety_score", 3)
        try:
            sat = int(sat)
        except Exception:
            sat = 3
        sat = max(1, min(5, sat))

        normalized = {
            "items": pick("items", []),
            "classification": pick("classification", "Mixed"),

            "total_calories_range": pick("total_calories_range", pick("calories_range", "—")),
            "calories_by_item": pick("calories_by_item", []),
            "portion_assumption": pick("portion_assumption", ""),

            "protein_estimate": pick("protein_estimate", "Moderate"),
            "protein_grams_range": pick("protein_grams_range", "—"),
            "fiber_estimate": pick("fiber_estimate", "Moderate"),
            "fiber_grams_range": pick("fiber_grams_range", "—"),
            "calorie_density": pick("calorie_density", "Medium"),
            "added_sugar_risk": pick("added_sugar_risk", "Low"),
            "fat_quality": pick("fat_quality", "Mixed"),
            "blood_sugar_impact": pick("blood_sugar_impact", "Medium"),
            "portion_risk": pick("portion_risk", "Medium"),
            "satiety_score": sat,
            "timing_advice": pick("timing_advice", "Anytime"),
            "notes": pick("notes", ""),
            "tips": pick("tips", pick("weight_loss_tips", [])),  # backward compatible
            "confidence": pick("confidence", "Medium"),
        }

        return JSONResponse(content=normalized)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Food analysis error: {e}")


@app.post("/analyze_pose")
async def analyze_pose(body: PoseCompareRequest):
    """
    Upper-body batting setup analysis for RIGHT- or LEFT-handed hitter.
    """
    live = to_kp_map(body.live_keypoints)
    ref = to_kp_map(body.ref_keypoints) if body.ref_keypoints else {}

    # ---------------- helpers ----------------
    def has(m, *names):
        return all(n in m for n in names)

    def score_ok(m, name, min_score=0.35):
        return name in m and (m[name].get("score", 0.0) >= min_score)

    def all_scores_ok(m, names, min_score=0.35):
        missing = []
        for n in names:
            if not score_ok(m, n, min_score=min_score):
                missing.append(n)
        return missing

    def shoulder_width(m):
        if has(m, "left_shoulder", "right_shoulder"):
            return abs(m["right_shoulder"]["x"] - m["left_shoulder"]["x"]) or None
        return None

    def angle(m, a, b, c):
        return calculate_angle(
            m[a]["x"], m[a]["y"],
            m[b]["x"], m[b]["y"],
            m[c]["x"], m[c]["y"],
        )

    def norm_dist(dx, dy, sw):
        if not sw:
            return None
        return (math.hypot(dx, dy) / sw)

    # ---------------- Identify key side names (HANDNESS-AWARE) ----------------
    hand = (body.handedness or "right").lower().strip()
    if hand.startswith("l"):
        back_sh, back_el, back_wr = "left_shoulder", "left_elbow", "left_wrist"
        front_sh, front_el, front_wr = "right_shoulder", "right_elbow", "right_wrist"
    else:
        back_sh, back_el, back_wr = "right_shoulder", "right_elbow", "right_wrist"
        front_sh, front_el, front_wr = "left_shoulder", "left_elbow", "left_wrist"

    # ---------------- GATING 1: confidence ----------------
    required = [front_sh, back_sh, front_wr, back_wr]
    missing_conf = all_scores_ok(live, required, min_score=0.35)
    live_sw = shoulder_width(live)

    if not live_sw or missing_conf:
        return JSONResponse(content={
            "Positives": {"issue": "not enough reliable joints yet", "advice": "No worries — you’re getting set up."},
            "Improvements": {
                "issue": "upper-body joints not confidently detected",
                "advice": (
                    "I need to clearly see both shoulders and both wrists. "
                    "Step back, raise the camera slightly, and keep your arms in frame."
                )
            }
        })

    # Optional: elbows help, but don't hard-fail if missing
    elbow_missing = all_scores_ok(live, [front_el, back_el], min_score=0.30)

    # ---------------- GATING 2: too far / cropped ----------------
    if live_sw < 40:
        return JSONResponse(content={
            "Positives": {"issue": "camera is running", "advice": "Good — I can see you, but not enough of your upper body yet."},
            "Improvements": {"issue": "too much cropping / too far away", "advice": "Move closer OR adjust framing so your shoulders and wrists are clearly visible."}
        })

    # ---------------- GATING 3: stance check ----------------
    hands_x = (live[back_wr]["x"] + live[front_wr]["x"]) / 2.0
    hands_y = (live[back_wr]["y"] + live[front_wr]["y"]) / 2.0

    dx_h_bs = hands_x - live[back_sh]["x"]
    dy_h_bs = hands_y - live[back_sh]["y"]
    hand_to_back_sh = norm_dist(dx_h_bs, dy_h_bs, live_sw)

    if hand_to_back_sh is not None and hand_to_back_sh > 1.25:
        return JSONResponse(content={
            "Positives": {"issue": "you’re in frame", "advice": "Nice — I can see you clearly."},
            "Improvements": {
                "issue": "not in batting setup yet",
                "advice": (
                    "Get into your batting stance and set your hands near your back shoulder. "
                    "Stand sideways to the camera with the webcam in front of you pointed at your chest, "
                    "then say Start or Go."
                )
            }
        })

    # ---------------- Build feedback: positives + improvements ----------------
    positives: List[str] = []
    improvements: List[str] = []

    def add_pos(msg: str):
        positives.append(msg)

    def add_imp(part: str, issue: str, advice: str):
        improvements.append(f"{part}: {issue} — {advice}")

    if elbow_missing:
        add_imp("Visibility", "elbows not clearly visible", "Try better lighting and keep both elbows in frame for more accurate feedback.")

    # 1) Hands compact
    wrist_dx = live[back_wr]["x"] - live[front_wr]["x"]
    wrist_dy = live[back_wr]["y"] - live[front_wr]["y"]
    wrist_sep = norm_dist(wrist_dx, wrist_dy, live_sw)

    if wrist_sep is not None:
        if wrist_sep <= 0.45:
            add_pos("Grip: hands look reasonably compact/stacked (good for quickness).")
        else:
            add_imp("Hands (Grip)", "hands too far apart", "Bring your hands closer together (stacked grip). Compact hands = quicker to the ball.")

    # 2) Hands near back shoulder
    if hand_to_back_sh is not None:
        if hand_to_back_sh <= 0.90:
            add_pos("Hand set: hands are fairly close to the back shoulder (compact/quick).")
        else:
            add_imp("Hand Set (Compact)", "hands set too far from back shoulder", "Move your hands closer to your back shoulder. Compact hand set helps you get to the ball faster.")

    # forward drift from shoulder midpoint
    mid_sh_x = (live["left_shoulder"]["x"] + live["right_shoulder"]["x"]) / 2.0
    forward_drift = abs(hands_x - mid_sh_x) / live_sw
    if forward_drift <= 0.90:
        add_pos("Hands: not drifting too far away from your torso line (good).")
    else:
        add_imp("Hand Set (Compact)", "hands drifting away from torso", "Keep hands closer to your chest/back shoulder line for a quick, compact move.")

    # 3) Back elbow slot
    if score_ok(live, back_el, 0.30):
        back_el_y = live[back_el]["y"]
        back_sh_y = live[back_sh]["y"]
        el_height = (back_sh_y - back_el_y) / live_sw

        if -0.35 <= el_height <= 0.10:
            add_pos("Back elbow: looks in a good slot (not flying, not pinned).")
        else:
            if el_height > 0.10:
                add_imp("Back Elbow", "back elbow too high (flying)", "Lower the back elbow slightly into the slot. Too high can make you longer to the ball.")
            if el_height < -0.35:
                add_imp("Back Elbow", "back elbow too low", "Raise the back elbow a bit. Keep it relaxed and ready — not pinned down.")

    # 4) Elbow bend
    if score_ok(live, back_el, 0.30):
        back_elbow_ang = angle(live, back_sh, back_el, back_wr)
        if 65 <= back_elbow_ang <= 150:
            add_pos("Back arm: comfortable bend (good for a quick/compact launch).")
        elif back_elbow_ang > 150:
            add_imp("Back Arm", "back arm too straight", "Relax the back arm; keep a soft bend so you can launch quickly.")
        else:
            add_imp("Back Arm", "back arm too collapsed", "Open your back elbow slightly. Too tight can restrict a quick turn.")

    if score_ok(live, front_el, 0.30):
        front_elbow_ang = angle(live, front_sh, front_el, front_wr)
        if 60 <= front_elbow_ang <= 155:
            add_pos("Front arm: not locked out (good).")
        elif front_elbow_ang > 155:
            add_imp("Front Arm", "front arm locked out", "Soften the front elbow a bit. A relaxed front arm helps you stay compact.")
        else:
            add_imp("Front Arm", "front arm too tucked", "Give the front arm a little room. Don’t let it collapse tight into the body.")

    # 5) Compact triangle
    if score_ok(live, back_el, 0.30):
        dx_eb_bs = live[back_el]["x"] - live[back_sh]["x"]
        dy_eb_bs = live[back_el]["y"] - live[back_sh]["y"]
        elbow_to_back_sh = norm_dist(dx_eb_bs, dy_eb_bs, live_sw)

        if elbow_to_back_sh is not None:
            if elbow_to_back_sh <= 0.85:
                add_pos("Compact triangle: back elbow stays reasonably close (fast hands setup).")
            else:
                add_imp("Compact Triangle", "back elbow drifting away", "Keep the back elbow closer to the body/shoulder. Compact triangle = faster hands.")

    # 6) Shoulder level
    diff = live["left_shoulder"]["y"] - live["right_shoulder"]["y"]
    if abs(diff) <= 25:
        add_pos("Shoulders: fairly level (good foundation).")
    else:
        add_imp("Shoulders", "shoulders not level", "Try to keep shoulders more level. Big tilt can change your swing path and timing.")

    # 7) Reference comparison (optional)
    ref_sw = shoulder_width(ref) if ref else None
    if ref and ref_sw and all_scores_ok(ref, [front_sh, back_sh, front_wr, back_wr], min_score=0.30) == []:
        ref_hands_x = (ref[back_wr]["x"] + ref[front_wr]["x"]) / 2.0
        ref_hands_y = (ref[back_wr]["y"] + ref[front_wr]["y"]) / 2.0

        live_h_dist = math.hypot(hands_x - live[back_sh]["x"], hands_y - live[back_sh]["y"]) / live_sw
        ref_h_dist = math.hypot(ref_hands_x - ref[back_sh]["x"], ref_hands_y - ref[back_sh]["y"]) / ref_sw

        if abs(live_h_dist - ref_h_dist) > 0.20:
            if live_h_dist > ref_h_dist:
                add_imp("Reference Match", "hands set farther than reference", "Bring hands closer to the back shoulder to match the compact reference.")
            else:
                add_pos("Reference match: hands are at least as compact as the reference (nice).")

    if not positives:
        positives.append("You’re in frame and the camera is tracking. Good start — now let’s tighten the setup.")
    if not improvements:
        improvements.append("All good: compact upper-body setup looks solid — keep hands quiet near the back shoulder and stay quick/compact.")

    rewritten = coach_rewrite(body.handedness, positives, improvements)

    return JSONResponse(content={
        "Positives": {"issue": "Good job", "advice": rewritten["positive"]},
        "Improvements": {"issue": "One thing to work on", "advice": rewritten["improvement"]}
    })
