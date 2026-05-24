from fastapi import APIRouter, UploadFile, File, HTTPException, Header
from fastapi.responses import JSONResponse, FileResponse
import os, shutil, uuid, math
from datetime import datetime
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from utils.excel_parser import parse_excel
from utils.pdf_parser import parse_pdf
from utils.save_transactions import append_transactions, get_all_transactions

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_uid(authorization: str = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "anonymous"
    return "authenticated-user"


def save_upload(file: UploadFile, uid: str):
    user_dir = os.path.join(UPLOAD_DIR, uid)
    os.makedirs(user_dir, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower()
    unique_name = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    dest = os.path.join(user_dir, unique_name)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest, unique_name


def sanitize(records: list) -> list:
    """Replace all NaN / Inf float values with None so JSON never crashes."""
    def clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return [{k: clean(v) for k, v in row.items()} for row in records]



@router.post("/upload/excel")
async def upload_excel(
    file: UploadFile = File(...),
    authorization: str = Header(None),
):
    if not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "Only .xlsx / .xls / .csv accepted")

    uid = get_uid(authorization)
    dest, unique_name = save_upload(file, uid)

    try:
        df = parse_excel(dest)
        if df.empty:
            return JSONResponse({"status": "parsed_empty", "filename": unique_name,
                                 "message": "No transactions found in this file"})

        stats   = append_transactions(df, uid, UPLOAD_DIR, source_file=file.filename)
        preview = sanitize(df.head(5).to_dict(orient="records"))

        return {
            "status":             "success",
            "filename":           unique_name,
            "rows_in_file":       len(df),
            "new_added":          stats["new_added"],
            "duplicates_skipped": stats["duplicates_skipped"],
            "total_transactions": stats["total_transactions"],
            "master_file":        stats["master_file"],
            "preview":            preview,
        }
    except Exception as e:
        return JSONResponse({"status": "error", "filename": unique_name, "error": str(e)})



@router.post("/upload/pdf")
async def upload_pdf(
    file: UploadFile = File(...),
    authorization: str = Header(None),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF accepted")

    uid = get_uid(authorization)
    dest, unique_name = save_upload(file, uid)

    try:
        df = parse_pdf(dest)
        if df.empty:
            return JSONResponse({"status": "parsed_empty", "filename": unique_name,
                                 "message": "No transactions found in this PDF"})

        stats   = append_transactions(df, uid, UPLOAD_DIR, source_file=file.filename)
        preview = sanitize(df.head(5).to_dict(orient="records"))

        return {
            "status":             "success",
            "filename":           unique_name,
            "rows_in_file":       len(df),
            "new_added":          stats["new_added"],
            "duplicates_skipped": stats["duplicates_skipped"],
            "total_transactions": stats["total_transactions"],
            "master_file":        stats["master_file"],
            "preview":            preview,
        }
    except Exception as e:
        return JSONResponse({"status": "error", "filename": unique_name, "error": str(e)})



@router.get("/transactions")
async def get_transactions(authorization: str = Header(None)):
    uid = get_uid(authorization)
    df  = get_all_transactions(uid, UPLOAD_DIR)
    df  = df.where(df.notna(), other=None)
    records = sanitize(df.to_dict(orient="records"))
    return {
        "total":   len(records),
        "columns": list(df.columns),
        "data":    records,
    }



@router.get("/transactions/download")
async def download_transactions(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    master_path = os.path.join(UPLOAD_DIR, uid, "transactions_master.xlsx")
    if not os.path.exists(master_path):
        raise HTTPException(404, "No transactions saved yet — upload a statement first")
    return FileResponse(
        master_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="transactions_master.xlsx"
    )



@router.get("/uploads")
async def list_uploads(authorization: str = Header(None)):
    uid      = get_uid(authorization)
    user_dir = os.path.join(UPLOAD_DIR, uid)
    if not os.path.exists(user_dir):
        return {"files": []}
    files = []
    for f in sorted(os.listdir(user_dir), reverse=True):
        if f == "transactions_master.xlsx":
            continue
        stat = os.stat(os.path.join(user_dir, f))
        files.append({
            "name":        f,
            "size_kb":     round(stat.st_size / 1024, 1),
            "uploaded_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
        })
    return {"files": files}