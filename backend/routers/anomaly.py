from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
import os, sys, json, math
from datetime import datetime
import pandas as pd
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

router = APIRouter()

UPLOAD_DIR   = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
REPORT_FILE  = "anomaly_report.json"

def get_uid(authorization=None):
    if not authorization or not authorization.startswith("Bearer "):
        return "anonymous"
    return "authenticated-user"

def sanitize_val(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.integer,)): return int(v)
    if isinstance(v, (np.floating,)): return None if math.isnan(float(v)) else float(v)
    return v

def sanitize(obj):
    if isinstance(obj, dict):  return {k: sanitize(v) for k,v in obj.items()}
    if isinstance(obj, list):  return [sanitize(i) for i in obj]
    return sanitize_val(obj)



def run_isolation_forest(df: pd.DataFrame):
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import LabelEncoder

    df = df.copy()
    df["AMOUNT"]  = pd.to_numeric(df["AMOUNT"],  errors="coerce").fillna(0)
    df["BALANCE"] = pd.to_numeric(df["BALANCE"], errors="coerce").fillna(0)

    # Date features
    df["_date"] = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
    df["_dow"]  = df["_date"].dt.dayofweek.fillna(0)
    df["_dom"]  = df["_date"].dt.day.fillna(1)
    df["_month"]= df["_date"].dt.month.fillna(1)

    # Category encoding
    le = LabelEncoder()
    cats = df["category"].fillna("Unknown") if "category" in df.columns else pd.Series(["Unknown"]*len(df))
    df["_cat_enc"] = le.fit_transform(cats.astype(str))

    # Type encoding
    df["_type_enc"] = (df["TYPE"].fillna("DEBIT") == "DEBIT").astype(int)

    features = ["AMOUNT", "_dow", "_dom", "_month", "_cat_enc", "_type_enc"]
    X = df[features].values

    # Train Isolation Forest
    model = IsolationForest(
        n_estimators=100,
        contamination=0.08,   # expect ~8% anomalies
        random_state=42,
        n_jobs=-1
    )
    preds  = model.fit_predict(X)
    scores = model.score_samples(X)   # more negative = more anomalous

    df["_anomaly"]      = preds          # -1 = anomaly, 1 = normal
    df["_anomaly_score"] = scores

    
    cat_stats = df.groupby("_cat_enc")["AMOUNT"].agg(["mean","std"]).to_dict("index")

    anomalies = []
    for idx, row in df[df["_anomaly"] == -1].iterrows():
        amount = float(row["AMOUNT"])
        cat_i  = int(row["_cat_enc"])
        mean   = cat_stats.get(cat_i, {}).get("mean", amount)
        std    = cat_stats.get(cat_i, {}).get("std",  0) or 1
        z      = (amount - mean) / std

        
        score = float(row["_anomaly_score"])
        if score < -0.15:   severity = "CRITICAL"
        elif score < -0.10: severity = "HIGH"
        elif score < -0.05: severity = "MEDIUM"
        else:               severity = "LOW"

        # Reason
        reasons = []
        if z > 2.5:    reasons.append(f"{z:.1f}x above your usual {row.get('category','') or 'spending'} amount")
        if row["_dow"] in [5,6]: reasons.append("Weekend transaction — unusual pattern")
        if amount > df["AMOUNT"].quantile(0.95): reasons.append("Top 5% largest transaction")
        if not reasons: reasons.append("Unusual pattern detected by AI model")

        anomalies.append({
            "index":       int(idx),
            "date":        str(row["DATE"]),
            "transaction": str(row["TRANSACTION"])[:80],
            "merchant":    str(row.get("Merchant",""))[:40],
            "amount":      round(amount, 2),
            "type":        str(row["TYPE"]),
            "category":    str(row.get("category","Unknown")),
            "balance":     round(float(row["BALANCE"]), 2),
            "severity":    severity,
            "score":       round(score, 4),
            "reasons":     reasons,
        })

    # Sort by severity
    sev_order = {"CRITICAL":0,"HIGH":1,"MEDIUM":2,"LOW":3}
    anomalies.sort(key=lambda x: sev_order.get(x["severity"],4))

    return anomalies, float(np.mean(scores)), int((preds == -1).sum())



def run_economist_rules(df: pd.DataFrame, summary: dict) -> dict:
    df = df.copy()
    df["AMOUNT"] = pd.to_numeric(df["AMOUNT"], errors="coerce").fillna(0)

    total_in  = float(summary.get("total_credit", 0)) or 1
    total_out = float(summary.get("total_debit",  0))
    net       = float(summary.get("net_flow",     0))
    balance   = float(summary.get("latest_balance", 0))

    debits  = df[df["TYPE"]=="DEBIT"]
    credits = df[df["TYPE"]=="CREDIT"]

    # Category spend map
    cat_spend = debits.groupby("category")["AMOUNT"].sum().to_dict() if "category" in df.columns else {}

    # ── 50/30/20 Rule ───────────────────────────────
    needs_cats  = ["Utilities & Bills","Rent & Housing","Groceries","Medical & Healthcare","EMI & Loan Repayment","Insurance","Education","Transport"]
    wants_cats  = ["Entertainment","Food & Dining","Shopping","Subscriptions","Travel & Hotels","ATM & Cash"]
    saving_cats = ["Investment & Savings","Salary & Income"]

    needs  = sum(cat_spend.get(c,0) for c in needs_cats)
    wants  = sum(cat_spend.get(c,0) for c in wants_cats)
    saves  = max(net, 0)

    needs_pct  = round(needs  / total_in * 100, 1)
    wants_pct  = round(wants  / total_in * 100, 1)
    saves_pct  = round(saves  / total_in * 100, 1)

    rule_5030 = {
        "needs_pct":  needs_pct,
        "wants_pct":  wants_pct,
        "saves_pct":  saves_pct,
        "needs_ok":   needs_pct  <= 50,
        "wants_ok":   wants_pct  <= 30,
        "saves_ok":   saves_pct  >= 20,
        "status":     "PASS" if (needs_pct<=50 and wants_pct<=30 and saves_pct>=20) else "FAIL",
    }

    
    monthly_expenses = total_out
    months_covered   = round(balance / monthly_expenses, 1) if monthly_expenses > 0 else 0
    emergency_fund   = {
        "months_covered": months_covered,
        "target_months":  6,
        "status": "GOOD" if months_covered >= 6 else ("FAIR" if months_covered >= 3 else "POOR"),
        "message": f"Your balance covers {months_covered} months of expenses. Target: 6 months."
    }

    
    savings_rate = round(net / total_in * 100, 1) if total_in > 0 else 0
    savings_rule = {
        "rate": savings_rate,
        "status": "EXCELLENT" if savings_rate>=30 else ("GOOD" if savings_rate>=20 else ("FAIR" if savings_rate>=10 else "POOR")),
        "message": f"You saved {savings_rate}% of income. Target: ≥20%."
    }

    
    df["_date"] = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
    df["_month"] = df["_date"].dt.to_period("M").astype(str)
    monthly_spend = debits.copy()
    monthly_spend["_month"] = df.loc[debits.index, "_month"] if not debits.empty else pd.Series()
    vol_data = monthly_spend.groupby("_month")["AMOUNT"].sum() if not monthly_spend.empty else pd.Series()
    volatility = round(float(vol_data.std() / vol_data.mean() * 100), 1) if len(vol_data) > 1 and vol_data.mean() > 0 else 0
    volatility_rule = {
        "cv_percent": volatility,
        "status": "STABLE" if volatility < 20 else ("MODERATE" if volatility < 40 else "VOLATILE"),
        "message": f"Your monthly spending varies by {volatility}%. {'Stable pattern.' if volatility<20 else 'High variation detected.'}"
    }

    
    if cat_spend:
        top_cat = max(cat_spend, key=cat_spend.get)
        top_pct = round(cat_spend[top_cat] / total_out * 100, 1) if total_out > 0 else 0
        concentration = {
            "top_category": top_cat,
            "percent": top_pct,
            "status": "OK" if top_pct < 40 else "HIGH",
            "message": f"{top_pct}% of spending in '{top_cat}'. {'Diversified.' if top_pct<40 else 'High concentration risk.'}"
        }
    else:
        concentration = {"top_category":"N/A","percent":0,"status":"OK","message":"No data"}

    
    upi_spend = cat_spend.get("UPI Person Transfer", 0)
    upi_pct   = round(upi_spend / total_out * 100, 1) if total_out > 0 else 0
    upi_rule  = {
        "amount": round(upi_spend, 2),
        "percent": upi_pct,
        "status": "OK" if upi_pct < 25 else "REVIEW",
        "message": f"₹{upi_spend:,.0f} ({upi_pct}%) sent via UPI P2P. {'Normal.' if upi_pct<25 else 'High — review recipients.'}"
    }

    
    txn_counts = debits.groupby("Merchant")["AMOUNT"].count() if not debits.empty else pd.Series()
    recurring  = txn_counts[txn_counts >= 2].index.tolist()[:8] if not txn_counts.empty else []

    return {
        "rule_50_30_20":   rule_5030,
        "emergency_fund":  emergency_fund,
        "savings_rate":    savings_rule,
        "volatility":      volatility_rule,
        "concentration":   concentration,
        "upi_risk":        upi_rule,
        "recurring":       recurring,
    }



def compute_health_score(rules: dict, anomaly_count: int, total_txns: int) -> dict:
    score = 50
    grades = []

    sr = rules["savings_rate"]["rate"]
    if sr >= 30:   score += 20; grades.append(("Savings Rate", "A", "+20"))
    elif sr >= 20: score += 10; grades.append(("Savings Rate", "B", "+10"))
    elif sr >= 10: score +=  5; grades.append(("Savings Rate", "C", "+5"))
    else:          score -= 10; grades.append(("Savings Rate", "D", "-10"))

    ef = rules["emergency_fund"]["months_covered"]
    if ef >= 6:   score += 15; grades.append(("Emergency Fund", "A", "+15"))
    elif ef >= 3: score +=  7; grades.append(("Emergency Fund", "B", "+7"))
    else:         score -=  5; grades.append(("Emergency Fund", "C", "-5"))

    if rules["rule_50_30_20"]["status"] == "PASS":
        score += 10; grades.append(("50/30/20 Rule", "A", "+10"))
    else:
        score -=  5; grades.append(("50/30/20 Rule", "C", "-5"))

    vol = rules["volatility"]["cv_percent"]
    if vol < 20:   score += 10; grades.append(("Spending Stability", "A", "+10"))
    elif vol < 40: score +=  5; grades.append(("Spending Stability", "B", "+5"))
    else:          score -=  5; grades.append(("Spending Stability", "D", "-5"))

    anom_rate = (anomaly_count / max(total_txns, 1)) * 100
    if anom_rate < 3:    score +=  5; grades.append(("Anomaly Risk", "A", "+5"))
    elif anom_rate < 8:  score +=  0; grades.append(("Anomaly Risk", "B", "+0"))
    else:                score -= 10; grades.append(("Anomaly Risk", "D", "-10"))

    score = max(10, min(100, score))

    if score >= 80:   label, color = "EXCELLENT", "#00e5a0"
    elif score >= 65: label, color = "GOOD",      "#3b9eff"
    elif score >= 45: label, color = "FAIR",      "#f59e0b"
    else:             label, color = "POOR",      "#ff4d6d"

    recs = []
    if sr < 20:   recs.append("Increase monthly savings to at least 20% of income")
    if ef < 6:    recs.append(f"Build emergency fund to cover 6 months ({6-ef:.0f} more months needed)")
    if rules["rule_50_30_20"]["wants_pct"] > 30: recs.append("Reduce discretionary spending (entertainment, dining, shopping)")
    if rules["concentration"]["status"] == "HIGH": recs.append(f"Diversify spending — {rules['concentration']['top_category']} is {rules['concentration']['percent']}% of total")
    if rules["upi_risk"]["status"] == "REVIEW": recs.append("Review UPI person transfers — unusually high volume")
    if anom_rate > 8: recs.append("Multiple anomalous transactions detected — review flagged items")
    if not recs: recs.append("Your finances are in great shape — maintain current habits!")

    return {"score": score, "label": label, "color": color, "grades": grades, "recommendations": recs}



@router.post("/anomaly/detect")
async def detect_anomalies(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    master_path = os.path.join(UPLOAD_DIR, uid, "transactions_master.xlsx")

    if not os.path.exists(master_path):
        raise HTTPException(404, "No transactions found — upload a statement first")

    df = pd.read_excel(master_path, dtype=str)
    df = df.fillna("")

    if len(df) < 10:
        raise HTTPException(400, "Need at least 10 transactions for anomaly detection")

    df["AMOUNT"]  = pd.to_numeric(df["AMOUNT"],  errors="coerce").fillna(0)
    df["BALANCE"] = pd.to_numeric(df["BALANCE"], errors="coerce").fillna(0)


    debits  = df[df["TYPE"]=="DEBIT"]
    credits = df[df["TYPE"]=="CREDIT"]
    net     = float(credits["AMOUNT"].sum()) - float(debits["AMOUNT"].sum())
    summary = {
        "total_credit":   float(credits["AMOUNT"].sum()),
        "total_debit":    float(debits["AMOUNT"].sum()),
        "net_flow":       net,
        "latest_balance": float(df["BALANCE"].iloc[0]) if len(df) > 0 else 0,
        "total_transactions": len(df),
    }

    
    anomalies, avg_score, anom_count = run_isolation_forest(df)
    rules   = run_economist_rules(df, summary)
    health  = compute_health_score(rules, anom_count, len(df))

    report = {
        "generated_at":   datetime.utcnow().isoformat(),
        "total_txns":     len(df),
        "summary":        summary,
        "anomalies":      anomalies,
        "anomaly_count":  anom_count,
        "anomaly_rate":   round(anom_count / max(len(df),1) * 100, 1),
        "avg_if_score":   round(avg_score, 4),
        "rules":          rules,
        "health":         health,
    }

    report_path = os.path.join(UPLOAD_DIR, uid, REPORT_FILE)
    with open(report_path, "w") as f:
        json.dump(sanitize(report), f, indent=2, default=str)

    return sanitize(report)


@router.get("/anomaly/report")
async def get_report(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    report_path = os.path.join(UPLOAD_DIR, uid, REPORT_FILE)
    if not os.path.exists(report_path):
        raise HTTPException(404, "No report yet — run detection first")
    with open(report_path) as f:
        return json.load(f)
