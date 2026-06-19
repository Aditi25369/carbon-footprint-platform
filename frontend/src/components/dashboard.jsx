/**
 * Dashboard.jsx — Main carbon dashboard
 * Real-time data via Firestore onSnapshot listeners
 * Charts: Recharts (lightweight, no Maps API key needed client-side)
 */

import { useState, useEffect, useCallback } from "react";
import {
    AreaChart, Area, XAxis, YAxis, Tooltip,
    PieChart, Pie, Cell, ResponsiveContainer
} from "recharts";
import {
    subscribeToHistory,
    subscribeToProfile,
    subscribeToLeaderboard,
    trackEvent,
} from "../lib/firebase";
import { analyzeFootprint } from "../lib/gemini";

// ─── Constants ────────────────────────────────────────────────────────────────

const CATEGORY_COLORS = {
    transport: "#2D8EFF",
    home: "#F59E0B",
    food: "#10B981",
    shopping: "#8B5CF6",
};

const INDIA_MONTHLY_AVG = 0.545;   // tonnes CO2e/month
const GLOBAL_MONTHLY_AVG = 0.833;


// ─── Component ────────────────────────────────────────────────────────────────

export default function Dashboard({ user }) {
    const [profile, setProfile] = useState(null);
    const [history, setHistory] = useState([]);
    const [leaderboard, setLeaderboard] = useState([]);
    const [loading, setLoading] = useState(true);
    const [currentMonth, setCurrentMonth] = useState(null);

    // ─── Real-time Firestore subscriptions ──────────────────────────────────
    useEffect(() => {
        if (!user?.uid) return;

        const unsubProfile = subscribeToProfile(user.uid, (data) => {
            setProfile(data);
            setLoading(false);
        });

        const unsubHistory = subscribeToHistory(user.uid, (data) => {
            const sorted = [...data].sort((a, b) => a.month.localeCompare(b.month));
            setHistory(sorted);
            if (sorted.length > 0) setCurrentMonth(sorted[sorted.length - 1]);
        });

        const location = user?.location || "Belagavi";
        const unsubLb = subscribeToLeaderboard(location, setLeaderboard);

        return () => {
            unsubProfile();
            unsubHistory();
            unsubLb();
        };
    }, [user?.uid]);

    // ─── Derived metrics ──────────────────────────────────────────────────────
    const co2 = currentMonth?.total_co2 ?? 0;
    const greenScore = profile?.green_score ?? 0;
    const vsIndia = ((co2 - INDIA_MONTHLY_AVG) / INDIA_MONTHLY_AVG * 100).toFixed(0);
    const treesEquiv = Math.round(co2 * 40);   // ~40 trees absorb 1t CO2/year

    const trendData = history.map((h) => ({
        month: h.month?.slice(5),   // "2025-01" → "01"
        co2: parseFloat(h.total_co2?.toFixed(2) ?? 0),
    }));

    const pieData = [
        { name: "Transport", value: currentMonth?.transport_co2 ?? 1.8, color: CATEGORY_COLORS.transport },
        { name: "Home", value: currentMonth?.home_co2 ?? 1.2, color: CATEGORY_COLORS.home },
        { name: "Food", value: currentMonth?.food_co2 ?? 0.8, color: CATEGORY_COLORS.food },
        { name: "Shopping", value: currentMonth?.shopping_co2 ?? 0.4, color: CATEGORY_COLORS.shopping },
    ];

    // User's rank in leaderboard
    const userRank = leaderboard.findIndex((u) => u.user_id === user?.uid) + 1;

    if (loading) return <DashboardSkeleton />;

    return (
        <div className="dashboard">

            {/* ── Metric cards ────────────────────────────────────────────────── */}
            <div className="metrics-grid">
                <MetricCard
                    label="This month"
                    value={`${co2.toFixed(1)}t`}
                    sub={`${vsIndia > 0 ? "+" : ""}${vsIndia}% vs India avg`}
                    trend={vsIndia < 0 ? "down" : "up"}
                />
                <MetricCard
                    label="Green score"
                    value={`${greenScore}/100`}
                    sub={`+${profile?.score_delta ?? 0} this month`}
                    trend="up"
                />
                <MetricCard
                    label="Rank (local)"
                    value={userRank > 0 ? `#${userRank}` : "—"}
                    sub={`of ${leaderboard.length} in ${profile?.location ?? "city"}`}
                    trend="neutral"
                />
                <MetricCard
                    label="Trees equiv."
                    value={`${treesEquiv} 🌱`}
                    sub="saved this year"
                    trend="up"
                />
            </div>

            {/* ── Trend chart ─────────────────────────────────────────────────── */}
            <div className="card">
                <h3 className="card-title">6-month CO₂ trend</h3>
                {trendData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={140}>
                        <AreaChart data={trendData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                            <defs>
                                <linearGradient id="co2Gradient" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#10B981" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
                            <Tooltip
                                formatter={(v) => [`${v}t CO₂`, "Monthly"]}
                                contentStyle={{ fontSize: 12, borderRadius: 6 }}
                            />
                            <Area
                                type="monotone"
                                dataKey="co2"
                                stroke="#10B981"
                                strokeWidth={2}
                                fill="url(#co2Gradient)"
                            />
                        </AreaChart>
                    </ResponsiveContainer>
                ) : (
                    <EmptyState message="Complete your first footprint analysis to see trends." />
                )}
            </div>

            {/* ── Pie breakdown ───────────────────────────────────────────────── */}
            <div className="card">
                <h3 className="card-title">Footprint breakdown</h3>
                <div className="pie-row">
                    <ResponsiveContainer width={120} height={120}>
                        <PieChart>
                            <Pie
                                data={pieData}
                                cx="50%"
                                cy="50%"
                                innerRadius={35}
                                outerRadius={55}
                                paddingAngle={2}
                                dataKey="value"
                            >
                                {pieData.map((entry, i) => (
                                    <Cell key={i} fill={entry.color} />
                                ))}
                            </Pie>
                        </PieChart>
                    </ResponsiveContainer>
                    <div className="legend">
                        {pieData.map((item) => (
                            <div key={item.name} className="legend-row">
                                <span className="legend-dot" style={{ background: item.color }} />
                                <span className="legend-name">{item.name}</span>
                                <span className="legend-val">{item.value.toFixed(1)}t</span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* ── Leaderboard ─────────────────────────────────────────────────── */}
            <div className="card">
                <h3 className="card-title">
                    {profile?.location ?? "Local"} leaderboard
                    <span className="card-sub"> · this month</span>
                </h3>
                {leaderboard.length > 0 ? (
                    <div className="lb-list">
                        {leaderboard.slice(0, 5).map((entry) => (
                            <LeaderboardRow
                                key={entry.user_id}
                                entry={entry}
                                isYou={entry.user_id === user?.uid}
                            />
                        ))}
                    </div>
                ) : (
                    <EmptyState message="Be the first in your city to track your carbon footprint!" />
                )}
            </div>

        </div>
    );
}


// ─── Sub-components ───────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, trend }) {
    const trendColors = { down: "#10B981", up: "#EF4444", neutral: "#6B7280" };
    return (
        <div className="metric-card">
            <div className="metric-label">{label}</div>
            <div className="metric-value">{value}</div>
            <div className="metric-sub" style={{ color: trendColors[trend] }}>{sub}</div>
        </div>
    );
}

function LeaderboardRow({ entry, isYou }) {
    return (
        <div className={`lb-row ${isYou ? "lb-row--you" : ""}`}>
            <span className="lb-rank">#{entry.rank}</span>
            <span className="lb-avatar">{entry.display_name?.slice(0, 2).toUpperCase()}</span>
            <span className="lb-name">
                {entry.display_name}
                {isYou && <span className="lb-you-badge"> (you)</span>}
            </span>
            <span className="lb-score">{entry.green_score} pts</span>
            <span className="lb-co2">{entry.monthly_co2?.toFixed(1)}t</span>
        </div>
    );
}

function EmptyState({ message }) {
    return (
        <div className="empty-state">
            <span className="empty-icon">🌱</span>
            <p>{message}</p>
        </div>
    );
}

function DashboardSkeleton() {
    return (
        <div className="skeleton-wrapper">
            {[...Array(4)].map((_, i) => (
                <div key={i} className="skeleton-card" />
            ))}
        </div>
    );
}