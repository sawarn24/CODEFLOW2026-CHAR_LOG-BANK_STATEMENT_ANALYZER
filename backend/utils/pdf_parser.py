import re
import pandas as pd
import pdfplumber

DATE_RE    = re.compile(r'\d{2}[-/]\d{2}[-/]\d{4}|\d{2}[-/]\d{2}[-/]\d{2}|\d{2}\s+\w{3}\s+\d{4}')
AMOUNT_RE  = re.compile(r'[\d,]+\.\d{2}')
DEBIT_KW   = ["dr","debit","wdl","withdrawal","payment","paid","purchase","atm","emi debit","neft dr","nach dr","upi/dr"]
CREDIT_KW  = ["cr","credit","dep","deposit","received","refund","cashback","interest","salary","neft cr","upi/cr"]


def clean_amount(val) -> float:
    if not val or str(val).strip() in ("", "-", "nan", "None"):
        return 0.0
    try:
        return abs(float(str(val).replace(",", "").replace("₹", "").strip()))
    except:
        return 0.0


def infer_type(text: str) -> str:
    t = text.lower()
    for kw in DEBIT_KW:
        if kw in t:
            return "DEBIT"
    for kw in CREDIT_KW:
        if kw in t:
            return "CREDIT"
    return "DEBIT"


def extract_merchant(txn: str) -> str:
    txn = str(txn).strip()
    
    upi = re.search(r'UPI/(?:DR|CR)/\d+/([^/]+)', txn, re.I)
    if upi:
        name = upi.group(1).strip()
        
        name = re.split(r'[/\d]', name)[0].strip()
        if len(name) >= 2:
            return name.title()

    # IMPS pattern
    imps = re.search(r'(?:IMPS|NEFT|RTGS)[^/]*/[^/]*/([A-Za-z][^/]{2,}?)(?:/|$)', txn, re.I)
    if imps:
        return imps.group(1).strip().title()

    # ATM
    atm = re.search(r'ATM\s+(?:WDL\s+)?([A-Z][A-Z\s]{2,20})', txn, re.I)
    if atm:
        return atm.group(1).strip().title()

    SKIP = {"DEP","TFR","DR","CR","NFS","ACH","ECS","EMI","WDL","NACH","SI","BY","TO","FROM","UPI","SBIN","CBIN","PUNB","YESB","HDFC","ICIC"}
    for w in txn.upper().split():
        w_clean = re.sub(r'[^A-Z]', '', w)
        if len(w_clean) >= 3 and w_clean not in SKIP:
            return w_clean.title()
    return txn[:20]


def find_col(headers: list, keywords: list):
    """Return index of first header matching any keyword."""
    for kw in keywords:
        for i, h in enumerate(headers):
            if kw in str(h).lower().replace(" ", ""):
                return i
    return None


def parse_pdf_table(filepath: str) -> pd.DataFrame:
    """Extract transactions from PDF tables (works for SBI, HDFC, ICICI digital PDFs)."""
    rows_out = []

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue

                
                header_idx = None
                for i, row in enumerate(table):
                    vals = [str(v).strip().lower() for v in row if v and str(v).strip()]
                    joined = " ".join(vals)
                    if any(k in joined for k in ["date","narration","description","debit","credit","balance","withdrawal","deposit"]):
                        header_idx = i
                        break

                if header_idx is None:
                    
                    for row in table:
                        if not row or all(not v for v in row):
                            continue
                       
                        date_val = None
                        date_col_i = None
                        for ci, cell in enumerate(row):
                            if cell and DATE_RE.search(str(cell)):
                                date_val = str(cell).strip()
                                date_col_i = ci
                                break
                        if not date_val:
                            continue

                        # Collect all amounts in row
                        amounts = []
                        for ci, cell in enumerate(row):
                            if ci == date_col_i:
                                continue
                            if cell and AMOUNT_RE.search(str(cell).replace(",","")):
                                amounts.append((ci, clean_amount(str(cell))))

                        if not amounts:
                            continue

                        
                        desc = ""
                        for ci, cell in enumerate(row):
                            if not cell or ci == date_col_i:
                                continue
                            s = str(cell).strip()
                            if AMOUNT_RE.search(s.replace(",","")) or DATE_RE.search(s):
                                continue
                            if len(s) > len(desc):
                                desc = s

                        balance = amounts[-1][1] if amounts else 0.0
                        # debit/credit: second-to-last or before
                        
                        debit  = 0.0
                        credit = 0.0
                        if len(amounts) >= 3:
                            debit  = amounts[-3][1]
                            credit = amounts[-2][1]
                        elif len(amounts) == 2:
                            
                            t = infer_type(desc)
                            if t == "DEBIT":
                                debit = amounts[-2][1]
                            else:
                                credit = amounts[-2][1]

                        if debit > 0:
                            amount, txn_type = debit, "DEBIT"
                        elif credit > 0:
                            amount, txn_type = credit, "CREDIT"
                        else:
                            amount, txn_type = 0.0, infer_type(desc)

                        rows_out.append({
                            "DATE":        date_val,
                            "TRANSACTION": desc,
                            "AMOUNT":      amount,
                            "TYPE":        txn_type,
                            "BALANCE":     balance,
                            "Merchant":    extract_merchant(desc),
                        })
                    continue

                # ── Named header found ──
                headers = [str(h).strip().lower() if h else "" for h in table[header_idx]]

                date_i   = find_col(headers, ["valuedate","value date","date"])
                txn_i    = find_col(headers, ["narration","description","details","particulars","transaction","remarks"])
                debit_i  = find_col(headers, ["debit","withdrawal","dr","withdrawalamount"])
                credit_i = find_col(headers, ["credit","deposit","cr","depositamount"])
                amt_i    = find_col(headers, ["amount","txnamount"])
                bal_i    = find_col(headers, ["balance","closingbalance","runningbalance"])

                for row in table[header_idx + 1:]:
                    if not row or all(not v for v in row):
                        continue

                    def g(i):
                        return str(row[i]).strip() if i is not None and i < len(row) and row[i] else ""

                    raw_date = g(date_i)
                    if not raw_date or not DATE_RE.search(raw_date):
                        continue

                    txn     = g(txn_i)
                    balance = clean_amount(g(bal_i)) if bal_i is not None else 0.0
                    debit   = clean_amount(g(debit_i))  if debit_i  is not None else 0.0
                    credit  = clean_amount(g(credit_i)) if credit_i is not None else 0.0

                    if amt_i is not None:
                        amount   = clean_amount(g(amt_i))
                        txn_type = infer_type(txn)
                    elif debit > 0:
                        amount, txn_type = debit, "DEBIT"
                    elif credit > 0:
                        amount, txn_type = credit, "CREDIT"
                    else:
                        amount, txn_type = 0.0, infer_type(txn)

                    rows_out.append({
                        "DATE":        DATE_RE.search(raw_date).group() if DATE_RE.search(raw_date) else raw_date,
                        "TRANSACTION": txn,
                        "AMOUNT":      amount,
                        "TYPE":        txn_type,
                        "BALANCE":     balance,
                        "Merchant":    extract_merchant(txn),
                    })

    return pd.DataFrame(rows_out, columns=["DATE","TRANSACTION","AMOUNT","TYPE","BALANCE","Merchant"])


def parse_pdf_text(filepath: str) -> pd.DataFrame:
    """Fallback: line-by-line text extraction."""
    rows_out = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                date_match = DATE_RE.search(line)
                if not date_match:
                    continue
                amounts = AMOUNT_RE.findall(line)
                if not amounts:
                    continue
                balance = clean_amount(amounts[-1])
                amount  = clean_amount(amounts[-2]) if len(amounts) >= 2 else clean_amount(amounts[0])
                date_end = date_match.end()
                first_amt = line.find(amounts[0])
                txn = re.sub(r'\s+', ' ', line[date_end:first_amt]).strip()
                txn_type = infer_type(txn + " " + line)
                rows_out.append({
                    "DATE":        date_match.group(),
                    "TRANSACTION": txn,
                    "AMOUNT":      amount,
                    "TYPE":        txn_type,
                    "BALANCE":     balance,
                    "Merchant":    extract_merchant(txn),
                })
    return pd.DataFrame(rows_out, columns=["DATE","TRANSACTION","AMOUNT","TYPE","BALANCE","Merchant"])


def parse_pdf(filepath: str) -> pd.DataFrame:
    df = parse_pdf_table(filepath)
    if df.empty:
        print("Table extraction empty — trying text fallback")
        df = parse_pdf_text(filepath)
    if df.empty:
        print("⚠️  No transactions found in PDF")
    return df
