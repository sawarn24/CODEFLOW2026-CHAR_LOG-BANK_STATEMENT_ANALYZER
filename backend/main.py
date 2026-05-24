from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import firebase_admin
from firebase_admin import credentials, auth
import os

from routers import upload
from routers import analyze
from routers import anomaly
from routers import ai_insights

app = FastAPI(title="Banklytics API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Firebase ──────────────────────────────────────────────────────────────────
SERVICE_ACCOUNT_PATH = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
if os.path.exists(SERVICE_ACCOUNT_PATH):
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)
else:
    print("⚠️  serviceAccountKey.json not found – Firebase verification disabled in dev mode")

# ── Static & pages ────────────────────────────────────────────────────────────
BASE_DIR     = r"C:\SREY2K26"
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

app.include_router(upload.router,  prefix="/api")
app.include_router(analyze.router, prefix="/api")
app.include_router(anomaly.router, prefix="/api")
app.include_router(ai_insights.router, prefix="/api")

@app.get("/")
def serve_login():
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", "login.html"))

@app.get("/register")
def serve_register():
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", "register.html"))

@app.get("/home")
def serve_home():
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", "home.html"))

@app.get("/analysis")
def serve_analysis():
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", "analysis.html"))

@app.get("/ai_insights")
def serve_ai_insights():
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", "ai_insights.html"))

@app.get("/anomaly")
def serve_anomaly():
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", "anomaly.html"))

# In your main.py / app setup:
from routers.report_generator import router as report_router
app.include_router(report_router, prefix="/api")

@app.get("/ai-insights")
def serve_ai_insights():
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", "ai_insights.html"))

@app.post("/api/verify-token")
async def verify_token(payload: dict):
    id_token = payload.get("idToken")
    if not id_token:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="idToken required")
    try:
        if not firebase_admin._apps:
            return {"uid": "dev-user", "email": "dev@example.com", "name": "Dev User"}
        decoded = auth.verify_id_token(id_token)
        return {"uid": decoded["uid"], "email": decoded.get("email",""), "name": decoded.get("name","")}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)