import cv2
import re
import pandas as pd
import logging
from paddleocr import PaddleOCR
from datetime import datetime


# HIDE PADDLE LOGS


logging.getLogger("ppocr").setLevel(logging.ERROR)


# READ IMAGE


image_path = r"C:\Users\manis\Downloads\WhatsApp Image 2026-05-23 at 4.48.26 PM.jpeg"

img = cv2.imread(image_path)

if img is None:
    print("Image not found!")
    exit()


# PREPROCESS IMAGE


img = cv2.resize(img, None, fx=2, fy=2)

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

gray = cv2.fastNlMeansDenoising(gray)

processed = cv2.adaptiveThreshold(
    gray,
    255,
    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
    cv2.THRESH_BINARY,
    31,
    11
)

processed_path = "processed.jpg"

cv2.imwrite(processed_path, processed)


# OCR


ocr = PaddleOCR(
    use_angle_cls=True,
    lang='en',
    show_log=False
)

result = ocr.ocr(processed_path)

# EXTRACT TEXT LINES


rows = []

for line in result[0]:

    text = line[1][0].strip()

    if text:
        rows.append(text)


# PARSE TRANSACTIONS


transactions = []

date_pattern = r"\d{2}[./-]\d{2}[./-]\d{2,4}"

i = 0

while i < len(rows):

    line = rows[i]

    if re.search(date_pattern, line):

        transaction = {
            "DATE": None,
            "TRANSACTION PARTICULARS": None,
            "AMOUNT": None,
            "TYPE": None,
            "BALANCE": None,
            "Merchant Name": None
        }


# DATE


        date_match = re.search(date_pattern, line)

        raw_date = date_match.group()

        raw_date = raw_date.replace(".", "/").replace("-", "/")

        try:

            if len(raw_date.split("/")[-1]) == 2:
                dt = datetime.strptime(raw_date, "%d/%m/%y")
            else:
                dt = datetime.strptime(raw_date, "%d/%m/%Y")

            formatted_date = dt.strftime("%d-%m-%Y")

        except:
            formatted_date = raw_date

        transaction["DATE"] = formatted_date


# PARTICULARS


        particulars = line.replace(
            date_match.group(),
            ""
        ).strip()

        j = i + 1

        while j < len(rows):

            next_line = rows[j]

            # stop if next transaction
            if re.search(date_pattern, next_line):
                break

            amount_match = re.search(
                r'[\d,]+\.\d{2}(Cr|Dr|cr|dr|Ct|0r)?',
                next_line
            )

            if amount_match:

                value = amount_match.group()

                clean_amount = re.sub(
                    r'[A-Za-z]',
                    '',
                    value
                )

                # CREDIT
                if any(x in value for x in ["Cr", "cr", "Ct", "0r"]):

                    if transaction["AMOUNT"] is None:
                        transaction["AMOUNT"] = clean_amount
                        transaction["TYPE"] = "CREDIT"
                    else:
                        transaction["BALANCE"] = clean_amount

                # DEBIT
                elif any(x in value for x in ["Dr", "dr"]):

                    if transaction["AMOUNT"] is None:
                        transaction["AMOUNT"] = clean_amount
                        transaction["TYPE"] = "DEBIT"
                    else:
                        transaction["BALANCE"] = clean_amount

                else:

                    if transaction["AMOUNT"] is None:
                        transaction["AMOUNT"] = clean_amount
                    else:
                        transaction["BALANCE"] = clean_amount

            else:
                particulars += " " + next_line

            j += 1

        transaction["TRANSACTION PARTICULARS"] = particulars

        transactions.append(transaction)

        i = j

    else:
        i += 1


# CREATE DATAFRAME


df = pd.DataFrame(transactions)


# CLEANING FUNCTIONS


def clean_text(text):

    if pd.isna(text):
        return text

    corrections = {
        "UP1": "UPI",
        "0R": "DR",
        "Ct": "Cr",
        "0r": "Cr",
        "PY1n": "PYTM",
        "PYIN": "PYTM",
        "P11a": "PAY",
        "TRAN3FER": "TRANSFER",
        "TRAN5FER": "TRANSFER",
        "FROK": "FROM",
        "FRSK": "FROM",
        "Carned": "Carried",
        "SHS": "SMS",
        "AMI1": "AMIT",
        "S5IN": "SBIN",
        "Sach1n": "Sachin",
        "Sach10": "Sachin"
    }

    for wrong, correct in corrections.items():
        text = text.replace(wrong, correct)

    return text

df["TRANSACTION PARTICULARS"] = df[
    "TRANSACTION PARTICULARS"
].apply(clean_text)


# DETECT TYPE


def detect_type(text):

    if pd.isna(text):
        return None

    if "/CR/" in text:
        return "CREDIT"

    elif "/DR/" in text:
        return "DEBIT"

    return None

df["TYPE"] = df[
    "TRANSACTION PARTICULARS"
].apply(detect_type)


# MERCHANT NAME


def extract_merchant(text):

    if pd.isna(text):
        return None

    match = re.search(
        r'/([A-Za-z ]{3,})/(PYTM|SBIN|OKB|PAY|UBIN|AXIS)',
        text,
        re.IGNORECASE
    )

    if match:
        return match.group(1).strip()

    return None

df["Merchant Name"] = df[
    "TRANSACTION PARTICULARS"
].apply(extract_merchant)


# CLEAN AMOUNTS


for col in ["AMOUNT", "BALANCE"]:

    df[col] = (
        df[col]
        .astype(str)
        .str.replace("Cr", "", regex=False)
        .str.replace("Dr", "", regex=False)
        .str.replace(",", "", regex=False)
    )

# =========================================
# SHOW ONLY DATAFRAME
# =========================================

print(df.to_string())


# EXPORT CSV


# df.to_csv("final_transactions.csv", index=False)