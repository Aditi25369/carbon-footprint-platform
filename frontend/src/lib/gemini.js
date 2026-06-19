/**
 * Gemini API Client — Frontend
 * All Gemini calls go through Cloud Run/local backend (never expose API keys client-side).
 * Multilingual: en | hi | kn | mr
 */

const API_BASE = import.meta.env.VITE_CLOUD_RUN_URL || "http://localhost:8080";

// ─── Core API Call ────────────────────────────────────────────────────────────

async function callAPI(endpoint, body, token = null) {
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
    });

    if (!res.ok) {
        const err = await res.text();
        throw new Error(`API error ${res.status}: ${err}`);
    }
    return res.json();
}


// ─── Footprint Analysis (LangGraph Pipeline) ─────────────────────────────────

/**
 * Full multi-agent pipeline:
 * calculate → RAG retrieve → recommend → Gemini insight
 * Returns: { total_co2, recommendations, gemini_insight, breakdown }
 */
export async function analyzeFootprint(footprintData, token) {
    return callAPI("/api/v1/footprint/analyze", footprintData, token);
}


// ─── RAG-powered Q&A ──────────────────────────────────────────────────────────

/**
 * Ask a carbon question — answered by Gemini + Vertex AI RAG
 * Supports multilingual responses
 */
export async function askAdvisor(question, userId, language = "en", token = null) {
    return callAPI("/api/v1/insights/ask", { user_id: userId, question, language }, token);
}


// ─── Action Toggle ────────────────────────────────────────────────────────────

export async function toggleAction(userId, actionId, completed, token) {
    return callAPI("/api/v1/actions/toggle", { user_id: userId, action_id: actionId, completed }, token);
}


// ─── Weekly Digest ────────────────────────────────────────────────────────────

export async function requestWeeklyDigest(userId, token) {
    return callAPI("/api/v1/digest/trigger", { user_id: userId }, token);
}


// ─── Gemini Insight (real backend call — replaces old fake streaming) ───────

/**
 * Calls YOUR backend's /api/v1/footprint/analyze endpoint, which runs the
 * full LangGraph pipeline (calculate → RAG → recommend → Gemini insight)
 * using either Vertex AI (prod) or AI Studio key (local) — set server-side.
 *
 * Returns the same shape as analyzeFootprint() — use result.gemini_insight
 * for the AI text and result.recommendations for the actions list.
 */
export async function getGeminiInsight(footprintData, userId, token = null) {
    const payload = { user_id: userId || "guest", ...footprintData };
    const result = await callAPI("/api/v1/footprint/analyze", payload, token);
    return {
        insight: result.gemini_insight,
        recommendations: result.recommendations,
        total_co2: result.total_co2,
    };
}


// ─── Multilingual Helpers ─────────────────────────────────────────────────────

export const SUPPORTED_LANGUAGES = [
    { code: "en", label: "English", flag: "🇬🇧" },
    { code: "hi", label: "हिंदी", flag: "🇮🇳" },
    { code: "kn", label: "ಕನ್ನಡ", flag: "🏴" },
    { code: "mr", label: "मराठी", flag: "🏴" },
];

export const UI_STRINGS = {
    en: { title: "Carbon Nexus", analyze: "Analyze with Gemini AI", ask: "Ask Advisor" },
    hi: { title: "कार्बन नेक्सस", analyze: "Gemini AI से विश्लेषण करें", ask: "सलाहकार से पूछें" },
    kn: { title: "ಕಾರ್ಬನ್ ನೆಕ್ಸಸ್", analyze: "Gemini AI ನಿಂದ ವಿಶ್ಲೇಷಿಸಿ", ask: "ಸಲಹೆಗಾರರನ್ನು ಕೇಳಿ" },
    mr: { title: "कार्बन नेक्सस", analyze: "Gemini AI ने विश्लेषण करा", ask: "सल्लागाराला विचारा" },
};