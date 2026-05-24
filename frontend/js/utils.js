
const firebaseConfig = {
  apiKey: "AIzaSyDY8hHATT6e13VtQ8YcvSJ8USAYZlQEsmw",
  authDomain: "banklytics.firebaseapp.com",
  projectId: "banklytics",
  storageBucket: "banklytics.firebasestorage.app",
  messagingSenderId: "642438931095",
  appId: "1:642438931095:web:53c558d0462d68fbc37fd2",
  measurementId: "G-8BBT09LE5W"
};

const API_BASE = window.location.origin; // FastAPI runs on same origin


let _firebaseApp, _auth;

function initFirebase() {
  if (_firebaseApp) return;
  _firebaseApp = firebase.initializeApp(firebaseConfig);
  _auth = firebase.auth();
}

function getAuth() {
  initFirebase();
  return _auth;
}


async function getCurrentUser() {
  return new Promise((resolve) => {
    const unsubscribe = getAuth().onAuthStateChanged((user) => {
      unsubscribe();
      resolve(user);
    });
  });
}

async function getIdToken() {
  const user = await getCurrentUser();
  if (!user) return null;
  return user.getIdToken();
}

async function requireAuth() {
  const user = await getCurrentUser();
  if (!user) {
    window.location.href = "/";
    return null;
  }
  return user;
}

async function requireGuest() {
  const user = await getCurrentUser();
  if (user) {
    window.location.href = "/home";
  }
}

async function signOut() {
  await getAuth().signOut();
  localStorage.removeItem("bl_user");
  window.location.href = "/";
}


async function apiFetch(path, options = {}) {
  const token = await getIdToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

async function apiUpload(path, formData) {
  const token = await getIdToken();
  const headers = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

// ─── Toast ─────────────────────────────────────────────────────────────────
function ensureToastContainer() {
  let c = document.getElementById("toast-container");
  if (!c) {
    c = document.createElement("div");
    c.id = "toast-container";
    document.body.appendChild(c);
  }
  return c;
}

function toast(message, type = "info", duration = 3500) {
  const icons = { success: "✓", error: "✕", info: "ℹ" };
  const container = ensureToastContainer();
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${icons[type] || "•"}</span><span>${message}</span>`;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add("fadeOut");
    setTimeout(() => el.remove(), 350);
  }, duration);
}


function firebaseErrorMsg(code) {
  const map = {
    "auth/email-already-in-use":    "This email is already registered.",
    "auth/invalid-email":           "Please enter a valid email address.",
    "auth/weak-password":           "Password must be at least 6 characters.",
    "auth/user-not-found":          "No account found with this email.",
    "auth/wrong-password":          "Incorrect password. Please try again.",
    "auth/too-many-requests":       "Too many attempts. Please wait a moment.",
    "auth/network-request-failed":  "Network error. Check your connection.",
    "auth/popup-closed-by-user":    "Sign-in popup was closed.",
  };
  return map[code] || "Something went wrong. Please try again.";
}


function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}