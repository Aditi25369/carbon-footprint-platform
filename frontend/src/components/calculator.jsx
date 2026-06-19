/**
 * Calculator.jsx — Interactive carbon footprint calculator
 * - Slider-based inputs for all emission categories
 * - Real-time CO2 estimate (client-side formula)
 * - Gemini 2.0 Flash analysis via backend LangGraph pipeline (real call, not mocked)
 * - Saves result to Firestore via backend (only if signed in)
 */

import { useState, useCallback } from "react";
import { getGeminiInsight, analyzeFootprint, UI_STRINGS } from "../lib/gemini";
import { trackEvent } from "../lib/firebase";

// ─── Emission factors (mirrors backend for instant client-side preview) ──────
const EF = {
    car: 0.182,    // kg CO2/km (petrol/diesel mix India)
    flight: 0.133,    // kg CO2/km per passenger (domestic)
    elec: 0.00072,  // tonnes CO2/kWh (Karnataka grid)
    lpg: 0.02946,  // tonnes CO2/cylinder
    meat: 0.003,    // tonnes CO2/meal (avg India meat mix)
    veg: 0.00084,  // tonnes CO2/meal
    shop: 0.00082,  // tonnes CO2/₹1000 spent
};

const DEFAULT_INPUTS = {
    transport_km: 500,
    flights_per_year: 2,
    transit_days_week: 2,
    electricity_kwh: 300,
    lpg_cylinders: 1,
    meat_meals_week: 4,
    local_produce_pct: 40,
    shopping_spend_inr: 5000,
};

const INDIA_AVG = 0.545;

const CATEGORY_LABELS = {
    transport: "Transport",
    home: "Home energy",
    food: "Food",
    shopping: "Shopping",
};


// ─── Client-side CO2 estimate ──────────────────────────────────────────────

function estimateCO2(inputs) {
    const car = inputs.transport_km * EF.car / 1000;
    const flight = (inputs.flights_per_year * 1200 * EF.flight) / 12 / 1000;
    const transit = inputs.transit_days_week * 4 * 15 * (0.182 - 0.027) / 1000;
    const elec = inputs.electricity_kwh * EF.elec;
    const lpg = inputs.lpg_cylinders * EF.lpg;
    const food = (inputs.meat_meals_week * EF.meat + (21 - inputs.meat_meals_week) * EF.veg) * 4.33;
    const local = food * (inputs.local_produce_pct / 100) * 0.15;
    const shop = (inputs.shopping_spend_inr / 1000) * EF.shop;

    return {
        transport: Math.max(0, car + flight - transit),
        home: elec + lpg,
        food: Math.max(0, food - local),
        shopping: shop,
        total: Math.max(0, car + flight - transit + elec + lpg + food - local + shop),
    };
}

// Identify the single biggest contributor — feeds the "explainable AI" line
function biggestContributor(inputs, co2) {
    const entries = [
        ["transport", co2.transport],
        ["home", co2.home],
        ["food", co2.food],
        ["shopping", co2.shopping],
    ].sort((a, b) => b[1] - a[1]);

    const [topCat] = entries[0];

    const reasons = {
        transport: inputs.transport_km > 800
            ? "high monthly car distance"
            : inputs.flights_per_year > 4
                ? "frequent domestic flights"
                : "daily driving habits",
        home: inputs.electricity_kwh > 400
            ? "above-average electricity usage"
            : "LPG-heavy cooking",
        food: inputs.meat_meals_week > 7
            ? "frequent meat consumption"
            : "low local-produce sourcing",
        shopping: "consumer goods spending",
    };

    return { category: topCat, reason: reasons[topCat] };
}


// ─── Component ────────────────────────────────────────────────────────────────

export default function Calculator({ user, language = "en", onAnalysisComplete }) {
    const [inputs, setInputs] = useState(DEFAULT_INPUTS);
    const [activeTab, setActiveTab] = useState("transport");
    const [insight, setInsight] = useState("");
    const [recommendations, setRecommendations] = useState([]);
    const [loadingAI, setLoadingAI] = useState(false);
    const [aiError, setAiError] = useState("");
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    const co2 = estimateCO2(inputs);
    const vsIndia = ((co2.total - INDIA_AVG) / INDIA_AVG * 100).toFixed(0);
    const ui = UI_STRINGS[language] || UI_STRINGS.en;
    const topReason = biggestContributor(inputs, co2);

    const updateInput = useCallback((key, val) => {
        setInsight("");
        setRecommendations([]);
        setAiError("");
        setSaved(false);
        setInputs((prev) => ({ ...prev, [key]: parseFloat(val) }));
    }, []);

    // ─── Gemini analysis — real backend call via LangGraph pipeline ─────────
    const analyzeWithGemini = async () => {
        setInsight("");
        setAiError("");
        setLoadingAI(true);
        trackEvent("gemini_analysis_started");

        try {
            const userId = user?.uid || "guest-" + Math.random().toString(36).slice(2, 8);
            const result = await getGeminiInsight(
                { location: user?.location || "Belagavi, Karnataka", ...inputs },
                userId
            );
            setInsight(result.insight);
            setRecommendations(result.recommendations || []);
            onAnalysisComplete?.(result);   // share with App.jsx → Actions tab
            trackEvent("gemini_analysis_completed", { co2: co2.total.toFixed(2) });
        } catch (err) {
            console.error("Gemini analysis failed:", err);
            setAiError("⚠️ Couldn't reach the AI advisor. Make sure the backend is running on " +
                (import.meta.env.VITE_CLOUD_RUN_URL || "http://localhost:8080"));
        } finally {
            setLoadingAI(false);
        }
    };

    // ─── Save to Firestore (only if signed in) ───────────────────────────────
    const saveAnalysis = async () => {
        if (!user?.uid) return;
        setSaving(true);
        try {
            const token = user.getIdToken ? await user.getIdToken() : null;
            await analyzeFootprint(
                { user_id: user.uid, location: user.location || "Belagavi, Karnataka", ...inputs },
                token
            );
            setSaved(true);
            trackEvent("footprint_saved", { co2: co2.total.toFixed(2) });
        } catch (err) {
            console.error("Save failed:", err);
        } finally {
            setSaving(false);
        }
    };

    const TABS = [
        { id: "transport", label: "🚗 Transport" },
        { id: "home", label: "🏠 Home" },
        { id: "food", label: "🥗 Food" },
        { id: "shopping", label: "🛍 Shopping" },
    ];

    return (
        <div className="calculator">

            {/* ── Category tabs ──────────────────────────────────────────────── */}
            <div className="cat-tabs">
                {TABS.map((t) => (
                    <button
                        key={t.id}
                        className={`cat-tab ${activeTab === t.id ? "cat-tab--active" : ""}`}
                        onClick={() => setActiveTab(t.id)}
                    >
                        {t.label}
                    </button>
                ))}
            </div>

            {/* ── Sliders ────────────────────────────────────────────────────── */}
            <div className="sliders-section">

                {activeTab === "transport" && (
                    <>
                        <SliderRow
                            label="Km driven by car/month"
                            value={inputs.transport_km}
                            min={0} max={2000} step={10}
                            unit="km"
                            onChange={(v) => updateInput("transport_km", v)}
                            co2hint={`~${(inputs.transport_km * EF.car / 1000).toFixed(3)}t CO₂`}
                        />
                        <SliderRow
                            label="Flights per year"
                            value={inputs.flights_per_year}
                            min={0} max={20} step={1}
                            unit="flights"
                            onChange={(v) => updateInput("flights_per_year", v)}
                            co2hint={`~${((inputs.flights_per_year * 1200 * EF.flight) / 12 / 1000).toFixed(3)}t/mo`}
                        />
                        <SliderRow
                            label="Public transit days/week"
                            value={inputs.transit_days_week}
                            min={0} max={7} step={1}
                            unit="days"
                            onChange={(v) => updateInput("transit_days_week", v)}
                            co2hint="saves CO₂ vs driving"
                            positive
                        />
                    </>
                )}

                {activeTab === "home" && (
                    <>
                        <SliderRow
                            label="Electricity units/month"
                            value={inputs.electricity_kwh}
                            min={0} max={1000} step={10}
                            unit="kWh"
                            onChange={(v) => updateInput("electricity_kwh", v)}
                            co2hint={`Karnataka grid: ${(inputs.electricity_kwh * EF.elec).toFixed(3)}t CO₂`}
                        />
                        <SliderRow
                            label="LPG cylinders/month"
                            value={inputs.lpg_cylinders}
                            min={0} max={5} step={0.5}
                            unit="cylinders"
                            onChange={(v) => updateInput("lpg_cylinders", v)}
                            co2hint={`${(inputs.lpg_cylinders * EF.lpg).toFixed(3)}t CO₂`}
                        />
                    </>
                )}

                {activeTab === "food" && (
                    <>
                        <SliderRow
                            label="Meat meals per week"
                            value={inputs.meat_meals_week}
                            min={0} max={21} step={1}
                            unit="meals"
                            onChange={(v) => updateInput("meat_meals_week", v)}
                            co2hint={`~${(inputs.meat_meals_week * EF.meat * 4.33).toFixed(3)}t CO₂/month`}
                        />
                        <SliderRow
                            label="Local produce percentage"
                            value={inputs.local_produce_pct}
                            min={0} max={100} step={5}
                            unit="%"
                            onChange={(v) => updateInput("local_produce_pct", v)}
                            co2hint="higher % = lower emissions"
                            positive
                        />
                    </>
                )}

                {activeTab === "shopping" && (
                    <SliderRow
                        label="Monthly shopping spend"
                        value={inputs.shopping_spend_inr}
                        min={0} max={50000} step={500}
                        unit="₹"
                        prefix="₹"
                        onChange={(v) => updateInput("shopping_spend_inr", v)}
                        co2hint={`~${((inputs.shopping_spend_inr / 1000) * EF.shop).toFixed(3)}t CO₂`}
                    />
                )}
            </div>

            {/* ── CO2 Result ─────────────────────────────────────────────────── */}
            <div className="co2-result">
                <div className="co2-main">
                    <span className="co2-number">{co2.total.toFixed(2)}</span>
                    <span className="co2-unit"> tonnes CO₂e/month</span>
                </div>
                <div className={`co2-compare ${parseFloat(vsIndia) < 0 ? "co2-compare--good" : "co2-compare--bad"}`}>
                    {vsIndia > 0 ? "+" : ""}{vsIndia}% vs India average
                </div>
                <div className="co2-breakdown">
                    {["transport", "home", "food", "shopping"].map((cat) => (
                        <div key={cat} className="co2-breakdown-item">
                            <span className="co2-cat">{cat}</span>
                            <span className="co2-cat-val">{co2[cat].toFixed(3)}t</span>
                        </div>
                    ))}
                </div>

                {/* Explainable AI line — why is this your footprint */}
                <div className="co2-explain">
                    <strong>Main driver:</strong> {CATEGORY_LABELS[topReason.category]} — {topReason.reason}
                </div>
            </div>

            {/* ── Gemini AI Analysis ─────────────────────────────────────────── */}
            <button className="btn-gemini" onClick={analyzeWithGemini} disabled={loadingAI}>
                {loadingAI ? "✨ Gemini is analyzing..." : `✨ ${ui.analyze}`}
            </button>

            {aiError && <div className="gemini-error">{aiError}</div>}

            {insight && (
                <div className="gemini-insight">
                    <div className="gemini-badge">🤖 Gemini 2.0 Flash · LangGraph + RAG</div>
                    <p className="gemini-text">{insight}</p>
                </div>
            )}

            {recommendations.length > 0 && (
                <div className="card">
                    <h3 className="card-title">Recommended actions for you</h3>
                    {recommendations.slice(0, 3).map((rec) => (
                        <div key={rec.action_id} className="rec-item">
                            <div className="rec-header">
                                <span className="rec-title">{rec.title}</span>
                                <span className="rec-impact">-{rec.co2_saving_monthly}t/mo</span>
                            </div>
                            <p className="rec-desc">{rec.description}</p>
                            {rec.local_scheme && <span className="rec-scheme">📍 {rec.local_scheme}</span>}
                        </div>
                    ))}
                </div>
            )}

            {/* ── Save button — only shown when signed in ──────────────────────── */}
            {user && (
                <button
                    className={`btn-save ${saved ? "btn-save--done" : ""}`}
                    onClick={saveAnalysis}
                    disabled={saving || saved}
                >
                    {saved ? "✅ Saved to your profile!" : saving ? "Saving..." : "Save to Firestore"}
                </button>
            )}
            {!user && (
                <div className="signin-hint">Sign in to save this analysis and track it over time.</div>
            )}
        </div>
    );
}


// ─── SliderRow sub-component ──────────────────────────────────────────────────

function SliderRow({ label, value, min, max, step, unit, prefix = "", onChange, co2hint, positive = false }) {
    return (
        <div className="slider-row">
            <div className="slider-header">
                <span className="slider-label">{label}</span>
                <span className="slider-value">{prefix}{value} {unit}</span>
            </div>
            <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className="slider"
            />
            <div className={`slider-hint ${positive ? "slider-hint--positive" : ""}`}>
                {co2hint}
            </div>
        </div>
    );
}