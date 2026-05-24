from fastapi import APIRouter, Header, HTTPException
import os, sys, json
import pandas as pd
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

router = APIRouter()

UPLOAD_DIR  = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
MODEL_DIR   = r"C:\SREY2K26\finsight_model"   # ← your trained model path
MASTER_FILE = "transactions_master.xlsx"


_model     = None
_tokenizer = None
_id2label  = None

def load_model():
    global _model, _tokenizer, _id2label
    if _model is not None:
        return _model, _tokenizer, _id2label

    import torch
    from transformers import DistilBertTokenizerFast, DistilBertForSequenceClassification

    print(f"Loading DistilBert model from {MODEL_DIR} ...")
    _tokenizer = DistilBertTokenizerFast.from_pretrained(MODEL_DIR)
    _model     = DistilBertForSequenceClassification.from_pretrained(MODEL_DIR)
    _model.eval()

    
    label_map_path = os.path.join(MODEL_DIR, "label_map.json")
    if os.path.exists(label_map_path):
        with open(label_map_path) as f:
            lmap = json.load(f)
        _id2label = {int(k): v for k, v in lmap["id2label"].items()}
    else:
        _id2label = _model.config.id2label

    print("Model loaded ✓")
    return _model, _tokenizer, _id2label


def get_uid(authorization: str = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "anonymous"
    return "authenticated-user"


def get_master_path(uid: str) -> str:
    return os.path.join(UPLOAD_DIR, uid, MASTER_FILE)



def predict_categories(particulars: list, merchants: list, batch_size: int = 32) -> list:
    import torch
    model, tokenizer, id2label = load_model()

    all_preds = []
    for i in range(0, len(particulars), batch_size):
        batch_p = particulars[i:i+batch_size]
        batch_m = merchants[i:i+batch_size]

        enc = tokenizer(
            batch_p,
            batch_m,
            truncation=True,
            padding=True,
            max_length=96,
            return_tensors="pt",
        )

        with torch.no_grad():
            outputs = model(**enc)
            preds   = torch.argmax(outputs.logits, dim=1).numpy()

        all_preds.extend([id2label[int(p)] for p in preds])

    return all_preds



@router.post("/analyze")
async def run_analysis(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    master_path = get_master_path(uid)

    if not os.path.exists(master_path):
        raise HTTPException(404, "No transactions found — upload a bank statement first")

    df = pd.read_excel(master_path, dtype=str)

    if df.empty:
        raise HTTPException(400, "Transactions file is empty — upload a statement first")

    # Map columns to model inputs
    if "TRANSACTION" not in df.columns:
        raise HTTPException(400, "Missing TRANSACTION column in master file")

    particulars = df["TRANSACTION"].fillna("").tolist()
    merchants   = df["Merchant"].fillna("").tolist() if "Merchant" in df.columns else [""] * len(df)

    print(f"Running model on {len(df)} transactions...")
    categories = predict_categories(particulars, merchants)

    df["category"] = categories

    # Convert AMOUNT and BALANCE to numeric
    df["AMOUNT"]  = pd.to_numeric(df["AMOUNT"],  errors="coerce").fillna(0)
    df["BALANCE"] = pd.to_numeric(df["BALANCE"], errors="coerce").fillna(0)

    # Save back with category filled
    df.to_excel(master_path, index=False)
    print("Categories saved ✓")

    return {
        "status":       "success",
        "total":        len(df),
        "categorised":  len(df),
        "message":      "Analysis complete",
    }



@router.get("/analysis/data")
async def get_analysis_data(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    master_path = get_master_path(uid)

    if not os.path.exists(master_path):
        raise HTTPException(404, "No data found")

    df = pd.read_excel(master_path, dtype=str)

    if df.empty or "category" not in df.columns:
        raise HTTPException(400, "Run analysis first")

    df["AMOUNT"]  = pd.to_numeric(df["AMOUNT"],  errors="coerce").fillna(0)
    df["BALANCE"] = pd.to_numeric(df["BALANCE"], errors="coerce").fillna(0)
    df["TYPE"]    = df["TYPE"].fillna("DEBIT")## isko change krna hai########################

    total_transactions = len(df)
    debits  = df[df["TYPE"] == "DEBIT"]
    credits = df[df["TYPE"] == "CREDIT"]

    total_debit  = float(debits["AMOUNT"].sum())
    total_credit = float(credits["AMOUNT"].sum())
    net_flow     = total_credit - total_debit

    # Latest balance
    latest_balance = float(df["BALANCE"].iloc[0]) if len(df) > 0 else 0.0


    debit_by_cat = (
        debits.groupby("category")["AMOUNT"]
        .sum()
        .sort_values(ascending=False)
    )
    total_debit_nonzero = debit_by_cat.sum() or 1

    category_breakdown = [
        {
            "category": cat,
            "amount":   round(float(amt), 2),
            "percent":  round(float(amt) / total_debit_nonzero * 100, 1),
            "count":    int((debits["category"] == cat).sum()),
        }
        for cat, amt in debit_by_cat.items()
    ]

    # ── Monthly trend ─────────────────────────────────────────────────────────
    df["_date"] = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
    df["_month"] = df["_date"].dt.to_period("M").astype(str)

    monthly = []
    for month, grp in df.groupby("_month"):
        if month == "NaT":
            continue
        monthly.append({
            "month":  month,
            "debit":  round(float(grp[grp["TYPE"]=="DEBIT"]["AMOUNT"].sum()), 2),
            "credit": round(float(grp[grp["TYPE"]=="CREDIT"]["AMOUNT"].sum()), 2),
        })
    monthly.sort(key=lambda x: x["month"])

    
    top_merchants = (
        debits.groupby("Merchant")["AMOUNT"]
        .sum()
        .sort_values(ascending=False)
        .head(8)
    )
    top_merchants_list = [
        {"merchant": m, "amount": round(float(a), 2)}
        for m, a in top_merchants.items()
        if m and m != "nan"
    ]

    
    credit_by_cat = (
        credits.groupby("category")["AMOUNT"]
        .sum()
        .sort_values(ascending=False)
    )
    credit_breakdown = [
        {"category": cat, "amount": round(float(amt), 2)}
        for cat, amt in credit_by_cat.items()
    ]

    return {
        "summary": {
            "total_transactions": total_transactions,
            "total_debit":        round(total_debit, 2),
            "total_credit":       round(total_credit, 2),
            "net_flow":           round(net_flow, 2),
            "latest_balance":     round(latest_balance, 2),
        },
        "category_breakdown": category_breakdown,
        "credit_breakdown":   credit_breakdown,
        "monthly_trend":      monthly,
        "top_merchants":      top_merchants_list,
        "transactions":       df.drop(columns=["_date","_month"], errors="ignore").to_dict(orient="records"),
    }



@router.post("/clear-data")
async def clear_data(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    master_path = get_master_path(uid)

    if not os.path.exists(master_path):
        raise HTTPException(404, "No data to clear")

    
    empty = pd.DataFrame(columns=[
        "DATE","TRANSACTION","AMOUNT","TYPE","BALANCE",
        "Merchant","source_file","uploaded_at","category"
    ])
    empty.to_excel(master_path, index=False)

    return {"status": "cleared", "message": "All transactions cleared"}
