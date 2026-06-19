/**
 * Firebase Config — Carbon Nexus Frontend
 * Services: Auth, Firestore (real-time listeners), Firebase Hosting
 */

import { initializeApp } from "firebase/app";
import {
    getAuth,
    GoogleAuthProvider,
    signInWithPopup,
    signOut,
    onAuthStateChanged,
} from "firebase/auth";
import {
    getFirestore,
    doc,
    collection,
    onSnapshot,
    setDoc,
    getDoc,
    query,
    orderBy,
    limit,
    where,
} from "firebase/firestore";
import { getAnalytics, logEvent } from "firebase/analytics";

// ─── Firebase App Config ──────────────────────────────────────────────────────
// Values injected at build time via .env.local
const firebaseConfig = {
    apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
    authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
    projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
    storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
    appId: import.meta.env.VITE_FIREBASE_APP_ID,
    measurementId: import.meta.env.VITE_FIREBASE_MEASUREMENT_ID,
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = getFirestore(app);
export const analytics = getAnalytics(app);


// ─── Auth ─────────────────────────────────────────────────────────────────────

const googleProvider = new GoogleAuthProvider();
googleProvider.setCustomParameters({ prompt: "select_account" });

export const signInWithGoogle = async () => {
    try {
        const result = await signInWithPopup(auth, googleProvider);
        logEvent(analytics, "login", { method: "Google" });
        return result.user;
    } catch (error) {
        console.error("Google sign-in error:", error);
        throw error;
    }
};

export const signOutUser = () => signOut(auth);

export const onAuthChange = (callback) => onAuthStateChanged(auth, callback);


// ─── Firestore Real-time Listeners ───────────────────────────────────────────

/**
 * Subscribe to user's monthly CO2 history (real-time)
 * Updates whenever Cloud Run writes new data after analysis
 */
export const subscribeToHistory = (userId, callback) => {
    const historyRef = collection(db, "users", userId, "monthly_history");
    const q = query(historyRef, orderBy("month", "desc"), limit(6));
    return onSnapshot(q, (snapshot) => {
        const history = snapshot.docs.map((d) => d.data());
        callback(history);
    });
};

/**
 * Subscribe to user's actions (real-time completion status)
 */
export const subscribeToActions = (userId, callback) => {
    const actionsRef = collection(db, "users", userId, "actions");
    return onSnapshot(actionsRef, (snapshot) => {
        const actions = {};
        snapshot.docs.forEach((d) => { actions[d.id] = d.data(); });
        callback(actions);
    });
};

/**
 * Subscribe to local leaderboard (real-time)
 */
export const subscribeToLeaderboard = (location, callback) => {
    const lbRef = collection(db, "leaderboard");
    const q = query(
        lbRef,
        where("location", "==", location),
        orderBy("green_score", "desc"),
        limit(10)
    );
    return onSnapshot(q, (snapshot) => {
        const board = snapshot.docs.map((d, i) => ({ rank: i + 1, ...d.data() }));
        callback(board);
    });
};

/**
 * Subscribe to user profile (real-time)
 */
export const subscribeToProfile = (userId, callback) => {
    const userRef = doc(db, "users", userId);
    return onSnapshot(userRef, (snapshot) => {
        callback(snapshot.data() || {});
    });
};


// ─── Firestore Writes ─────────────────────────────────────────────────────────

export const saveUserProfile = async (userId, profileData) => {
    await setDoc(doc(db, "users", userId), profileData, { merge: true });
};

export const getUserProfile = async (userId) => {
    const snap = await getDoc(doc(db, "users", userId));
    return snap.exists() ? snap.data() : null;
};


// ─── Analytics Events ─────────────────────────────────────────────────────────

export const trackEvent = (eventName, params = {}) => {
    logEvent(analytics, eventName, params);
};