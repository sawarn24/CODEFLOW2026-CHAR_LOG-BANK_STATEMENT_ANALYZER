from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
import os, sys, json, math, textwrap
from datetime import datetime, date
import pandas as pd
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

router = APIRouter()

UPLOAD_DIR   = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
MASTER_FILE  = "transactions_master.xlsx"
ANOMALY_FILE = "anomaly_report.json"
REPORT_FILE  = "financial_report.pdf"
STATUS_FILE  = "report_status.json"

# ── Your Groq API key (or set env var GROQ_API_KEY) ──────────────────────
GROQ_API_KEY = os.getenv("groq_api_key")
GROQ_MODEL   = "openai/gpt-oss-120b"


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_uid(authorization=None):
    return "authenticated-user"


def set_status(uid: str, stage: str, pct: int, message: str):
    path = os.path.join(UPLOAD_DIR, uid, STATUS_FILE)
    with open(path, "w") as f:
        json.dump({"stage": stage, "pct": pct, "message": message,
                   "ts": datetime.utcnow().isoformat()}, f)


def fmt_inr(n: float) -> str:
    n = float(n or 0)
    if abs(n) >= 1e5:
        return f"₹{n/1e5:,.1f}L"
    if abs(n) >= 1e3:
        return f"₹{n/1e3:,.1f}K"
    return f"₹{n:,.0f}"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Build fact-pack from both data sources
# ─────────────────────────────────────────────────────────────────────────────
def build_fact_pack(uid: str) -> dict:
    master_path  = os.path.join(UPLOAD_DIR, uid, MASTER_FILE)
    anomaly_path = os.path.join(UPLOAD_DIR, uid, ANOMALY_FILE)

    if not os.path.exists(master_path):
        raise HTTPException(404, "Transactions not found — run analysis first")
    if not os.path.exists(anomaly_path):
        raise HTTPException(404, "Anomaly report not found — run detection first")

    df = pd.read_excel(master_path, dtype=str)
    df["AMOUNT"]  = pd.to_numeric(df["AMOUNT"],  errors="coerce").fillna(0)
    df["BALANCE"] = pd.to_numeric(df["BALANCE"], errors="coerce").fillna(0)
    df["TYPE"]    = df["TYPE"].fillna("DEBIT")

    with open(anomaly_path) as f:
        anom = json.load(f)

    debits  = df[df["TYPE"] == "DEBIT"]
    credits = df[df["TYPE"] == "CREDIT"]

    # Category breakdown (top 8 debit)
    cat_break = (
        debits.groupby("category")["AMOUNT"]
        .sum().sort_values(ascending=False).head(8)
        .to_dict()
    ) if "category" in df.columns else {}

    # Monthly trend
    df["_date"]  = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
    df["_month"] = df["_date"].dt.to_period("M").astype(str)
    monthly = []
    for mo, grp in df.groupby("_month"):
        if mo in ("NaT", "nan"):
            continue
        monthly.append({
            "month":  mo,
            "debit":  round(float(grp[grp["TYPE"] == "DEBIT"]["AMOUNT"].sum()), 2),
            "credit": round(float(grp[grp["TYPE"] == "CREDIT"]["AMOUNT"].sum()), 2),
        })
    monthly.sort(key=lambda x: x["month"])

    # Top merchants
    top_merch = (
        debits.groupby("Merchant")["AMOUNT"].sum()
        .sort_values(ascending=False).head(5)
        .to_dict()
    ) if "Merchant" in debits.columns else {}

    # Top 5 critical anomalies
    top_anomalies = [
        {k: a[k] for k in ("date", "transaction", "merchant", "amount", "severity", "reasons", "category")}
        for a in anom.get("anomalies", [])[:5]
    ]

    return {
        "summary":        anom.get("summary", {}),
        "health":         anom.get("health", {}),
        "rules":          anom.get("rules", {}),
        "anomaly_count":  anom.get("anomaly_count", 0),
        "anomaly_rate":   anom.get("anomaly_rate", 0),
        "top_anomalies":  top_anomalies,
        "category_breakdown": cat_break,
        "monthly_trend":  monthly,
        "top_merchants":  top_merch,
        "total_txns":     int(len(df)),
        "generated_at":   datetime.utcnow().strftime("%d %B %Y"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Call Groq LLM — returns dict of narrative sections
# ─────────────────────────────────────────────────────────────────────────────
def call_groq(fact_pack: dict) -> dict:
    from groq import Groq

    client = Groq(api_key=GROQ_API_KEY)

    fp = fact_pack
    summary = fp["summary"]
    health  = fp["health"]
    rules   = fp["rules"]

    # Compact fact summary for the prompt
    cat_lines = "\n".join(
        f"  • {cat}: {fmt_inr(amt)}"
        for cat, amt in list(fp["category_breakdown"].items())[:8]
    )
    monthly_lines = "\n".join(
        f"  • {m['month']}: spent {fmt_inr(m['debit'])}, earned {fmt_inr(m['credit'])}"
        for m in fp["monthly_trend"][-4:]
    )
    anomaly_lines = "\n".join(
        f"  • [{a['severity']}] {a['date']} — {a['merchant'] or a['transaction']} — {fmt_inr(a['amount'])} ({a['category']}) — {a['reasons'][0] if a['reasons'] else 'Unusual pattern'}"
        for a in fp["top_anomalies"]
    )

    prompt = f"""You are a senior personal finance analyst at a top Indian bank.
Your job is to write a clear, warm, and actionable financial health report for a bank customer.
Use simple, human language — no jargon. Be specific with numbers. Be honest but empathetic.
Write as if you are talking directly to the customer.

=== FINANCIAL DATA ===
Report Date: {fp['generated_at']}
Total Transactions Analysed: {fp['total_txns']}

CASH FLOW SUMMARY
  Total Income:   {fmt_inr(summary.get('total_credit', 0))}
  Total Spending: {fmt_inr(summary.get('total_debit', 0))}
  Net Flow:       {fmt_inr(summary.get('net_flow', 0))}  ({'SURPLUS' if summary.get('net_flow',0) >= 0 else 'DEFICIT'})
  Current Balance:{fmt_inr(summary.get('latest_balance', 0))}

FINANCIAL HEALTH SCORE: {health.get('score', 0)}/100 — {health.get('label', 'N/A')}

SCORE BREAKDOWN (grades):
{chr(10).join(f"  • {g[0]}: Grade {g[1]} ({g[2]})" for g in health.get('grades', []))}

50/30/20 RULE: {rules.get('rule_50_30_20', {}).get('status', 'N/A')}
  Needs (essentials): {rules.get('rule_50_30_20', {}).get('needs_pct', 0)}% (target ≤50%)
  Wants (lifestyle):  {rules.get('rule_50_30_20', {}).get('wants_pct', 0)}% (target ≤30%)
  Savings:            {rules.get('rule_50_30_20', {}).get('saves_pct', 0)}% (target ≥20%)

SAVINGS RATE: {rules.get('savings_rate', {}).get('rate', 0)}% — {rules.get('savings_rate', {}).get('status', 'N/A')}
EMERGENCY FUND: {rules.get('emergency_fund', {}).get('months_covered', 0)} months — {rules.get('emergency_fund', {}).get('status', 'N/A')}
SPENDING VOLATILITY: {rules.get('volatility', {}).get('cv_percent', 0)}% — {rules.get('volatility', {}).get('status', 'N/A')}
CATEGORY CONCENTRATION: {rules.get('concentration', {}).get('percent', 0)}% in {rules.get('concentration', {}).get('top_category', 'N/A')} — {rules.get('concentration', {}).get('status', 'N/A')}
UPI TRANSFERS: {fmt_inr(rules.get('upi_risk', {}).get('amount', 0))} ({rules.get('upi_risk', {}).get('percent', 0)}% of spending) — {rules.get('upi_risk', {}).get('status', 'N/A')}

TOP SPENDING CATEGORIES:
{cat_lines}

RECENT MONTHLY TREND:
{monthly_lines}

ANOMALIES DETECTED: {fp['anomaly_count']} ({fp['anomaly_rate']}% of transactions)
TOP FLAGGED TRANSACTIONS:
{anomaly_lines if anomaly_lines else '  No significant anomalies detected.'}

=== INSTRUCTIONS ===
Write EXACTLY 6 sections. Return ONLY valid JSON — no markdown, no backticks.
JSON structure:
{{
  "executive_summary": "2-3 sentences. Overall financial health in plain English. Mention the score and what it means for this person.",
  "income_and_cashflow": "3-4 sentences. Describe income, spending pattern, net flow. Is the person saving or spending more than they earn? What does the monthly trend show?",
  "spending_analysis": "3-4 sentences. Highlight the top spending categories. Are any categories dangerously high? What is the biggest spending driver?",
  "financial_rules_assessment": "4-5 sentences. Explain the 50/30/20 result, savings rate, and emergency fund in plain terms. What does PASS or FAIL actually mean for this person?",
  "anomalies_and_risks": "3-4 sentences. Explain the anomalies in human terms. Should the customer be worried? What should they check? If no anomalies, say so positively.",
  "action_plan": "Give exactly 4 specific, numbered action items the customer should do in the next 30 days. Be concrete and personalised to their actual numbers."
}}"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.65,
        max_tokens=1800,
    )

    raw = response.choices[0].message.content.strip()

    # 1. Strip markdown fences if present
    if "```" in raw:
        parts = raw.split("```")
        # find the json block
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    # 2. Extract just the JSON object (from first { to last })
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end+1]

    # 3. Strip control characters that break json.loads
    import re as _re
    raw = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)

    # 4. Fix unescaped newlines inside JSON string values
    #    (replace literal \n inside strings with \\n)
    def fix_newlines(m):
        return m.group(0).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    raw = _re.sub(r'(?s)"[^"\\]*(?:\\.[^"\\]*)*"', fix_newlines, raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Last resort: extract values with regex
        keys = ["executive_summary","income_and_cashflow","spending_analysis",
                "financial_rules_assessment","anomalies_and_risks","action_plan"]
        result = {}
        for key in keys:
            pat = _re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, _re.DOTALL)
            if pat:
                result[key] = pat.group(1).replace('\\n', ' ').replace('\\"', '"')
            else:
                result[key] = f"Section could not be parsed. Raw error: {e}"
        return result


# ─────────────────────────────────────────────────────────────────────────────
# 3. Render PDF with ReportLab
# ─────────────────────────────────────────────────────────────────────────────
def render_pdf(fact_pack: dict, narratives: dict, out_path: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.platypus import Flowable

    W, H   = A4
    MARGIN = 18 * mm

    # ── Colours ──────────────────────────────────────────────────────────────
    BG       = colors.HexColor("#080b12")
    SURFACE  = colors.HexColor("#111820")
    BORDER   = colors.HexColor("#1e2a38")
    GREEN    = colors.HexColor("#00e5a0")
    RED      = colors.HexColor("#ff4d6d")
    BLUE     = colors.HexColor("#3b9eff")
    YELLOW   = colors.HexColor("#f59e0b")
    PURPLE   = colors.HexColor("#a855f7")
    CYAN     = colors.HexColor("#06b6d4")
    TEXT     = colors.HexColor("#e2e8f0")
    TEXT_DIM = colors.HexColor("#8899aa")
    TEXT_MUT = colors.HexColor("#4a5568")
    WHITE    = colors.white

    health       = fact_pack["health"]
    score        = health.get("score", 0)
    score_label  = health.get("label", "N/A")
    score_color_hex = health.get("color", "#3b9eff")
    SCORE_COL    = colors.HexColor(score_color_hex)

    # ── Styles ────────────────────────────────────────────────────────────────
    def style(name, **kw):
        defaults = dict(
            fontName="Helvetica", fontSize=10, leading=15,
            textColor=TEXT, backColor=None,
            spaceAfter=0, spaceBefore=0, leftIndent=0,
        )
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    ST_TITLE    = style("title",    fontName="Helvetica-Bold", fontSize=22, textColor=GREEN,  leading=28, spaceAfter=2*mm)
    ST_SUBTITLE = style("subtitle", fontName="Helvetica",      fontSize=10, textColor=TEXT_DIM, leading=14, spaceAfter=6*mm)
    ST_H1       = style("h1",       fontName="Helvetica-Bold", fontSize=13, textColor=TEXT,   leading=18, spaceBefore=5*mm, spaceAfter=2*mm)
    ST_H2       = style("h2",       fontName="Helvetica-Bold", fontSize=10, textColor=CYAN,   leading=14, spaceBefore=3*mm, spaceAfter=1*mm)
    ST_BODY     = style("body",     fontName="Helvetica",      fontSize=9,  textColor=TEXT_DIM, leading=14, spaceAfter=3*mm)
    ST_MONO     = style("mono",     fontName="Courier",        fontSize=8,  textColor=TEXT,   leading=12)
    ST_LABEL    = style("label",    fontName="Helvetica-Bold", fontSize=7,  textColor=TEXT_MUT, leading=10, spaceAfter=0)
    ST_VAL      = style("val",      fontName="Helvetica-Bold", fontSize=15, textColor=TEXT,   leading=18, spaceAfter=0)
    ST_ACTION   = style("action",   fontName="Helvetica",      fontSize=9,  textColor=TEXT,   leading=14, leftIndent=4*mm, spaceAfter=2*mm)

    summary     = fact_pack["summary"]
    rules       = fact_pack["rules"]
    cat_break   = fact_pack["category_breakdown"]
    monthly     = fact_pack["monthly_trend"]
    anom_list   = fact_pack["top_anomalies"]
    anom_count  = fact_pack["anomaly_count"]

    # ── Custom Flowables ──────────────────────────────────────────────────────
    class ColorRect(Flowable):
        """Filled rounded rectangle — used as card backgrounds."""
        def __init__(self, w, h, fill, radius=4):
            super().__init__()
            self.w, self.h, self.fill, self.r = w, h, fill, radius
        def draw(self):
            self.canv.setFillColor(self.fill)
            self.canv.roundRect(0, 0, self.w, self.h, self.r, stroke=0, fill=1)

    class HLine(Flowable):
        def __init__(self, w, color=BORDER, thickness=0.5):
            super().__init__()
            self.w, self.color, self.t = w, color, thickness
        def draw(self):
            self.canv.setStrokeColor(self.color)
            self.canv.setLineWidth(self.t)
            self.canv.line(0, 0, self.w, 0)

    # ── Document ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="Banklytics Financial Health Report",
        author="Banklytics AI",
    )

    # ── Background on every page ──────────────────────────────────────────────
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, W, H, stroke=0, fill=1)
        # Footer
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(TEXT_MUT)
        canvas.drawString(MARGIN, 10*mm, "Banklytics Financial Report — Confidential")
        canvas.drawRightString(W - MARGIN, 10*mm, f"Page {doc.page}  |  {fact_pack['generated_at']}")
        canvas.restoreState()

    # ── Usable width ──────────────────────────────────────────────────────────
    UW = W - 2 * MARGIN   # ~174 mm

    story = []

    # ═══════════════════════════════════════════════════════════════════════════
    # COVER HEADER
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Banklytics", ST_TITLE))
    story.append(Paragraph(
        f"Financial Health Report &nbsp;&bull;&nbsp; {fact_pack['generated_at']}",
        ST_SUBTITLE
    ))
    story.append(HLine(UW, BORDER, 0.5))
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # HEALTH SCORE BANNER — big stat cards row
    # ═══════════════════════════════════════════════════════════════════════════
    net_flow    = summary.get("net_flow", 0)
    net_pos     = net_flow >= 0
    sav_rate    = rules.get("savings_rate", {}).get("rate", 0)
    emerg_mo    = rules.get("emergency_fund", {}).get("months_covered", 0)

    def stat_cell(label, value, color, sub=""):
        return [
            Paragraph(label, style("sl", fontName="Helvetica-Bold", fontSize=6.5,
                                   textColor=TEXT_MUT, leading=9)),
            Paragraph(value, style("sv", fontName="Helvetica-Bold", fontSize=18,
                                   textColor=color, leading=22)),
            Paragraph(sub,   style("ss", fontName="Helvetica", fontSize=7,
                                   textColor=TEXT_MUT, leading=9)),
        ]

    banner_data = [[
        stat_cell("HEALTH SCORE", f"{score}/100", SCORE_COL, score_label),
        stat_cell("NET FLOW",
                  fmt_inr(abs(net_flow)),
                  GREEN if net_pos else RED,
                  "SURPLUS" if net_pos else "DEFICIT"),
        stat_cell("SAVINGS RATE", f"{sav_rate}%",
                  GREEN if sav_rate >= 20 else (YELLOW if sav_rate >= 10 else RED),
                  rules.get("savings_rate", {}).get("status", "")),
        stat_cell("EMERGENCY FUND", f"{emerg_mo} mo",
                  GREEN if emerg_mo >= 6 else (YELLOW if emerg_mo >= 3 else RED),
                  rules.get("emergency_fund", {}).get("status", "")),
    ]]

    col_w = UW / 4
    banner = Table(banner_data, colWidths=[col_w]*4, rowHeights=None)
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
        ("BOX",        (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEAFTER",  (0, 0), (2, 0),   0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(banner)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION — Executive Summary (LLM)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Executive Summary", ST_H1))
    story.append(HLine(UW, GREEN, 1))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(narratives.get("executive_summary", ""), ST_BODY))
    story.append(Spacer(1, 4*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION — Cash Flow & Income (LLM + data table)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Income & Cash Flow", ST_H1))
    story.append(HLine(UW, BLUE, 1))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(narratives.get("income_and_cashflow", ""), ST_BODY))

    # Cash flow mini-table
    cf_data = [
        [Paragraph("METRIC", ST_LABEL),     Paragraph("AMOUNT", ST_LABEL),     Paragraph("NOTE", ST_LABEL)],
        ["Total Income",    fmt_inr(summary.get("total_credit", 0)), "All credits & salary"],
        ["Total Spending",  fmt_inr(summary.get("total_debit",  0)), "All debits & transfers"],
        ["Net Flow",        fmt_inr(net_flow),                        "SURPLUS" if net_pos else "⚠ DEFICIT"],
        ["Current Balance", fmt_inr(summary.get("latest_balance", 0)), "As of latest transaction"],
        ["Transactions",    str(fact_pack["total_txns"]),              "Total records analysed"],
    ]
    cf_table = Table(cf_data, colWidths=[UW*0.35, UW*0.25, UW*0.40])
    cf_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#0d1117")),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  TEXT_MUT),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("FONTNAME",      (1, 1), (1, -1),  "Courier-Bold"),
        ("TEXTCOLOR",     (1, 1), (1, -1),  GREEN),
        ("TEXTCOLOR",     (0, 1), (0, -1),  TEXT_DIM),
        ("TEXTCOLOR",     (2, 1), (2, -1),  TEXT_MUT),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [SURFACE, colors.HexColor("#0f1620")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 2*mm))
    story.append(cf_table)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION — Spending Analysis (LLM + category breakdown)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Spending Analysis", ST_H1))
    story.append(HLine(UW, PURPLE, 1))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(narratives.get("spending_analysis", ""), ST_BODY))

    # Category table
    total_spend = sum(cat_break.values()) or 1
    CAT_COLORS  = [GREEN, BLUE, YELLOW, RED, PURPLE, CYAN,
                   colors.HexColor("#f97316"), colors.HexColor("#ec4899")]
    cat_rows = [[
        Paragraph("CATEGORY",   ST_LABEL),
        Paragraph("AMOUNT",     ST_LABEL),
        Paragraph("% OF SPEND", ST_LABEL),
        Paragraph("BAR",        ST_LABEL),
    ]]
    for i, (cat, amt) in enumerate(list(cat_break.items())[:8]):
        pct  = amt / total_spend * 100
        bar  = "█" * int(pct / 3) + "░" * max(0, 33 - int(pct / 3))
        ccol = CAT_COLORS[i % len(CAT_COLORS)]
        cat_rows.append([
            Paragraph(cat,           style(f"c{i}", fontName="Helvetica", fontSize=8, textColor=TEXT_DIM, leading=11)),
            Paragraph(fmt_inr(amt),  style(f"a{i}", fontName="Courier-Bold", fontSize=8, textColor=ccol, leading=11)),
            Paragraph(f"{pct:.1f}%", style(f"p{i}", fontName="Helvetica-Bold", fontSize=8, textColor=TEXT, leading=11)),
            Paragraph(bar[:20],      style(f"b{i}", fontName="Courier", fontSize=7, textColor=ccol, leading=11)),
        ])

    cat_table = Table(cat_rows, colWidths=[UW*0.38, UW*0.20, UW*0.14, UW*0.28])
    cat_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#0d1117")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [SURFACE, colors.HexColor("#0f1620")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 2*mm))
    story.append(cat_table)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION — Financial Rules (LLM + grades)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Financial Rules Assessment", ST_H1))
    story.append(HLine(UW, YELLOW, 1))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(narratives.get("financial_rules_assessment", ""), ST_BODY))

    # Grades table
    grade_color = {"A": GREEN, "B": BLUE, "C": YELLOW, "D": RED}
    gr_rows = [[Paragraph(h, ST_LABEL) for h in ["METRIC", "GRADE", "IMPACT", "STATUS"]]]
    for g in health.get("grades", []):
        name, grade, delta = g
        gc   = grade_color.get(grade, TEXT)
        ispos = delta.startswith("+")
        gr_rows.append([
            Paragraph(name,  style("gn", fontName="Helvetica",      fontSize=8, textColor=TEXT_DIM, leading=11)),
            Paragraph(grade, style("gg", fontName="Courier-Bold",   fontSize=9, textColor=gc,       leading=12)),
            Paragraph(delta, style("gd", fontName="Courier-Bold",   fontSize=8,
                                   textColor=GREEN if ispos else RED, leading=11)),
            Paragraph("GOOD" if grade in ("A","B") else "NEEDS WORK",
                       style("gs", fontName="Helvetica-Bold", fontSize=7,
                             textColor=GREEN if grade in ("A","B") else YELLOW, leading=10)),
        ])
    gr_table = Table(gr_rows, colWidths=[UW*0.44, UW*0.12, UW*0.16, UW*0.28])
    gr_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#0d1117")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [SURFACE, colors.HexColor("#0f1620")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 2*mm))
    story.append(gr_table)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION — Anomalies & Risks (LLM + flagged table)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Anomalies & Risk Flags", ST_H1))
    story.append(HLine(UW, RED, 1))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(narratives.get("anomalies_and_risks", ""), ST_BODY))

    if anom_list:
        sev_col_map = {"CRITICAL": RED, "HIGH": colors.HexColor("#f97316"),
                       "MEDIUM": YELLOW, "LOW": BLUE}
        an_rows = [[Paragraph(h, ST_LABEL) for h in ["DATE", "MERCHANT", "AMOUNT", "SEV", "REASON"]]]
        for a in anom_list:
            sc = sev_col_map.get(a.get("severity","LOW"), BLUE)
            an_rows.append([
                Paragraph(str(a.get("date",""))[:10], style("ad", fontName="Courier", fontSize=7, textColor=TEXT_MUT, leading=10)),
                Paragraph((a.get("merchant","") or a.get("transaction",""))[:22],
                           style("am", fontName="Helvetica", fontSize=8, textColor=TEXT_DIM, leading=11)),
                Paragraph(fmt_inr(a.get("amount",0)),
                           style("aa", fontName="Courier-Bold", fontSize=8, textColor=sc, leading=11)),
                Paragraph(a.get("severity",""),
                           style("as", fontName="Helvetica-Bold", fontSize=7, textColor=sc, leading=10)),
                Paragraph((a.get("reasons",[""])[0] or "")[:45],
                           style("ar", fontName="Helvetica", fontSize=7, textColor=TEXT_MUT, leading=10)),
            ])
        an_table = Table(an_rows, colWidths=[UW*0.13, UW*0.22, UW*0.14, UW*0.12, UW*0.39])
        an_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#0d1117")),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [SURFACE, colors.HexColor("#0f1620")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ]))
        story.append(Spacer(1, 2*mm))
        story.append(an_table)
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION — Action Plan (LLM)
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Your 30-Day Action Plan", ST_H1))
    story.append(HLine(UW, GREEN, 1))
    story.append(Spacer(1, 2*mm))

    action_text = narratives.get("action_plan", "")
    # Split numbered items into separate paragraphs
    import re
    items = re.split(r'\n?\s*\d+[\.\)]\s*', action_text)
    items = [i.strip() for i in items if i.strip()]
    icons = ["🎯", "💰", "🏦", "📊"]
    for i, item in enumerate(items[:4]):
        num  = i + 1
        icon = icons[i % len(icons)]
        story.append(Paragraph(
            f"{icon} &nbsp;<b>Step {num}:</b> &nbsp;{item}",
            style(f"act{i}", fontName="Helvetica", fontSize=9, textColor=TEXT,
                  leading=14, leftIndent=6*mm, spaceAfter=3*mm,
                  backColor=SURFACE)
        ))
    story.append(Spacer(1, 5*mm))

    # ═══════════════════════════════════════════════════════════════════════════
    # FOOTER DISCLAIMER
    # ═══════════════════════════════════════════════════════════════════════════
    story.append(HLine(UW, BORDER, 0.5))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "This report is generated automatically by Banklytics AI for informational purposes only. "
        "It does not constitute financial advice. Please consult a certified financial advisor "
        "for personalised guidance. All figures are derived from your uploaded bank statement(s).",
        style("disc", fontName="Helvetica", fontSize=7, textColor=TEXT_MUT, leading=10, spaceAfter=0)
    ))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


# ─────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/report/generate")
async def generate_report(authorization: str = Header(None)):
    """
    Called automatically by the pipeline once both /analyze and /anomaly/detect
    have succeeded. Builds the LLM narrative and renders the PDF.
    """
    uid = get_uid(authorization)
    set_status(uid, "starting", 5, "Initialising report generator…")

    try:
        set_status(uid, "loading", 15, "Loading transaction and anomaly data…")
        fact_pack = build_fact_pack(uid)

        set_status(uid, "llm", 40, "Asking Groq AI to write your narrative…")
        narratives = call_groq(fact_pack)

        set_status(uid, "pdf", 70, "Rendering polished PDF report…")
        out_path = os.path.join(UPLOAD_DIR, uid, REPORT_FILE)
        render_pdf(fact_pack, narratives, out_path)

        set_status(uid, "done", 100, "Report ready — click Download PDF!")
        return {"status": "done", "message": "Report generated", "download_url": "/api/report/download"}

    except Exception as e:
        set_status(uid, "error", 0, str(e))
        raise HTTPException(500, f"Report generation failed: {e}")


@router.get("/report/status")
async def report_status(authorization: str = Header(None)):
    """Poll this endpoint to track report generation progress (0–100%)."""
    uid  = get_uid(authorization)
    path = os.path.join(UPLOAD_DIR, uid, STATUS_FILE)
    if not os.path.exists(path):
        return {"stage": "idle", "pct": 0, "message": "No report generation started"}
    with open(path) as f:
        return json.load(f)


@router.get("/report/download")
async def download_report(authorization: str = Header(None)):
    """Stream the generated PDF to the browser."""
    uid  = get_uid(authorization)
    path = os.path.join(UPLOAD_DIR, uid, REPORT_FILE)
    if not os.path.exists(path):
        raise HTTPException(404, "Report not generated yet")
    fname = f"Banklytics_Report_{datetime.utcnow().strftime('%Y%m%d')}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=fname)
