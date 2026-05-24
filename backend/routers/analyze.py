from fastapi import APIRouter, Header, HTTPException
from dotenv import load_dotenv
load_dotenv()
import os, sys, json, time, re
import pandas as pd

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

router = APIRouter()

UPLOAD_DIR  = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
MASTER_FILE = "transactions_master.xlsx"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.3-70b-versatile"

CATEGORIES = [
    "Food & Dining", "Groceries", "Shopping", "Transport",
    "Utilities & Bills", "Rent & Housing", "EMI & Loan Repayment",
    "Insurance", "Salary & Income", "UPI Person Transfer",
    "Fund Transfer", "Subscriptions", "ATM & Cash",
    "Medical & Healthcare", "Education", "Investment & Savings",
    "Travel & Hotels", "Government & Taxes", "Bank Charges & Fees",
]

SYSTEM_PROMPT = """
You are a senior financial data scientist specializing in Indian banking
transaction classification. You have processed millions of Indian bank
statements from SBI, HDFC, ICICI, PNB, Yes Bank, Axis, Kotak, Union Bank,
Canara Bank, and all major Indian cooperative and rural banks.

You deeply understand:
- How UPI transaction strings are structured across different apps
- The difference between person-to-person transfers and merchant payments
- How Indian banks format NEFT, RTGS, IMPS, NACH, ECS transactions
- Indian merchant naming conventions and abbreviations
- Common Indian personal names that appear in UPI transfers
- How salaries, EMIs, insurance premiums appear in bank statements
- Bank-specific fee structures and how they appear in statements

AVAILABLE CATEGORIES — USE EXACTLY THESE NAMES:

1.  Food & Dining
    Restaurants, food delivery, cafes, dhabas, canteens, bakeries.
    Apps: Swiggy, Zomato, Dominos, KFC, McDonalds, Dunkin, Subway,
    Box8, Rebel Foods, EatFit, Faasos, Behrouz Biryani.

2.  Groceries
    Supermarkets, online grocery, kirana stores, daily essentials.
    Apps/Stores: BigBasket, Blinkit, Zepto, DMart, Reliance Fresh,
    More Supermarket, Spencer's, Grofers, Swiggy Instamart.

3.  Shopping
    Retail, ecommerce, clothing, electronics, home goods, personal care.
    Platforms: Amazon, Flipkart, Myntra, Ajio, Meesho, Nykaa, Snapdeal,
    Tata CLiQ, Croma, Vijay Sales, Reliance Digital, H&M, Zara, Decathlon.

4.  Transport
    All commute and vehicle-related expenses.
    Apps: Uber, Ola, Rapido, InDrive, BluSmart.
    Also: metro recharge, bus pass, auto UPI, petrol pump, FastTag toll.

5.  Utilities & Bills
    Electricity, gas, water, broadband, DTH, mobile bills, prepaid recharge.
    Providers: BESCOM, MSEB, TATA Power, IGL, MGL, Jio Fiber, Airtel, ACT.

6.  Rent & Housing
    House rent, PG fees, hostel, society maintenance, apartment association dues.
    Signal: large recurring monthly transfer with RENT/MAINTENANCE/SOCIETY.

7.  EMI & Loan Repayment
    Home loan, personal loan, car loan, consumer durable, education loan EMIs.
    Signals: NACH DR, ECS DR, EMI, LOAN, INSTALMENT, lender names.

8.  Insurance
    Life, health, vehicle insurance premiums.
    Providers: LIC, HDFC Life, ICICI Pru, SBI Life, Star Health, Niva Bupa.

9.  Salary & Income
    Monthly salary, freelance, consulting, business revenue, pension, PF withdrawal.
    Signal: large credit from a company/organization name, SALARY/PAYROLL/WAGES.

10. UPI Person Transfer
    Money sent/received between individuals via UPI.
    Apps: PhonePe, Google Pay, Paytm, BHIM, Amazon Pay, WhatsApp Pay.
    Signal: merchant is a personal Indian name or name@upi handle.

11. Fund Transfer
    NEFT, RTGS, IMPS bank-to-bank transfers (NOT UPI rails).
    Signal: NEFT/RTGS/IMPS keywords, account numbers, IFSC codes.

12. Subscriptions
    Recurring digital services: Netflix, Amazon Prime, Hotstar, Spotify,
    Microsoft 365, Google One, Adobe, ChatGPT, gaming subscriptions.

13. ATM & Cash
    ATM cash withdrawal, CDM cash deposit.
    Signal: ATM WDL, CASH WDL, CDM DEPOSIT.

14. Medical & Healthcare
    Hospitals, pharmacies, diagnostic labs, dental, eye care.
    Providers: Apollo, Fortis, 1mg, Netmeds, Dr Lal PathLabs.

15. Education
    School/college fees, coaching classes, online courses, exam fees.
    Providers: FIITJEE, Allen, Byju's, Vedantu, Coursera, Udemy.

16. Investment & Savings
    Mutual fund SIP, stocks, FD, RD, PPF, NPS, gold.
    Providers: Zerodha, Groww, Kuvera, Angel One, ICICI Direct.

17. Travel & Hotels
    Flights, trains, hotels, buses, holiday packages.
    Providers: IRCTC, IndiGo, MakeMyTrip, OYO, redBus, Booking.com.

18. Government & Taxes
    Income tax, GST, property tax, road tax, municipal payments, fines.
    Portals: PayGov, BBPS government billers, Challan 280.

19. Bank Charges & Fees
    Fees charged by the bank itself: AMC, SMS charges, minimum balance
    penalty, cheque book, NEFT charges, locker rent.
    Signal: FEE, CHARGE, PENALTY, ISSUANCE, MAINTENANCE — bank as payee.

DECISION FRAMEWORK:
STEP 1 — Merchant name: known business → business category; person name → UPI Person Transfer; bank fee keyword → Bank Charges & Fees.
STEP 2 — Transaction type: NACH/ECS → EMI or Insurance; ATM WDL → ATM & Cash; NEFT/RTGS/IMPS → Fund Transfer; UPI~ → UPI Person Transfer or merchant UPI.
STEP 3 — Amount & direction: large credit from company → Salary; same amount monthly → EMI/Rent/Insurance; small recurring digital → Subscriptions.
STEP 4 — Still unsure: pick the most economically meaningful category. Never leave blank.

CRITICAL RULES:
- Only use the exact 19 category names above.
- Every transaction gets exactly one category.
- UPI person transfers and Fund transfers are DIFFERENT — distinguish carefully.
- Bank fees are NOT government taxes.
- Large credit from individual = UPI Transfer, NOT Salary.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_uid(authorization: str = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "authenticated-user"
    return "authenticated-user"


def get_master_path(uid: str) -> str:
    return os.path.join(UPLOAD_DIR, uid, MASTER_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# Groq batch categoriser
# ─────────────────────────────────────────────────────────────────────────────
def label_batch_groq(rows: list, batch_num: int, client) -> list:
    categories_str = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(CATEGORIES))

    numbered = "\n".join([
        f"{i+1}. Particulars: {r['particulars']} | Merchant: {r['merchant']} | "
        f"Amount: Rs.{r['amount']} | Type: {r['txn_type']}"
        for i, r in enumerate(rows)
    ])

    user_prompt = f"""Classify each transaction into exactly one of these 19 categories:
{categories_str}

TRANSACTIONS:
{numbered}

Return ONLY a raw JSON array with exactly {len(rows)} objects.
No explanation. No markdown. No backticks. Just the JSON array.

[
  {{"id": 1, "category": "Category Name"}},
  {{"id": 2, "category": "Category Name"}}
]"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=3000,
        )

        text = response.choices[0].message.content.strip()

        # Strip markdown fences
        text = text.replace("```json", "").replace("```", "").strip()

        # Extract JSON array
        start = text.find("[")
        end   = text.rfind("]") + 1
        if start != -1 and end > 0:
            text = text[start:end]

        result = json.loads(text)
        return [item["category"] for item in result]

    except json.JSONDecodeError as e:
        print(f"JSON parse error batch {batch_num}: {e}")
        return ["Fund Transfer"] * len(rows)   # safe fallback

    except Exception as e:
        print(f"Groq API error batch {batch_num}: {e}")
        time.sleep(3)
        return ["Fund Transfer"] * len(rows)


def predict_categories_groq(
    particulars: list,
    merchants: list,
    txn_types: list,
    amounts: list,
    batch_size: int = 40,
) -> list:
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)

    rows = [
        {
            "particulars": str(p).strip(),
            "merchant":    str(m).strip() if m else "UNKNOWN",
            "txn_type":    str(t).strip() if t else "UNKNOWN",
            "amount":      str(a).strip() if a else "0",
        }
        for p, m, t, a in zip(particulars, merchants, txn_types, amounts)
    ]

    all_categories = []
    batches = [rows[i:i+batch_size] for i in range(0, len(rows), batch_size)]

    for i, batch in enumerate(batches):
        print(f"Groq labelling batch {i+1}/{len(batches)} ({len(batch)} rows)...")
        cats = label_batch_groq(batch, i + 1, client)

        # Validate — if wrong count returned, pad with fallback
        if len(cats) != len(batch):
            cats = (cats + ["Fund Transfer"] * len(batch))[:len(batch)]

        all_categories.extend(cats)
        time.sleep(0.3)   # gentle rate-limit buffer

    return all_categories


# ─────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def run_analysis(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    master_path = get_master_path(uid)

    if not os.path.exists(master_path):
        raise HTTPException(404, "No transactions found — upload a bank statement first")

    df = pd.read_excel(master_path, dtype=str)

    if df.empty:
        raise HTTPException(400, "Transactions file is empty — upload a statement first")

    if "TRANSACTION" not in df.columns:
        raise HTTPException(400, "Missing TRANSACTION column in master file")

    particulars = df["TRANSACTION"].fillna("").tolist()
    merchants   = df["Merchant"].fillna("").tolist()  if "Merchant" in df.columns else [""] * len(df)
    txn_types   = df["TYPE"].fillna("").tolist()      if "TYPE"     in df.columns else [""] * len(df)
    amounts     = df["AMOUNT"].fillna("0").tolist()   if "AMOUNT"   in df.columns else ["0"] * len(df)

    print(f"Running Groq categorisation on {len(df)} transactions...")
    categories = predict_categories_groq(particulars, merchants, txn_types, amounts)

    df["category"] = categories
    df["AMOUNT"]   = pd.to_numeric(df["AMOUNT"],  errors="coerce").fillna(0)
    df["BALANCE"]  = pd.to_numeric(df["BALANCE"], errors="coerce").fillna(0)

    df.to_excel(master_path, index=False)
    print("Groq categories saved ✓")

    return {
        "status":      "success",
        "total":       len(df),
        "categorised": len(df),
        "message":     "Analysis complete (Groq LLM)",
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
    df["TYPE"]    = df["TYPE"].fillna("DEBIT")

    total_transactions = len(df)
    debits  = df[df["TYPE"] == "DEBIT"]
    credits = df[df["TYPE"] == "CREDIT"]

    total_debit  = float(debits["AMOUNT"].sum())
    total_credit = float(credits["AMOUNT"].sum())
    net_flow     = total_credit - total_debit
    latest_balance = float(df["BALANCE"].iloc[0]) if len(df) > 0 else 0.0

    debit_by_cat        = debits.groupby("category")["AMOUNT"].sum().sort_values(ascending=False)
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

    df["_date"]  = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
    df["_month"] = df["_date"].dt.to_period("M").astype(str)

    monthly = []
    for month, grp in df.groupby("_month"):
        if month == "NaT":
            continue
        monthly.append({
            "month":  month,
            "debit":  round(float(grp[grp["TYPE"] == "DEBIT"]["AMOUNT"].sum()),  2),
            "credit": round(float(grp[grp["TYPE"] == "CREDIT"]["AMOUNT"].sum()), 2),
        })
    monthly.sort(key=lambda x: x["month"])

    top_merchants = (
        debits.groupby("Merchant")["AMOUNT"].sum()
        .sort_values(ascending=False).head(8)
    )
    top_merchants_list = [
        {"merchant": m, "amount": round(float(a), 2)}
        for m, a in top_merchants.items()
        if m and m != "nan"
    ]

    credit_by_cat = credits.groupby("category")["AMOUNT"].sum().sort_values(ascending=False)
    credit_breakdown = [
        {"category": cat, "amount": round(float(amt), 2)}
        for cat, amt in credit_by_cat.items()
    ]

    return {
        "summary": {
            "total_transactions": total_transactions,
            "total_debit":        round(total_debit,  2),
            "total_credit":       round(total_credit, 2),
            "net_flow":           round(net_flow,     2),
            "latest_balance":     round(latest_balance, 2),
        },
        "category_breakdown": category_breakdown,
        "credit_breakdown":   credit_breakdown,
        "monthly_trend":      monthly,
        "top_merchants":      top_merchants_list,
        "transactions":       df.drop(columns=["_date", "_month"], errors="ignore").to_dict(orient="records"),
    }


@router.post("/clear-data")
async def clear_data(authorization: str = Header(None)):
    uid         = get_uid(authorization)
    master_path = get_master_path(uid)

    if not os.path.exists(master_path):
        raise HTTPException(404, "No data to clear")

    empty = pd.DataFrame(columns=[
        "DATE", "TRANSACTION", "AMOUNT", "TYPE", "BALANCE",
        "Merchant", "source_file", "uploaded_at", "category"
    ])
    empty.to_excel(master_path, index=False)

    return {"status": "cleared", "message": "All transactions cleared"}
