"""
ai_insights.py
POST /api/ai/chat  → send message to Groq with full financial context injected
GET  /api/ai/context → returns the financial summary to seed the chat
"""

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
import os, sys, json, math
import pandas as pd
import numpy as np
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException
import os, sys, json, math
import pandas as pd
import numpy as np
from datetime import datetime
from groq import Groq

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

router       = APIRouter()
UPLOAD_DIR   = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
GROQ_API_KEY = os.getenv("groq_api_key")
GROQ_MODEL   = "openai/gpt-oss-120b"


def get_uid(authorization=None):
    if not authorization or not authorization.startswith("Bearer "):
        return "anonymous"
    return "authenticated-user"


def sanitize_val(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.integer,)):  return int(v)
    if isinstance(v, (np.floating,)): return None if math.isnan(float(v)) else round(float(v), 2)
    return v


def build_financial_context(uid: str) -> dict:
    """Build a rich financial context dict from all available data."""
    master_path = os.path.join(UPLOAD_DIR, uid, "transactions_master.xlsx")
    report_path = os.path.join(UPLOAD_DIR, uid, "anomaly_report.json")
    context     = {}

    # ── Transactions ──────────────────────────────────────────────────────────
    if os.path.exists(master_path):
        df = pd.read_excel(master_path, dtype=str)
        df["AMOUNT"]  = pd.to_numeric(df["AMOUNT"],  errors="coerce").fillna(0)
        df["BALANCE"] = pd.to_numeric(df["BALANCE"], errors="coerce").fillna(0)

        debits  = df[df["TYPE"] == "DEBIT"]
        credits = df[df["TYPE"] == "CREDIT"]

        total_debit  = float(debits["AMOUNT"].sum())
        total_credit = float(credits["AMOUNT"].sum())
        net_flow     = total_credit - total_debit

        # Category breakdown
        cat_spend = {}
        if "category" in df.columns:
            cat_spend = debits.groupby("category")["AMOUNT"].sum().round(2).to_dict()

        # Monthly trend
        df["_date"]  = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
        df["_month"] = df["_date"].dt.to_period("M").astype(str)
        monthly = {}
        for m, grp in df.groupby("_month"):
            if m == "NaT":
                continue
            monthly[m] = {
                "debit":  round(float(grp[grp["TYPE"] == "DEBIT"]["AMOUNT"].sum()),  2),
                "credit": round(float(grp[grp["TYPE"] == "CREDIT"]["AMOUNT"].sum()), 2),
            }

        # Top merchants
        top_merch = (
            debits.groupby("Merchant")["AMOUNT"]
            .sum().sort_values(ascending=False)
            .head(10).round(2).to_dict()
        ) if not debits.empty else {}

        # Recurring merchants
        recurring = debits.groupby("Merchant")["AMOUNT"].count() if not debits.empty else pd.Series()
        recurring = recurring[recurring >= 2].index.tolist()[:8] if not recurring.empty else []

        context["transactions"] = {
            "total_count":        len(df),
            "total_debit":        round(total_debit, 2),
            "total_credit":       round(total_credit, 2),
            "net_flow":           round(net_flow, 2),
            "latest_balance":     round(float(df["BALANCE"].iloc[0]), 2) if len(df) > 0 else 0,
            "savings_rate":       round(net_flow / total_credit * 100, 1) if total_credit > 0 else 0,
            "category_spend":     cat_spend,
            "monthly_trend":      monthly,
            "top_merchants":      top_merch,
            "recurring_merchants":recurring,
        }

    # ── Anomaly report ────────────────────────────────────────────────────────
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
        context["anomalies"] = {
            "count":           report.get("anomaly_count", 0),
            "rate":            report.get("anomaly_rate",  0),
            "health_score":    report.get("health", {}).get("score", 0),
            "health_label":    report.get("health", {}).get("label", ""),
            "rules":           report.get("rules", {}),
            "recommendations": report.get("health", {}).get("recommendations", []),
            "top_anomalies":   report.get("anomalies", [])[:5],
        }

    return context


def build_system_prompt(context: dict) -> str:
    txn  = context.get("transactions", {})
    anom = context.get("anomalies",    {})

    cat_str = "\n".join(
        [f"  - {k}: ₹{v:,.2f}" for k, v in (txn.get("category_spend") or {}).items()]
    ) or "  No category data"

    merch_str = "\n".join(
        [f"  - {k}: ₹{v:,.2f}" for k, v in list((txn.get("top_merchants") or {}).items())[:6]]
    ) or "  No merchant data"

    monthly_str = "\n".join(
        [f"  - {m}: Debit ₹{v['debit']:,.0f} | Credit ₹{v['credit']:,.0f}"
         for m, v in list((txn.get("monthly_trend") or {}).items())[-3:]]
    ) or "  No monthly data"

    rules     = anom.get("rules", {})
    rule_5030 = rules.get("rule_50_30_20",  {})
    savings   = rules.get("savings_rate",   {})
    emerg     = rules.get("emergency_fund", {})
    vol       = rules.get("volatility",     {})

    recs_str = "\n".join(
        [f"  {i+1}. {r}" for i, r in enumerate(anom.get("recommendations", [])[:4])]
    ) or "  None"

    return f"""You are Banklytics AI — a personal financial analyst and advisor embedded in the Banklytics app.
You have full access to the user's real financial data and must give specific, data-driven, empathetic advice.

PERSONALITY:
- Speak like a smart, friendly financial advisor — not a robot
- Use the actual numbers from the data in every answer
- Be concise but insightful — no generic advice
- Use ₹ for Indian Rupees
- If something looks bad in the data, say so clearly but constructively
- Always end with 1 actionable suggestion

═══════════════════════════════════
USER'S FINANCIAL SNAPSHOT
═══════════════════════════════════

OVERVIEW:
  Total Transactions : {txn.get('total_count', 0)}
  Total Spent        : ₹{txn.get('total_debit', 0):,.2f}
  Total Received     : ₹{txn.get('total_credit', 0):,.2f}
  Net Flow           : ₹{txn.get('net_flow', 0):,.2f} ({'SURPLUS' if txn.get('net_flow', 0) >= 0 else 'DEFICIT'})
  Latest Balance     : ₹{txn.get('latest_balance', 0):,.2f}
  Savings Rate       : {txn.get('savings_rate', 0)}%

SPENDING BY CATEGORY:
{cat_str}

TOP MERCHANTS (by spend):
{merch_str}

RECURRING MERCHANTS: {', '.join(txn.get('recurring_merchants', []) or ['None'])}

RECENT MONTHLY TREND (last 3 months):
{monthly_str}

HEALTH & ANOMALY REPORT:
  Health Score  : {anom.get('health_score', 'N/A')} / 100 ({anom.get('health_label', '')})
  Anomalies     : {anom.get('count', 0)} ({anom.get('rate', 0)}% of transactions)
  Savings Rate  : {savings.get('rate', 'N/A')}% — {savings.get('status', '')}
  Emergency Fund: {emerg.get('months_covered', 'N/A')} months — {emerg.get('status', '')}
  50/30/20 Rule : {rule_5030.get('status', 'N/A')} (Needs:{rule_5030.get('needs_pct', '?')}% Wants:{rule_5030.get('wants_pct', '?')}% Saves:{rule_5030.get('saves_pct', '?')}%)
  Volatility    : {vol.get('cv_percent', 'N/A')}% — {vol.get('status', '')}

RECOMMENDATIONS FROM ANALYSIS:
{recs_str}

═══════════════════════════════════
You know EVERYTHING above about this user. Answer questions specifically using this data.
If the user asks something not covered by this data, say so honestly.
Do NOT make up numbers not in the data above.
Keep responses under 200 words unless a detailed breakdown is requested.
"""


# ── POST /api/ai/chat ─────────────────────────────────────────────────────────
@router.post("/ai/chat")
async def chat(payload: dict, authorization: str = Header(None)):
    messages = payload.get("messages", [])
    uid      = get_uid(authorization)

    if not messages:
        raise HTTPException(400, "messages required")

    if not GROQ_API_KEY:
        raise HTTPException(500, "GROQ_API_KEY not set — add it to your .env file")

    context       = build_financial_context(uid)
    system_prompt = build_system_prompt(context)

    client = Groq(api_key=GROQ_API_KEY)

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            *messages
        ],
        temperature=0.1,
        max_tokens=3000,
    )

    reply  = response.choices[0].message.content
    tokens = response.usage

    return {
        "reply":             reply,
        "model":             GROQ_MODEL,
        "tokens_used":       tokens.total_tokens,
        "prompt_tokens":     tokens.prompt_tokens,
        "completion_tokens": tokens.completion_tokens,
    }


# ── GET /api/ai/context ───────────────────────────────────────────────────────
@router.get("/ai/context")
async def get_context(authorization: str = Header(None)):
    uid     = get_uid(authorization)
    context = build_financial_context(uid)
    return context
