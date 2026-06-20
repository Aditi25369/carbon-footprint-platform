"""
Carbon Nexus - Main FastAPI Backend
Deployed on Google Cloud Run
Stack: FastAPI + LangGraph + Gemini 2.0 Flash + Vertex AI + Firestore + Pub/Sub

Local dev: uses GEMINI_API_KEY (free, no billing needed)
Cloud Run:  uses Vertex AI (full Google Cloud stack)
"""

import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ─── Detect mode: local (AI Studio key) vs prod (Vertex AI) ──────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
IS_LOCAL       = bool(GEMINI_API_KEY)
PROJECT_ID     = os.environ.get("GOOGLE_CLOUD_PROJECT", "carbon-nexus-prod")
LOCATION       = os.environ.get("VERTEX_LOCATION", "asia-south1")

print(f"🚀 Mode: {'LOCAL (AI Studio)' if IS_LOCAL else 'PRODUCTION (Vertex AI)'}")

# ─── Gemini model init — switches automatically ───────────────────────────────
if IS_LOCAL:
    # Free AI Studio key — no billing needed
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-2.5-flash")
    print("✅ Gemini AI Studio (free) initialized")
else:
    # Full Vertex AI for Cloud Run
    import vertexai
    from vertexai.generative_models import GenerativeModel, GenerationConfig
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    gemini_model = GenerativeModel(
        "gemini-2.5-flash",
        generation_config=GenerationConfig(temperature=0.7, max_output_tokens=1024)
    )
    print("✅ Vertex AI Gemini initialized")

# ─── Firestore — lazy init, won't crash locally ───────────────────────────────
db = None
try:
    if IS_LOCAL:
        import firebase_admin
        from firebase_admin import credentials, firestore as firebase_firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        db = firebase_firestore.client()
        print("✅ Firestore initialized")
    else:
        import firebase_admin
        from firebase_admin import credentials, firestore as firebase_firestore
        if not firebase_admin._apps:
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, {"projectId": PROJECT_ID})
        db = firebase_firestore.client()
        print("✅ Firestore initialized")
except Exception as e:
    print(f"⚠️  Firestore not available: {e} — DB operations will be skipped")

# ─── Pub/Sub — lazy init ──────────────────────────────────────────────────────
publisher  = None
TOPIC_PATH = None
try:
    from google.cloud import pubsub_v1
    publisher  = pubsub_v1.PublisherClient()
    TOPIC_PATH = publisher.topic_path(PROJECT_ID, "carbon-events")
    print("✅ Pub/Sub initialized")
except Exception as e:
    print(f"⚠️  Pub/Sub not available: {e} — events will be skipped")

# ─── Vector Search ────────────────────────────────────────────────────────────
from rag.vertex_search import VertexAIVectorSearch
vector_search = VertexAIVectorSearch(
    project_id=PROJECT_ID,
    location=LOCATION,
    index_endpoint_id=os.environ.get("VERTEX_INDEX_ENDPOINT_ID", ""),
    deployed_index_id=os.environ.get("VERTEX_DEPLOYED_INDEX_ID", "carbon-kb-index"),
)

# ─── LangGraph ────────────────────────────────────────────────────────────────
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator

from agents.footprint_agent import FootprintAnalysisAgent
from agents.recommendation_agent import RecommendationAgent
from agents.rag_agent import RAGAgent

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Carbon Nexus API",
    description="AI-powered carbon footprint platform for India — Google Cloud Stack",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)

# ─── Pydantic Models ──────────────────────────────────────────────────────────
class FootprintInput(BaseModel):
    user_id: str
    transport_km: float       = 500.0
    flights_per_year: int     = 2
    transit_days_week: int    = 2
    electricity_kwh: float    = 300.0
    lpg_cylinders: float      = 1.0
    meat_meals_week: int      = 4
    local_produce_pct: float  = 40.0
    shopping_spend_inr: float = 5000.0
    location: str             = "Belagavi, Karnataka"

class InsightRequest(BaseModel):
    user_id: str
    question: str
    language: str = "en"

class ActionToggle(BaseModel):
    user_id: str
    action_id: str
    completed: bool

class WeeklyDigestRequest(BaseModel):
    user_id: str

# ─── LangGraph State ──────────────────────────────────────────────────────────
class CarbonState(TypedDict):
    user_id: str
    footprint_data: dict
    calculated_co2: float
    rag_context: str
    recommendations: list
    gemini_insight: str
    user_profile: dict
    messages: Annotated[list, operator.add]

# ─── Gemini helper — works for both local and prod ───────────────────────────
def generate_content(prompt: str) -> str:
    """Unified Gemini call — AI Studio locally, Vertex AI in prod"""
    try:
        response = gemini_model.generate_content(prompt)
        if IS_LOCAL:
            return response.text
        else:
            return response.text
    except Exception as e:
        print(f"⚠️  Gemini error: {e}")
        return "Unable to generate insight at this time."

# ─── LangGraph Pipeline ───────────────────────────────────────────────────────
def build_carbon_graph() -> StateGraph:
    workflow             = StateGraph(CarbonState)
    footprint_agent      = FootprintAnalysisAgent(gemini_model)
    rag_agent            = RAGAgent(vector_search, gemini_model)
    recommendation_agent = RecommendationAgent(gemini_model, db)

    async def calculate_footprint(state: CarbonState) -> CarbonState:
        result = await footprint_agent.calculate(state["footprint_data"])
        return {**state, "calculated_co2": result["total_co2"], "messages": [f"CO2 calculated: {result['total_co2']}t"]}

    async def retrieve_rag_context(state: CarbonState) -> CarbonState:
        context = await rag_agent.retrieve(
            query=f"carbon reduction strategies for {state['footprint_data'].get('location','India')} household",
            co2_breakdown=state["footprint_data"]
        )
        return {**state, "rag_context": context, "messages": ["RAG context retrieved"]}

    async def generate_recommendations(state: CarbonState) -> CarbonState:
        recs = await recommendation_agent.rank(
            footprint_data=state["footprint_data"],
            rag_context=state["rag_context"],
            user_profile=state.get("user_profile", {}),
            location=state["footprint_data"].get("location", "Belagavi, Karnataka")
        )
        return {**state, "recommendations": recs, "messages": [f"{len(recs)} recommendations generated"]}

    async def generate_gemini_insight(state: CarbonState) -> CarbonState:
        prompt = f"""
        You are a carbon footprint advisor specialized in India.
        User location: {state['footprint_data'].get('location')}
        Total monthly CO2: {state['calculated_co2']:.2f} tonnes
        Transport: {state['footprint_data'].get('transport_km')}km driven
        Electricity: {state['footprint_data'].get('electricity_kwh')}kWh
        RAG strategies: {state['rag_context'][:400]}
        Generate a personalized 3-sentence insight:
        1. What they're doing well
        2. Biggest opportunity for reduction
        3. One hyper-local action for {state['footprint_data'].get('location')}
        Keep it warm, encouraging, specific. Under 100 words.
        """
        insight = generate_content(prompt)
        return {**state, "gemini_insight": insight, "messages": ["Gemini insight generated"]}

    workflow.add_node("calculate_footprint",      calculate_footprint)
    workflow.add_node("retrieve_rag_context",     retrieve_rag_context)
    workflow.add_node("generate_recommendations", generate_recommendations)
    workflow.add_node("generate_gemini_insight",  generate_gemini_insight)

    workflow.set_entry_point("calculate_footprint")
    workflow.add_edge("calculate_footprint",      "retrieve_rag_context")
    workflow.add_edge("retrieve_rag_context",     "generate_recommendations")
    workflow.add_edge("generate_recommendations", "generate_gemini_insight")
    workflow.add_edge("generate_gemini_insight",  END)

    return workflow.compile()

carbon_graph = build_carbon_graph()

# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    return {
        "status":    "healthy",
        "mode":      "local-AI-Studio" if IS_LOCAL else "production-VertexAI",
        "project":   PROJECT_ID,
        "region":    LOCATION,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/api/v1/footprint/analyze")
async def analyze_footprint(payload: FootprintInput, background_tasks: BackgroundTasks):
    try:
        initial_state: CarbonState = {
            "user_id":         payload.user_id,
            "footprint_data":  payload.model_dump(),
            "calculated_co2":  0.0,
            "rag_context":     "",
            "recommendations": [],
            "gemini_insight":  "",
            "user_profile":    {},
            "messages":        [],
        }
        result = await carbon_graph.ainvoke(initial_state)

        background_tasks.add_task(save_footprint_to_firestore, payload.user_id, result)
        background_tasks.add_task(publish_carbon_event, event_type="footprint_analyzed",
                                  user_id=payload.user_id, co2=result["calculated_co2"])
        return {
            "user_id":         result["user_id"],
            "total_co2":       round(result["calculated_co2"], 2),
            "recommendations": result["recommendations"],
            "gemini_insight":  result["gemini_insight"],
            "pipeline_log":    result["messages"],
            "timestamp":       datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/insights/ask")
async def ask_carbon_advisor(payload: InsightRequest):
    rag_agent = RAGAgent(vector_search, gemini_model)
    context   = await rag_agent.retrieve(query=payload.question, co2_breakdown={})

    lang_instruction = {
        "en": "Answer in English.",
        "hi": "Answer in Hindi.",
        "kn": "Answer in Kannada.",
        "mr": "Answer in Marathi.",
    }.get(payload.language, "Answer in English.")

    prompt = f"""
    You are a carbon footprint advisor for India.
    Context: {context}
    Question: {payload.question}
    {lang_instruction}
    Under 150 words, practical, India-specific.
    """
    answer = generate_content(prompt)
    return {"answer": answer, "language": payload.language,
            "sources": "Vertex AI Vector Search — Carbon Reduction Knowledge Base"}


@app.post("/api/v1/actions/toggle")
async def toggle_action(payload: ActionToggle):
    if db:
        doc_ref = db.collection("users").document(payload.user_id)\
                    .collection("actions").document(payload.action_id)
        doc_ref.set({"action_id": payload.action_id, "completed": payload.completed,
                     "updated_at": datetime.now(timezone.utc).isoformat()}, merge=True)
        if payload.completed:
            await publish_carbon_event(event_type="action_completed",
                                       user_id=payload.user_id, action_id=payload.action_id)
    return {"status": "updated", "action_id": payload.action_id, "completed": payload.completed}


@app.get("/api/v1/leaderboard/{location}")
async def get_leaderboard(location: str, limit: int = 10):
    if not db:
        return {"location": location, "leaderboard": [], "mode": "local-no-db"}
    try:
        from google.cloud import firestore as fs
        users_ref = db.collection("leaderboard")\
                      .where("location", "==", location)\
                      .order_by("green_score", direction=fs.Query.DESCENDING)\
                      .limit(limit)
        docs  = users_ref.stream()
        board = [{"rank": i+1, **doc.to_dict()} for i, doc in enumerate(docs)]
        return {"location": location, "leaderboard": board}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/user/{user_id}/history")
async def get_user_history(user_id: str, months: int = 6):
    if not db:
        return {"user_id": user_id, "history": [], "mode": "local-no-db"}
    history_ref = db.collection("users").document(user_id)\
                    .collection("monthly_history")\
                    .order_by("month", direction="DESCENDING")\
                    .limit(months)
    docs = history_ref.stream()
    return {"user_id": user_id, "history": [doc.to_dict() for doc in docs]}


@app.post("/api/v1/digest/trigger")
async def trigger_weekly_digest(payload: WeeklyDigestRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(publish_carbon_event, event_type="weekly_digest_requested",
                               user_id=payload.user_id)
    return {"status": "digest queued", "user_id": payload.user_id}


# ─── Background Tasks ─────────────────────────────────────────────────────────

async def save_footprint_to_firestore(user_id: str, result: dict):
    if not db:
        return
    try:
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        doc_ref   = db.collection("users").document(user_id)\
                       .collection("monthly_history").document(month_key)
        doc_ref.set({
            "month":           month_key,
            "total_co2":       result["calculated_co2"],
            "recommendations": result["recommendations"][:3],
            "gemini_insight":  result["gemini_insight"],
            "updated_at":      datetime.now(timezone.utc).isoformat()
        }, merge=True)
    except Exception as e:
        print(f"Firestore save error: {e}")


async def publish_carbon_event(event_type: str, user_id: str, **kwargs):
    if not publisher:
        return
    try:
        message       = {"event_type": event_type, "user_id": user_id,
                         "timestamp": datetime.now(timezone.utc).isoformat(), **kwargs}
        message_bytes = json.dumps(message).encode("utf-8")
        future        = publisher.publish(TOPIC_PATH, message_bytes)
        future.result(timeout=5)
    except Exception as e:
        print(f"Pub/Sub publish error: {e}")


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", 8080)), reload=False)
