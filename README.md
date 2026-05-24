# ₹ Banklytics

> **AI-powered personal finance intelligence for Indian bank statements.**  
> Upload your bank statement → get ML-categorised transactions → Isolation Forest anomaly detection → economist-grade financial health score → downloadable PDF report.

---

## Download

| Resource | Link |
|---|---|
| 📱 **Android APK** | [Download APK](https://drive.google.com/file/d/17p_72mTfbBS57rzJQfWEIInk-o501aB_/view?usp=drive_link) |
| 🤗 **Hugging Face Model** | [sawarn24/banklytics](https://huggingface.co/sawarn24/banklytics) |

---

## What It Does

Banklytics turns raw bank statement exports (PDF or Excel) into a full financial intelligence dashboard. It parses your transactions, uses a fine-tuned DistilBERT model to categorise each one, then trains an Isolation Forest model on *your own spending patterns* to flag anomalies. Finally, it applies real-world economist rules (50/30/20 budget rule, savings rate, emergency fund ratio, MoM spending velocity) to produce a financial health score and actionable recommendations.

---

## Features

| Feature | Details |
|---|---|
| **Multi-format ingestion** | PDF (pdfplumber, table + text fallback) and Excel/CSV (XLSX, XLS, CSV) |
| **Smart parsing** | Auto-detects header rows, column aliases, debit/credit separation, merchant extraction from UPI/IMPS/NEFT/ATM strings |
| **ML categorisation** | Fine-tuned DistilBERT (`finsight_model`) classifies transactions into categories like Food & Dining, Transport, Shopping, UPI Person Transfer, etc. |
| **Anomaly detection** | Isolation Forest trained per-user on 6 features — flags unusual transactions relative to *your* patterns, not a global threshold |
| **Financial health score** | 0–100 composite score using savings rate, emergency fund, spending velocity, diversification index, UPI ratio, and large transaction analysis |
| **Economist rules** | 50/30/20 budget split, month-over-month change, Herfindahl concentration index, recurring merchant detection |
| **PDF report** | Downloadable styled report with health score, key metrics, budget analysis, recommendations, and anomaly table |
| **Firebase auth** | Email/password + Google sign-in, per-user isolated data |
| **Duplicate deduplication** | Appending multiple statements skips already-seen transactions |

---

## Project Structure

```
C:\SREY2K26\
├── backend\
│   ├── main.py                  # FastAPI app, routes, Firebase init
│   ├── .env                     # Firebase credentials (not in repo)
│   ├── routers\
│   │   ├── upload.py            # POST /api/upload/excel, /api/upload/pdf
│   │   ├── analyze.py           # POST /api/analyze, GET /api/analysis/data
│   │   └── insights.py          # POST /api/insights/run, GET /api/insights/data, /report
│   └── utils\
│       ├── excel_parser.py      # Excel/CSV transaction parser
│       ├── pdf_parser.py        # PDF parser (table + text fallback)
│       └── save_transactions.py # Master Excel append + deduplication
│
├── frontend\
│   ├── css\
│   │   └── global.css           # Design system (DM Serif Display + DM Sans, dark theme)
│   ├── js\
│   │   └── utils.js             # Firebase init, apiFetch, toast, auth helpers
│   └── pages\
│       ├── login.html           # Sign in (email/password + Google)
│       ├── register.html        # Create account
│       ├── home.html            # Upload statements, view uploaded files
│       ├── analysis.html        # Basic analytics dashboard (donut, bar chart, table)
│       └── insights.html        # AI Intelligence Dashboard (anomalies, health score, PDF)
│
├── uploads\                     # Per-user transaction storage
│   └── <uid>\
│       ├── transactions_master.xlsx
│       └── insights_cache.json
│
└── finsight_model\              # Fine-tuned DistilBERT model (not in repo)
    ├── config.json
    ├── model.safetensors
    ├── tokenizer files...
    └── label_map.json
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <your-repo>
cd SREY2K26

pip install -r requirements.txt
pip install scikit-learn reportlab
```

**`requirements.txt` includes:**
```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
python-multipart>=0.0.9
firebase-admin>=6.5.0
pdfplumber>=0.11.0
pandas
openpyxl>=3.1.2
transformers>=4.33.0
torch>=2.7.0
aiofiles>=23.2.1
scikit-learn
reportlab
python-dotenv
```

### 2. Firebase setup

1. Go to [Firebase Console](https://console.firebase.google.com) → your project → Project Settings → Service Accounts
2. Click **Generate new private key**
3. Create a `.env` file in `backend/` with the following fields from the key:

```
type=service_account
project_id=your_project_id
private_key_id=your_key_id
private_key="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email=your_client_email
client_id=your_client_id
auth_uri=https://accounts.google.com/o/oauth2/auth
token_uri=https://oauth2.googleapis.com/token
auth_provider_x509_cert_url=https://www.googleapis.com/oauth2/v1/certs
client_x509_cert_url=your_client_cert_url
universe_domain=googleapis.com

FRONTEND_DIR=C:\SREY2K26\frontend
```

4. Enable **Email/Password** and **Google** sign-in under Authentication → Sign-in method

### 3. Place your DistilBERT model

Download from [sawarn24/banklytics](https://huggingface.co/sawarn24/banklytics) and place at `C:\SREY2K26\finsight_model\` with:
- `config.json`
- `model.safetensors` (or `pytorch_model.bin`)
- `tokenizer_config.json`, `vocab.txt`
- `label_map.json` → `{ "id2label": { "0": "Food & Dining", "1": "Transport", ... } }`

### 4. Run the server

```bash
cd backend
uvicorn main:app --reload
```

Server starts at `http://localhost:8000`.

---

## API Reference

### Upload

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/upload/excel` | Upload `.xlsx`, `.xls`, or `.csv` bank statement |
| `POST` | `/api/upload/pdf` | Upload PDF bank statement |
| `GET`  | `/api/transactions` | Get all saved transactions as JSON |
| `GET`  | `/api/transactions/download` | Download master transactions Excel |
| `GET`  | `/api/uploads` | List all uploaded files for user |

### Analyse

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/analyze` | Run DistilBERT model on transactions, fill category column |
| `GET`  | `/api/analysis/data` | Get categorised data + dashboard stats |
| `POST` | `/api/clear-data` | Wipe transactions master file |

### Insights (AI)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/insights/run` | Train Isolation Forest, compute health score, cache results |
| `GET`  | `/api/insights/data` | Return cached insights JSON |
| `GET`  | `/api/insights/report` | Download PDF financial health report |

---

## How the Isolation Forest Works

The model trains **from scratch on every run**, using only the current user's own transactions — so anomaly detection is personal, not based on any global average.

**Features used per transaction:**
1. `AMOUNT` — rupee value
2. Day of week (0=Mon … 6=Sun)
3. Day of month (1–31)
4. Merchant frequency — how often you transact with this merchant
5. Category frequency — how often this category appears for you
6. Is debit (0/1)

**Why this works:** If you regularly spend ₹100–200 on food, a ₹5,000 food transaction gets flagged. But if you regularly spend ₹3,000–5,000 on food, it won't. The contamination rate auto-adjusts (~5% of transactions expected as anomalous).

After detection, each anomaly gets a human-readable reason:
- *"Amount is 8.3× your usual spend in Food & Dining"*
- *"Unusually large transaction at Swiggy (4.1× normal)"*
- *"First-ever transaction with XYZ Merchant"*

---

## Financial Health Score

The score (0–100) is computed from:

| Rule | Weight | Source |
|------|--------|--------|
| Savings rate (target ≥20%) | ±25 | `(income - spend) / income` |
| Emergency fund (target 3–6 months) | ±20 | `balance / avg_monthly_spend` |
| Month-over-month spend change | ±10 | Last two months comparison |
| Spending diversification | ±10 | Herfindahl concentration index |
| UPI/P2P transfer ratio | ±8 | UPI transfers as % of total spend |
| Large transaction spikes | ±5 | Transactions > 3× your median |

**Score labels:** Excellent (80+) · Good (65–79) · Fair (45–64) · At Risk (<45)

---

## Pages

| URL | Page | Description |
|-----|------|-------------|
| `/` | Login | Email/password + Google sign-in |
| `/register` | Register | Create account with email verification |
| `/home` | Home | Upload statements, view file list, go to analysis |
| `/analysis` | Analytics | Basic charts: spending by category, monthly cash flow, merchant breakdown, transaction table |
| `/insights` | AI Insights | Isolation Forest anomalies, health score gauge, 50/30/20 budget bars, recommendations, PDF download |

---

## Supported Banks

The parsers are tested against:

- **SBI** — PDF table format and text fallback
- **HDFC** — Excel and PDF
- **ICICI** — Excel
- **Any bank** exporting standard CSV with date/description/debit/credit/balance columns

---

## Design System

The UI uses a custom dark financial aesthetic:

- **Fonts:** DM Serif Display (headings/numbers) + DM Sans (body)
- **Colors:** `#0a0c0f` background · `#4ade80` emerald accent · `#22d3ee` cyan secondary · `#f87171` danger
- **Components:** Cards, badges, toasts, spinner, form inputs — all in `global.css`

---

