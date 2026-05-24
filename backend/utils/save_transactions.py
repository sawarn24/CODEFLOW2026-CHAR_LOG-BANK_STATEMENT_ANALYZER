import os
import pandas as pd
from datetime import datetime

MASTER_FILENAME = "transactions_master.xlsx"


def get_master_path(uid: str, upload_dir: str) -> str:
    user_dir = os.path.join(upload_dir, uid)
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, MASTER_FILENAME)


def append_transactions(df_new: pd.DataFrame, uid: str, upload_dir: str, source_file: str) -> dict:
   
    master_path = get_master_path(uid, upload_dir)

    
    df_new = df_new.copy()
    df_new["source_file"]  = source_file
    df_new["uploaded_at"]  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

   
    cols = ["DATE", "TRANSACTION", "AMOUNT", "TYPE", "BALANCE", "Merchant", "source_file", "uploaded_at"]
    for c in cols:
        if c not in df_new.columns:
            df_new[c] = ""
    df_new = df_new[cols]

    
    if os.path.exists(master_path):
        df_existing = pd.read_excel(master_path, dtype=str)
    else:
        df_existing = pd.DataFrame(columns=cols)

    total_before = len(df_existing)

    
    df_combined = pd.concat([df_existing, df_new], ignore_index=True)
    df_combined["_dedup_key"] = (
        df_combined["DATE"].astype(str).str.strip() + "|" +
        df_combined["TRANSACTION"].astype(str).str.strip() + "|" +
        df_combined["AMOUNT"].astype(str).str.strip()
    )
    df_combined = df_combined.drop_duplicates(subset="_dedup_key", keep="first")
    df_combined = df_combined.drop(columns=["_dedup_key"])

    
    try:
        df_combined["_sort_date"] = pd.to_datetime(df_combined["DATE"], dayfirst=True, errors="coerce")
        df_combined = df_combined.sort_values("_sort_date", ascending=False)
        df_combined = df_combined.drop(columns=["_sort_date"])
    except Exception:
        pass

    
    df_combined.reset_index(drop=True, inplace=True)
    df_combined.to_excel(master_path, index=False)

    new_added   = len(df_combined) - total_before
    duplicates  = len(df_new) - new_added

    return {
        "master_file":       MASTER_FILENAME,
        "master_path":       master_path,
        "total_transactions": len(df_combined),
        "new_added":         max(new_added, 0),
        "duplicates_skipped": max(duplicates, 0),
    }


def get_all_transactions(uid: str, upload_dir: str) -> pd.DataFrame:
    
    master_path = get_master_path(uid, upload_dir)
    if not os.path.exists(master_path):
        return pd.DataFrame(columns=["DATE","TRANSACTION","AMOUNT","TYPE","BALANCE","Merchant","source_file","uploaded_at"])
    return pd.read_excel(master_path, dtype=str)
