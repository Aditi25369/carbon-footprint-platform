"""
Recommendation Agent
Ranks carbon reduction actions by impact, feasibility, and local availability.
Uses Gemini 2.0 Flash + Firestore user history + Karnataka-specific data.

Called by LangGraph node: generate_recommendations
"""

import json
import re
from dataclasses import dataclass
from typing import Optional
from vertexai.generative_models import GenerativeModel
from google.cloud import firestore


@dataclass
class Action:
    action_id: str
    title: str
    description: str
    category: str             # transport | home | food | shopping
    co2_saving_monthly: float # tonnes CO2e/month
    cost_inr: float           # upfront cost in INR
    payback_months: int       # months to recover cost
    difficulty: str           # easy | medium | hard
    local_scheme: Optional[str]   # government scheme name if applicable
    impact_score: float           # 0–100 computed score


# ─── Master action catalog (Karnataka / Belagavi context) ─────────────────────
ACTION_CATALOG = [
    {
        "action_id": "switch_to_bus",
        "title": "Switch to GSRTC bus 3x/week",
        "description": "Replace 3 car trips/week with Karnataka GSRTC buses. Monthly pass available at ₹1,500. Coverage: 78% of Belagavi urban routes.",
        "category": "transport",
        "co2_saving_monthly": 0.035,
        "cost_inr": 1500,
        "payback_months": 1,
        "difficulty": "easy",
        "local_scheme": "GSRTC Urban Pass — Belagavi Division"
    },
    {
        "action_id": "rooftop_solar",
        "title": "Install rooftop solar (2kW)",
        "description": "2kW BESCOM-approved rooftop solar. PM Surya Ghar Muft Bijli Yojana gives ₹20,000/kW subsidy. Payback in ~5 years at BESCOM tariffs.",
        "category": "home",
        "co2_saving_monthly": 0.100,
        "cost_inr": 80000,
        "payback_months": 60,
        "difficulty": "hard",
        "local_scheme": "PM Surya Ghar Muft Bijli Yojana + BESCOM net metering"
    },
    {
        "action_id": "solar_water_heater",
        "title": "Solar water heater",
        "description": "Replace electric geyser with 100L solar water heater. KREDL (Karnataka Renewable Energy Dev Ltd) gives 30% subsidy.",
        "category": "home",
        "co2_saving_monthly": 0.025,
        "cost_inr": 18000,
        "payback_months": 24,
        "difficulty": "medium",
        "local_scheme": "KREDL Solar Water Heater Subsidy"
    },
    {
        "action_id": "meatless_monday",
        "title": "Meatless Mondays",
        "description": "Replace Monday meat meals with plant-based alternatives. Traditional Karnataka cuisine (ragi mudde, bisibelebath) is naturally low-carbon.",
        "category": "food",
        "co2_saving_monthly": 0.014,
        "cost_inr": 0,
        "payback_months": 0,
        "difficulty": "easy",
        "local_scheme": None
    },
    {
        "action_id": "local_produce",
        "title": "Buy from Belagavi APMC market",
        "description": "Source fruits & vegetables from the local APMC yard instead of supermarkets. Reduces transport emissions by ~40%. Typically 20–30% cheaper too.",
        "category": "food",
        "co2_saving_monthly": 0.010,
        "cost_inr": 0,
        "payback_months": 0,
        "difficulty": "easy",
        "local_scheme": None
    },
    {
        "action_id": "led_bulbs",
        "title": "Switch all bulbs to LED",
        "description": "Replace remaining CFL/incandescent with LED. EESL (Ujala scheme) offers 9W LEDs at ₹70 each. Payback in under 8 months.",
        "category": "home",
        "co2_saving_monthly": 0.008,
        "cost_inr": 700,
        "payback_months": 8,
        "difficulty": "easy",
        "local_scheme": "UJALA Scheme — EESL LED distribution"
    },
    {
        "action_id": "ev_two_wheeler",
        "title": "Switch to electric two-wheeler",
        "description": "Replace petrol bike with EV (Ola S1, Ather, TVS iQube). FAME II subsidy ₹15,000. Charging at home costs ₹0.50/km vs ₹2.50/km petrol.",
        "category": "transport",
        "co2_saving_monthly": 0.040,
        "cost_inr": 85000,
        "payback_months": 36,
        "difficulty": "hard",
        "local_scheme": "FAME II — ₹15,000 subsidy on EV 2-wheelers"
    },
    {
        "action_id": "ac_temperature",
        "title": "Set AC to 26°C (BEE mandate)",
        "description": "BEE default AC temperature is 24°C — set it to 26°C. Each degree saves 6% electricity. Net saving: ~12% on AC bill per month.",
        "category": "home",
        "co2_saving_monthly": 0.012,
        "cost_inr": 0,
        "payback_months": 0,
        "difficulty": "easy",
        "local_scheme": "BEE Energy Conservation directive"
    },
    {
        "action_id": "composting",
        "title": "Home composting kitchen waste",
        "description": "Compost wet kitchen waste instead of sending to landfill. Reduces methane emissions + produces free fertilizer for kitchen garden.",
        "category": "food",
        "co2_saving_monthly": 0.008,
        "cost_inr": 500,
        "payback_months": 2,
        "difficulty": "medium",
        "local_scheme": "Belagavi Smart City — wet waste composting drive"
    },
    {
        "action_id": "5star_appliances",
        "title": "Upgrade to 5-star appliances",
        "description": "5-star BEE-rated refrigerator + washing machine cuts electricity by 30–40% vs older appliances. Priority: fridge (runs 24/7).",
        "category": "home",
        "co2_saving_monthly": 0.020,
        "cost_inr": 35000,
        "payback_months": 48,
        "difficulty": "hard",
        "local_scheme": None
    },
    {
        "action_id": "carpooling",
        "title": "Carpool for office commute",
        "description": "Share rides with 2–3 colleagues via QuickRide or WhatsApp groups. Halves per-person transport emissions with zero extra cost.",
        "category": "transport",
        "co2_saving_monthly": 0.030,
        "cost_inr": 0,
        "payback_months": 0,
        "difficulty": "medium",
        "local_scheme": None
    },
    {
        "action_id": "second_hand",
        "title": "Buy secondhand electronics & clothes",
        "description": "Source from OLX, Facebook Marketplace, or local Belagavi second-hand shops. Manufacturing new electronics is the highest-carbon shopping category.",
        "category": "shopping",
        "co2_saving_monthly": 0.015,
        "cost_inr": 0,
        "payback_months": 0,
        "difficulty": "easy",
        "local_scheme": None
    },
]


class RecommendationAgent:
    """
    LangGraph Node: generate_recommendations
    
    Scoring formula:
    impact_score = 0.50 * co2_impact_norm
                 + 0.25 * feasibility_score
                 + 0.15 * local_relevance
                 + 0.10 * user_history_bonus
    """

    def __init__(self, gemini_model: GenerativeModel, firestore_client):
        self.model = gemini_model
        self.db    = firestore_client

    async def rank(
        self,
        footprint_data: dict,
        rag_context: str,
        user_profile: dict,
        location: str
    ) -> list[dict]:
        """
        Rank and filter actions for this specific user.
        Returns top 6 actions sorted by impact_score.
        """
        # Get user's completed actions from Firestore
        completed_ids = await self._get_completed_actions(
            footprint_data.get("user_id", "")
        )

        # Score each action
        scored = []
        for action in ACTION_CATALOG:
            score = self._score_action(
                action=action,
                footprint_data=footprint_data,
                completed_ids=completed_ids,
                location=location
            )
            action_with_score = {**action, "impact_score": score, "completed": action["action_id"] in completed_ids}
            scored.append(action_with_score)

        # Sort by score descending
        scored.sort(key=lambda x: x["impact_score"], reverse=True)
        top_actions = scored[:8]

        # Gemini personalisation pass — add a custom tip for top 3
        top_actions = await self._add_gemini_tips(
            top_actions[:3], footprint_data, rag_context
        ) + top_actions[3:]

        return top_actions[:6]

    def _score_action(
        self,
        action: dict,
        footprint_data: dict,
        completed_ids: set,
        location: str
    ) -> float:
        """Multi-factor scoring — all factors 0–1 normalized"""

        # 1. CO2 impact (normalized to max saving in catalog)
        max_saving    = max(a["co2_saving_monthly"] for a in ACTION_CATALOG)
        impact_norm   = action["co2_saving_monthly"] / max_saving

        # 2. Feasibility (inverse of cost and difficulty)
        difficulty_map = {"easy": 1.0, "medium": 0.65, "hard": 0.35}
        cost_norm      = max(0, 1 - (action["cost_inr"] / 150000))   # 0 cost → 1.0
        feasibility    = 0.6 * difficulty_map[action["difficulty"]] + 0.4 * cost_norm

        # 3. Local relevance — boost if action matches user's top emission category
        transport_heavy = footprint_data.get("transport_km", 0) > 400
        home_heavy      = footprint_data.get("electricity_kwh", 0) > 300
        food_heavy      = footprint_data.get("meat_meals_week", 0) > 5

        local_relevance = 0.5  # default
        if action["category"] == "transport" and transport_heavy: local_relevance = 1.0
        if action["category"] == "home"      and home_heavy:      local_relevance = 1.0
        if action["category"] == "food"      and food_heavy:      local_relevance = 1.0

        # Karnataka/Belagavi boost
        if action.get("local_scheme") and "karnataka" in location.lower():
            local_relevance = min(1.0, local_relevance + 0.2)

        # 4. History bonus (already done → slight boost to maintain streak)
        history_bonus = 0.1 if action["action_id"] in completed_ids else 0.0

        score = (
            0.50 * impact_norm
            + 0.25 * feasibility
            + 0.15 * local_relevance
            + 0.10 * history_bonus
        ) * 100

        return round(score, 1)

    async def _add_gemini_tips(
        self,
        top3: list[dict],
        footprint_data: dict,
        rag_context: str
    ) -> list[dict]:
        """Gemini adds a hyper-local one-liner tip to each top-3 action"""
        for action in top3:
            prompt = f"""
            User is in {footprint_data.get('location', 'Belagavi, Karnataka')}.
            Action: {action['title']}
            Standard description: {action['description']}
            
            RAG context: {rag_context[:200]}
            
            Add ONE hyper-local tip (max 20 words) specific to this city/region.
            Output ONLY the tip text, nothing else.
            """
            try:
                response = self.model.generate_content(prompt)
                action["local_tip"] = response.text.strip()
            except Exception:
                action["local_tip"] = None

        return top3

    async def _get_completed_actions(self, user_id: str) -> set:
        if not user_id or not self.db:
            return set()
        try:
            actions_ref = self.db.collection("users").document(user_id)\
                              .collection("actions")\
                              .where("completed", "==", True)
            docs = actions_ref.stream()
            return {doc.id for doc in docs}
        except Exception:
            return set()