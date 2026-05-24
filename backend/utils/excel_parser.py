
import pandas as pd
import re

COL_ALIASES = {
    "DATE":        ["date", "value date", "valuedate", "txn date", "transaction date", "posting date", "post date"],
    "TRANSACTION": ["transaction", "description", "details", "narration", "particulars", "remarks", "txn details"],
    "AMOUNT":      ["amount", "txn amount", "transaction amount"],
    "TYPE":        ["type", "txn type", "dr/cr", "debit/credit", "cr/dr"],
    "BALANCE":     ["balance", "closing balance", "available balance", "running balance"],
    "DEBIT":       ["debit", "withdrawal", "dr", "debit amt", "debit amount", "₹ debit"],
    "CREDIT":      ["credit", "deposit", "cr", "credit amt", "credit amount", "₹ credit"],
}

def normalize_col(name):
    return str(name).strip().lower().replace("(₹)","").replace("(rs)","").replace("  "," ").strip()

def find_col(df_cols, field):
    aliases = COL_ALIASES.get(field, [field.lower()])
    for col in df_cols:
        if normalize_col(col) in aliases:
            return col
    return None

def detect_header_row(df_raw):
    keywords = {"date","description","narration","transaction","balance","amount","debit","credit"}
    for i, row in df_raw.iterrows():
        vals = {str(v).strip().lower() for v in row.values if pd.notna(v)}
        if len(vals & keywords) >= 2:
            return i
    return 0

def clean_amount(val):
    if pd.isna(val) or str(val).strip() in ("", "-", "nan"):
        return 0.0
    s = str(val).replace(",","").replace("₹","").replace("INR","").strip()
    try:
        return abs(float(s))
    except:
        return 0.0

def infer_type(row, debit_col, credit_col, type_col):
    if type_col:
        t = str(row.get(type_col,"")).strip().upper()
        if any(x in t for x in ["DR","DEBIT","WDL","WITHDRAWAL"]):
            return "DEBIT"
        if any(x in t for x in ["CR","CREDIT","DEP","DEPOSIT"]):
            return "CREDIT"
    if debit_col and credit_col:
        d = clean_amount(row.get(debit_col, 0))
        c = clean_amount(row.get(credit_col, 0))
        if d > 0: return "DEBIT"
        if c > 0: return "CREDIT"
    return "DEBIT"

def extract_merchant(txn):
    txn = str(txn).strip()
    upi = re.search(r'UPI[~/\-][\d~]*/([^/\d][^/]*?)(?:/\d{10}|$)', txn, re.I)
    if upi: return upi.group(1).strip().title()
    imps = re.search(r'(?:IMPS|NEFT|RTGS)[^/]*/[^/]*/([A-Za-z][^/]{2,}?)(?:/|$)', txn, re.I)
    if imps: return imps.group(1).strip().title()
    atm = re.search(r'ATM\s+(?:WDL\s+)?([A-Z][A-Z\s]{2,20})', txn, re.I)
    if atm: return atm.group(1).strip().title()
    pos = re.search(r'(?:POS|ECOM)\s+(?:\d+\s+)?([A-Z][A-Z\s]{2,})', txn, re.I)
    if pos: return pos.group(1).strip().title()
    SKIP = {"DEP","TFR","DR","CR","NFS","ACH","ECS","EMI","WDL","NACH","SI","BY","TO","FROM"}
    for w in txn.upper().split():
        w_clean = re.sub(r'[^A-Z]','',w)
        if len(w_clean) >= 3 and w_clean not in SKIP:
            return w_clean.title()
    return txn[:20]

def parse_excel(filepath: str) -> pd.DataFrame:
    ext = filepath.lower().split(".")[-1]
    if ext == "csv":
        df_raw = pd.read_csv(filepath, header=None, dtype=str, encoding="utf-8", errors="replace")
    else:
        df_raw = pd.read_excel(filepath, header=None, dtype=str)

    header_row = detect_header_row(df_raw)

    if ext == "csv":
        df = pd.read_csv(filepath, header=header_row, dtype=str, encoding="utf-8", errors="replace")
    else:
        df = pd.read_excel(filepath, header=header_row, dtype=str)

    df.columns = [str(c).strip() for c in df.columns]
    cols = list(df.columns)

    date_col   = find_col(cols, "DATE")
    txn_col    = find_col(cols, "TRANSACTION")
    amt_col    = find_col(cols, "AMOUNT")
    type_col   = find_col(cols, "TYPE")
    bal_col    = find_col(cols, "BALANCE")
    debit_col  = find_col(cols, "DEBIT")
    credit_col = find_col(cols, "CREDIT")

    rows_out = []
    for _, row in df.iterrows():
        if row.isna().all(): continue
        raw_date = str(row[date_col]).strip() if date_col else ""
        if not raw_date or raw_date.lower() in ("nan","none",""): continue

        txn = str(row[txn_col]).strip() if txn_col else ""

        if amt_col:
            amount = clean_amount(row[amt_col])
        elif debit_col and credit_col:
            d = clean_amount(row.get(debit_col,0))
            c = clean_amount(row.get(credit_col,0))
            amount = d if d > 0 else c
        else:
            amount = 0.0

        txn_type = infer_type(row, debit_col, credit_col, type_col)
        balance  = clean_amount(row[bal_col]) if bal_col else 0.0

        rows_out.append({
            "DATE":        raw_date,
            "TRANSACTION": txn,
            "AMOUNT":      amount,
            "TYPE":        txn_type,
            "BALANCE":     balance,
            "Merchant":    extract_merchant(txn),
        })

    return pd.DataFrame(rows_out, columns=["DATE","TRANSACTION","AMOUNT","TYPE","BALANCE","Merchant"])
