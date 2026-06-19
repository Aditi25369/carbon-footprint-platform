/**
 * App.jsx — Carbon Nexus Root Component
 * Handles: Firebase Auth, routing between tabs, language selector,
 * and shares the last footprint analysis across tabs (Calculator → Actions).
 */

import { useState, useEffect } from "react";
import { signInWithGoogle, signOutUser, onAuthChange, saveUserProfile } from "./lib/firebase";
import Dashboard from "./components/Dashboard";
import Calculator from "./components/Calculator";
import "./index.css";

const TABS = [
    { id: "dashboard", label: "📊 Dashboard" },
    { id: "calculator", label: "🧮 Calculator" },
    { id: "actions", label: "✅ Actions" },
    { id: "insights", label: "✨ AI Insights" },
    { id: "community", label: "🏆 Community" },
];

export default function App() {
    const [user, setUser] = useState(null);
    const [authLoading, setAuthLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("dashboard");
    const [language, setLanguage] = useState("en");

    // Shared analysis result — set by Calculator, read by Actions tab.
    // Lives here (not in Calculator's local state) so it survives tab switches.
    const [lastAnalysis, setLastAnalysis] = useState(null);

    // ─── Firebase Auth listener ──────────────────────────────────────────────
    useEffect(() => {
        const unsub = onAuthChange(async (firebaseUser) => {
            if (firebaseUser) {
                setUser(firebaseUser);   // unblock UI immediately — don't wait on Firestore
                try {
                    await saveUserProfile(firebaseUser.uid, {
                        uid: firebaseUser.uid,
                        display_name: firebaseUser.displayName,
                        email: firebaseUser.email,
                        photo_url: firebaseUser.photoURL,
                        location: "Belagavi",
                        last_login: new Date().toISOString(),
                    });
                } catch (err) {
                    console.warn("⚠️ Could not save profile to Firestore (non-blocking):", err.message);
                }
            } else {
                setUser(null);
            }
            setAuthLoading(false);
        });
        return () => unsub();
    }, []);

    if (authLoading) return <SplashScreen />;

    return (
        <div className="app-root">

            {/* ── Header ──────────────────────────────────────────────────────── */}
            <header className="app-header">
                <div className="header-left">
                    <div className="logo-mark">🌿</div>
                    <div>
                        <div className="logo-title">Carbon Nexus</div>
                        <div className="logo-sub">Gemini 2.0 Flash · Vertex AI · Firebase</div>
                    </div>
                </div>

                <div className="header-right">
                    <select
                        className="lang-select"
                        value={language}
                        onChange={(e) => setLanguage(e.target.value)}
                    >
                        <option value="en">🇬🇧 EN</option>
                        <option value="hi">🇮🇳 हिंदी</option>
                        <option value="kn">ಕನ್ನಡ</option>
                        <option value="mr">मराठी</option>
                    </select>

                    {user ? (
                        <div className="user-menu">
                            {user.photoURL && (
                                <img src={user.photoURL} alt="avatar" className="user-avatar" />
                            )}
                            <span className="user-name">{user.displayName?.split(" ")[0]}</span>
                            <button className="btn-signout" onClick={signOutUser}>Sign out</button>
                        </div>
                    ) : (
                        <button className="btn-google-signin" onClick={signInWithGoogle}>
                            <GoogleIcon /> Sign in with Google
                        </button>
                    )}
                </div>
            </header>

            {/* ── Nav tabs ────────────────────────────────────────────────────── */}
            <nav className="app-nav">
                {TABS.map((tab) => (
                    <button
                        key={tab.id}
                        className={`nav-tab ${activeTab === tab.id ? "nav-tab--active" : ""}`}
                        onClick={() => setActiveTab(tab.id)}
                    >
                        {tab.label}
                        {tab.id === "actions" && lastAnalysis?.recommendations?.length > 0 && (
                            <span className="nav-tab-badge">{lastAnalysis.recommendations.length}</span>
                        )}
                    </button>
                ))}
            </nav>

            {/* ── Page content ────────────────────────────────────────────────── */}
            <main className="app-main">
                {!user && activeTab !== "calculator" && (
                    <div className="auth-banner">
                        <span>Sign in with Google to save your footprint and join the leaderboard</span>
                        <button className="btn-google-signin btn-google-signin--small" onClick={signInWithGoogle}>
                            <GoogleIcon /> Sign in
                        </button>
                    </div>
                )}

                {activeTab === "dashboard" && (
                    <Dashboard user={user} language={language} />
                )}

                {activeTab === "calculator" && (
                    <Calculator
                        user={user}
                        language={language}
                        onAnalysisComplete={setLastAnalysis}
                    />
                )}

                {activeTab === "actions" && (
                    <ActionsTab analysis={lastAnalysis} onGoToCalculator={() => setActiveTab("calculator")} />
                )}

                {activeTab === "insights" && (
                    <InsightsTab language={language} user={user} />
                )}

                {activeTab === "community" && (
                    <CommunityPlaceholder user={user} />
                )}
            </main>

            {/* ── Footer ──────────────────────────────────────────────────────── */}
            <footer className="app-footer">
                <span>Carbon Nexus · Built for Google Gen AI Academy APAC — 1000 Builders Challenge</span>
                <span className="footer-stack">
                    Gemini 2.0 Flash · Vertex AI · Cloud Run · Firestore · Pub/Sub · Firebase
                </span>
            </footer>
        </div>
    );
}


// ─── Actions tab — real data, no longer a placeholder ─────────────────────────

function ActionsTab({ analysis, onGoToCalculator }) {
    const [completedIds, setCompletedIds] = useState(new Set());

    const toggleDone = (id) => {
        setCompletedIds((prev) => {
            const next = new Set(prev);
            next.has(id) ? next.delete(id) : next.add(id);
            return next;
        });
    };

    if (!analysis || !analysis.recommendations || analysis.recommendations.length === 0) {
        return (
            <div className="placeholder-page">
                <h2>✅ Personalized Actions</h2>
                <p>Ranked by CO₂ impact · Powered by Gemini + RAG · Karnataka-specific schemes</p>
                <div className="placeholder-note">
                    Run an analysis in the Calculator tab first — your personalized actions
                    will appear here automatically.
                </div>
                <button className="btn-primary" style={{ marginTop: 14 }} onClick={onGoToCalculator}>
                    Go to Calculator →
                </button>
            </div>
        );
    }

    const totalPotentialSaving = analysis.recommendations
        .reduce((sum, r) => sum + (r.co2_saving_monthly || 0), 0);

    return (
        <div>
            <div className="card">
                <h3 className="card-title">Your personalized actions</h3>
                <p style={{ fontSize: 13, color: "var(--gray-600)", marginBottom: 14 }}>
                    Based on your last analysis ({analysis.total_co2}t CO₂/month) — completing all of these
                    could save up to <strong style={{ color: "var(--green)" }}>
                        {totalPotentialSaving.toFixed(3)}t CO₂/month
                    </strong>.
                </p>

                {analysis.recommendations.map((action) => (
                    <div
                        key={action.action_id}
                        className={`action-card ${completedIds.has(action.action_id) ? "action-card--done" : ""}`}
                        onClick={() => toggleDone(action.action_id)}
                    >
                        <div className="action-card-header">
                            <span className="action-card-check">
                                {completedIds.has(action.action_id) ? "✅" : "⬜"}
                            </span>
                            <span className="action-card-title">{action.title}</span>
                            <span className="action-card-impact">-{action.co2_saving_monthly}t/mo</span>
                        </div>
                        <p className="action-card-desc">{action.description}</p>
                        <div className="action-card-meta">
                            {action.local_scheme && <span className="action-tag">📍 {action.local_scheme}</span>}
                            {action.difficulty && <span className="action-tag">⚡ {action.difficulty}</span>}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}


// ─── AI Insights tab ───────────────────────────────────────────────────────────

function InsightsTab({ language, user }) {
    const [question, setQuestion] = useState("");
    const [answer, setAnswer] = useState("");
    const [loading, setLoading] = useState(false);

    const ask = async () => {
        if (!question.trim()) return;
        setLoading(true);
        setAnswer("");
        try {
            const { askAdvisor } = await import("./lib/gemini");
            const res = await askAdvisor(question, user?.uid || "guest", language);
            setAnswer(res.answer);
        } catch {
            setAnswer("⚠️ Could not reach advisor. Make sure the backend is running.");
        }
        setLoading(false);
    };

    return (
        <div className="placeholder-page">
            <h2>✨ AI Carbon Advisor</h2>
            <p>RAG-powered Q&A · Gemini 2.0 Flash · India-specific knowledge base</p>
            <div className="advisor-box">
                <input
                    className="advisor-input"
                    placeholder="e.g. How can I reduce electricity bill in Belagavi?"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && ask()}
                />
                <button className="btn-primary" onClick={ask} disabled={loading}>
                    {loading ? "Asking Gemini..." : "Ask"}
                </button>
                {answer && <div className="advisor-answer">{answer}</div>}
            </div>
        </div>
    );
}

function CommunityPlaceholder() {
    return (
        <div className="placeholder-page">
            <h2>🏆 Community Leaderboard</h2>
            <p>Real-time rankings · Firestore onSnapshot · Belagavi, Karnataka</p>
            <div className="placeholder-note">
                Leaderboard data is live in the Dashboard tab once you sign in and
                complete your first footprint analysis.
            </div>
        </div>
    );
}

function SplashScreen() {
    return (
        <div className="splash">
            <div className="splash-logo">🌿</div>
            <div className="splash-title">Carbon Nexus</div>
            <div className="splash-sub">Loading...</div>
        </div>
    );
}

function GoogleIcon() {
    return (
        <svg width="16" height="16" viewBox="0 0 24 24" style={{ verticalAlign: "-3px", marginRight: 6 }}>
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
        </svg>
    );
}