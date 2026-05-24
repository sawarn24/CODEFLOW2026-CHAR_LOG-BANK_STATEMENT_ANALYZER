from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import firebase_admin
from firebase_admin import credentials, auth
import os
from dotenv import load_dotenv
 
load_dotenv()
 
from backend.routers import upload, analyze, anomaly, ai_insights
from backend.routers.report_generator import router as report_router
 
app = FastAPI(title="Banklytics API", version="1.0.0")
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 

_firebase_ready = False
 
_private_key = os.getenv("private_key", "").strip().strip('"').replace("\\n", "\n")
 
if _private_key:
    firebase_admin.initialize_app(credentials.Certificate({
        "type":                        os.getenv("type"),
        "project_id":                  os.getenv("project_id"),
        "private_key_id":              os.getenv("private_key_id"),
        "private_key":                 _private_key,
        "client_email":                os.getenv("client_email"),
        "client_id":                   os.getenv("client_id"),
        "auth_uri":                    os.getenv("auth_uri"),
        "token_uri":                   os.getenv("token_uri"),
        "auth_provider_x509_cert_url": os.getenv("auth_provider_x509_cert_url"),
        "client_x509_cert_url":        os.getenv("client_x509_cert_url"),
        "universe_domain":             os.getenv("universe_domain", "googleapis.com"),
    }))
    _firebase_ready = True
    print("✅ Firebase initialised from env")
else:
    print("⚠️  Firebase env vars not set – running in dev mode")
 
# ── Static & pages ────────────────────────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
 
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
 
app.include_router(upload.router,      prefix="/api")
app.include_router(analyze.router,     prefix="/api")
app.include_router(anomaly.router,     prefix="/api")
app.include_router(ai_insights.router, prefix="/api")
app.include_router(report_router,      prefix="/api")
 
# ── Pages ─────────────────────────────────────────────────────────────────────
def page(name):
    return FileResponse(os.path.join(FRONTEND_DIR, "pages", name))
 
@app.get("/")            
def serve_login():        return page("login.html")
 
@app.get("/register")    
def serve_register():     return page("register.html")
 
@app.get("/home")        
def serve_home():         return page("home.html")
 
@app.get("/analysis")    
def serve_analysis():     return page("analysis.html")
 
@app.get("/ai-insights") 
def serve_ai_insights():  return page("ai_insights.html")
 
@app.get("/anomaly")     
def serve_anomaly():      return page("anomaly.html")
 
# ── Token verification ────────────────────────────────────────────────────────
@app.post("/api/verify-token")
async def verify_token(payload: dict):
    id_token = payload.get("idToken")
    if not id_token:
        raise HTTPException(status_code=400, detail="idToken required")
    if not _firebase_ready:
        return {"uid": "dev-user", "email": "dev@example.com", "name": "Dev User"}
    try:
        decoded = auth.verify_id_token(id_token)
        return {"uid": decoded["uid"], "email": decoded.get("email", ""), "name": decoded.get("name", "")}
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
 
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
