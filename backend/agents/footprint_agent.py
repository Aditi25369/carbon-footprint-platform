"""
Footprint Analysis Agent
Uses India-specific emission factors (CEF 2023, IPCC AR6)
Called by LangGraph node: calculate_footprint
"""

from dataclasses import dataclass
from vertexai.generative_models import GenerativeModel


# ─── India-specific Emission Factors ─────────────────────────────────────────
# Sources: Central Electricity Authority (CEA) 2023, IPCC AR6, MoEFCC India
EMISSION_FACTORS = {

    # Transport (kg CO2e per km)
    "car_petrol":       0.192,   # average Indian petrol car
    "car_diesel":       0.171,   # average Indian diesel car
    "car_cng":          0.134,   # CNG car (common in Indian cities)
    "two_wheeler":      0.053,   # motorbike/scooter
    "bus_gsrtc":        0.027,   # Karnataka GSRTC per passenger-km
    "auto_rickshaw":    0.045,
    "flight_domestic":  0.133,   # kg CO2 per km per passenger (IndiGo/Air India avg)
    "flight_short_intl": 0.195,

    # Electricity — Karnataka grid (kg CO2e per kWh)
    # Karnataka has ~50% renewables, so lower than national average
    "electricity_karnataka": 0.00072,   # 0.72 kg/kWh
    "electricity_india_avg": 0.00082,   # national average

    # LPG cooking gas (kg CO2 per cylinder, 14.2 kg cylinder)
    "lpg_cylinder":     29.46,

    # Food (kg CO2e per meal)
    "meat_beef":        6.61,
    "meat_chicken":     2.33,
    "meat_mutton":      5.84,
    "fish":             1.34,
    "vegetarian":       0.84,
    "local_produce_factor": 0.85,   # 15% reduction for local sourcing

    # Shopping (kg CO2e per ₹1000 INR spent)
    "shopping_general": 0.82,
    "shopping_electronics": 2.10,
    "shopping_clothing":    1.35,

    # Home
    "ac_per_hour":      0.72,    # 1.5 ton split AC, Karnataka grid
    "water_heater_elec": 0.36,
    "water_heater_solar": 0.04,
}


@dataclass
class FootprintBreakdown:
    transport_co2: float
    home_co2: float
    food_co2: float
    shopping_co2: float
    total_co2: float
    breakdown_pct: dict
    vs_india_avg: float
    vs_global_avg: float


class FootprintAnalysisAgent:
    """
    LangGraph Node: calculate_footprint
    
    Calculates monthly CO2 from user inputs using India-specific factors.
    Falls back to Gemini for edge cases or ambiguous inputs.
    """

    INDIA_MONTHLY_AVG  = 0.545   # tonnes CO2e/person/month (~6.5t/year, MoEFCC 2023)
    GLOBAL_MONTHLY_AVG = 0.833   # tonnes CO2e/person/month (~10t/year, OWID 2023)

    def __init__(self, gemini_model: GenerativeModel):
        self.model = gemini_model

    async def calculate(self, footprint_data: dict) -> dict:
        """Main calculation entry point called by LangGraph"""

        transport_co2 = self._calculate_transport(footprint_data)
        home_co2      = self._calculate_home(footprint_data)
        food_co2      = self._calculate_food(footprint_data)
        shopping_co2  = self._calculate_shopping(footprint_data)

        total = transport_co2 + home_co2 + food_co2 + shopping_co2

        breakdown_pct = {
            "transport": round(transport_co2 / total * 100, 1) if total > 0 else 0,
            "home":      round(home_co2      / total * 100, 1) if total > 0 else 0,
            "food":      round(food_co2      / total * 100, 1) if total > 0 else 0,
            "shopping":  round(shopping_co2  / total * 100, 1) if total > 0 else 0,
        }

        green_score = self._calculate_green_score(
            total, transport_co2, home_co2, food_co2, footprint_data
        )

        return {
            "total_co2":       round(total, 3),
            "transport_co2":   round(transport_co2, 3),
            "home_co2":        round(home_co2, 3),
            "food_co2":        round(food_co2, 3),
            "shopping_co2":    round(shopping_co2, 3),
            "breakdown_pct":   breakdown_pct,
            "green_score":     green_score,
            "vs_india_avg":    round((total - self.INDIA_MONTHLY_AVG) / self.INDIA_MONTHLY_AVG * 100, 1),
            "vs_global_avg":   round((total - self.GLOBAL_MONTHLY_AVG) / self.GLOBAL_MONTHLY_AVG * 100, 1),
            "unit":            "tonnes CO2e/month",
        }

    def _calculate_transport(self, data: dict) -> float:
        """Transport emissions — car + flights + transit offset"""
        ef = EMISSION_FACTORS

        # Car driving (assume 60% petrol, 40% diesel for Karnataka)
        car_co2 = data.get("transport_km", 0) * (
            0.6 * ef["car_petrol"] + 0.4 * ef["car_diesel"]
        ) / 1000   # convert kg → tonnes

        # Domestic flights (prorated monthly)
        flight_co2 = (data.get("flights_per_year", 0) * 1200 * ef["flight_domestic"]) / 12 / 1000
        # 1200km = avg Indian domestic flight distance

        # Public transit offset (each transit day replaces ~15km of car driving)
        transit_offset = data.get("transit_days_week", 0) * 4 * 15 * (
            ef["car_petrol"] - ef["bus_gsrtc"]
        ) / 1000

        return max(0, car_co2 + flight_co2 - transit_offset)

    def _calculate_home(self, data: dict) -> float:
        """Home energy emissions — electricity + LPG"""
        ef = EMISSION_FACTORS

        location = data.get("location", "Karnataka")
        elec_factor = (
            ef["electricity_karnataka"]
            if "karnataka" in location.lower()
            else ef["electricity_india_avg"]
        )

        elec_co2 = data.get("electricity_kwh", 0) * elec_factor
        lpg_co2  = data.get("lpg_cylinders", 0) * ef["lpg_cylinder"] / 1000

        return elec_co2 + lpg_co2

    def _calculate_food(self, data: dict) -> float:
        """Food emissions — meat meals + local produce adjustment"""
        ef = EMISSION_FACTORS

        meat_meals   = data.get("meat_meals_week", 0)
        local_pct    = data.get("local_produce_pct", 40) / 100

        # India meat mix: ~40% chicken, 30% mutton, 20% fish, 10% beef (Kerala/Northeast)
        avg_meat_ef = (
            0.40 * ef["meat_chicken"]
            + 0.30 * ef["meat_mutton"]
            + 0.20 * ef["fish"]
            + 0.10 * ef["meat_beef"]
        )

        # Non-meat meals (21 meals/week - meat meals)
        veg_meals = 21 - meat_meals

        weekly_co2 = (meat_meals * avg_meat_ef) + (veg_meals * ef["vegetarian"])
        monthly_co2 = weekly_co2 * 4.33 / 1000  # 4.33 weeks/month

        # Local produce discount
        local_saving = monthly_co2 * local_pct * (1 - ef["local_produce_factor"])
        return max(0, monthly_co2 - local_saving)

    def _calculate_shopping(self, data: dict) -> float:
        """Shopping emissions — general consumer goods"""
        ef = EMISSION_FACTORS
        spend_thousands = data.get("shopping_spend_inr", 0) / 1000
        return spend_thousands * ef["shopping_general"] / 1000   # → tonnes

    def _calculate_green_score(
        self,
        total: float,
        transport_co2: float,
        home_co2: float,
        food_co2: float,
        data: dict
    ) -> int:
        """
        0–100 green score.
        50 = India average, 100 = near-zero, 0 = >3x India average
        """
        base_score = 100

        # Deductions based on CO2 vs India average
        india_avg = self.INDIA_MONTHLY_AVG
        ratio     = total / india_avg if india_avg > 0 else 1

        if ratio <= 0.5:   base_score -= 0
        elif ratio <= 0.8: base_score -= 10
        elif ratio <= 1.0: base_score -= 20
        elif ratio <= 1.5: base_score -= 35
        elif ratio <= 2.0: base_score -= 55
        else:              base_score -= 70

        # Bonus points for good behaviours
        if data.get("transit_days_week", 0) >= 3:   base_score += 5
        if data.get("local_produce_pct", 0) >= 60:  base_score += 5
        if data.get("meat_meals_week", 0) <= 2:     base_score += 5
        if data.get("flights_per_year", 0) == 0:    base_score += 5

        return max(0, min(100, base_score))

    async def gemini_edge_case(self, description: str) -> float:
        """
        Fallback: use Gemini 2.0 Flash to estimate CO2 for non-standard inputs
        e.g. "I ride a bicycle to work" or "I have solar panels"
        """
        prompt = f"""
        Estimate the monthly CO2 impact in kg CO2e for this activity in India:
        "{description}"
        
        Respond with ONLY a JSON object: {{"co2_kg": <number>, "reasoning": "<1 sentence>"}}
        """
        response = self.model.generate_content(prompt)
        import json, re
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("co2_kg", 0) / 1000   # kg → tonnes
        return 0.0