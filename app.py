import os
import re
import uuid
import logging
import io
import json
import base64
from typing import List, Dict, Any, Tuple, Optional
import polars as pl
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

# -------------------------------------------------------------
# 1. SETUP & CONFIG
# -------------------------------------------------------------
logger = logging.getLogger("dedupe_app")
logging.basicConfig(level=logging.INFO)

UPLOAD_DIR = "./uploads"
CACHE_DIR = "./cache"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# -------------------------------------------------------------
# 2. VALID NANP AREA CODES
# -------------------------------------------------------------
VALID_NANP_AREA_CODES = {
    "201", "202", "203", "204", "205", "206", "207", "208", "209", "210",
    "212", "213", "214", "215", "216", "217", "218", "219", "224", "225",
    "228", "229", "231", "234", "239", "240", "242", "246", "248", "250",
    "251", "252", "253", "254", "256", "260", "262", "264", "267", "268",
    "269", "270", "276", "281", "284", "289", "301", "302", "303", "304",
    "305", "306", "307", "308", "309", "310", "312", "313", "314", "315",
    "316", "317", "318", "319", "320", "321", "323", "325", "330", "331",
    "334", "336", "337", "339", "340", "345", "347", "351", "352", "360",
    "361", "386", "401", "402", "403", "404", "405", "406", "407", "408",
    "409", "410", "412", "413", "414", "415", "416", "417", "418", "419",
    "423", "425", "430", "432", "434", "435", "440", "441", "443", "450",
    "456", "469", "473", "478", "479", "480", "484", "501", "502", "503",
    "504", "505", "506", "507", "508", "509", "510", "512", "513", "514",
    "515", "516", "517", "518", "519", "520", "530", "540", "541", "551",
    "559", "561", "562", "563", "567", "570", "571", "573", "574", "580",
    "585", "586", "601", "602", "603", "604", "605", "606", "607", "608",
    "609", "610", "612", "613", "614", "615", "616", "617", "618", "619",
    "620", "623", "626", "630", "631", "636", "641", "646", "647", "649",
    "650", "651", "660", "661", "662", "664", "670", "671", "678", "682",
    "701", "702", "703", "704", "705", "706", "707", "708", "709", "710",
    "712", "713", "714", "715", "716", "717", "718", "719", "720", "724",
    "727", "731", "732", "734", "740", "754", "757", "758", "760", "763",
    "765", "767", "770", "772", "773", "774", "775", "778", "780", "781",
    "784", "785", "786", "787", "801", "802", "803", "804", "805", "806",
    "807", "808", "809", "810", "812", "813", "814", "815", "816", "817",
    "818", "819", "828", "830", "831", "832", "843", "845", "847", "848",
    "850", "856", "857", "858", "859", "860", "862", "863", "864", "865",
    "867", "868", "869", "870", "876", "878", "880", "881", "882", "901",
    "902", "903", "904", "905", "906", "907", "908", "909", "910", "912",
    "913", "914", "915", "916", "917", "918", "919", "920", "925", "928",
    "931", "936", "937", "939", "940", "941", "947", "949", "952", "954",
    "956", "970", "971", "972", "973", "978", "979", "980", "985", "989"
}

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "zoho.com", "protonmail.com", "yandex.com", "mail.com",
    "gmx.com", "live.com", "msn.com", "comcast.net", "sbcglobal.net",
    "bellsouth.net", "verizon.net", "cox.net", "charter.net", "att.net"
}

# -------------------------------------------------------------
# 3. CORE LOGIC & UNION-FIND
# -------------------------------------------------------------
class UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, i: int) -> int:
        path = []
        while self.parent[i] != i:
            path.append(i)
            i = self.parent[i]
        for node in path:
            self.parent[node] = i
        return i

    def union(self, i: int, j: int) -> None:
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            if self.rank[root_i] < self.rank[root_j]:
                self.parent[root_i] = root_j
            elif self.rank[root_i] > self.rank[root_j]:
                self.parent[root_j] = root_i
            else:
                self.parent[root_j] = root_i
                self.rank[root_i] += 1


def auto_map_columns(columns: List[str]) -> Dict[str, str]:
    mappings = {}
    col_lower = [c.lower().replace("_", "").replace(" ", "") for c in columns]
    
    patterns = {
        "phone": ["phone", "telephone", "mobile", "ph", "num", "contact"],
        "email": ["email", "mail", "emailaddress"],
        "website": ["website", "web", "url", "site", "link"],
        "company": ["company", "companyname", "firm", "org", "organization"],
        "first_name": ["firstname", "first", "fname"],
        "last_name": ["lastname", "last", "lname"],
        "name": ["name", "fullname", "name"]
    }
    
    for standard_name, keywords in patterns.items():
        for i, col in enumerate(columns):
            norm_col = col_lower[i]
            if any(keyword == norm_col or (len(keyword) > 3 and norm_col.startswith(keyword)) for keyword in keywords):
                if standard_name not in mappings:
                    mappings[standard_name] = col
                    break
                    
    if "first_name" not in mappings and "name" in mappings:
        mappings["first_name"] = mappings["name"]
        
    return mappings


def process_and_deduplicate(
    file_path_1: str,
    file_path_2: Optional[str] = None,
    mode: str = "internal",
    match_fields: Optional[List[str]] = None
) -> pl.DataFrame:
    if match_fields is None:
        match_fields = ["email", "phone"]

    # 1. Load dataframes
    lf1 = pl.scan_csv(file_path_1, infer_schema_length=10000, ignore_errors=True)
    df1_orig = lf1.collect()
    df1 = df1_orig.with_columns([
        pl.lit("File 1").alias("source_file"),
        pl.arange(0, df1_orig.height).alias("original_row_index")
    ])
    
    if file_path_2:
        lf2 = pl.scan_csv(file_path_2, infer_schema_length=10000, ignore_errors=True)
        df2_orig = lf2.collect()
        df2 = df2_orig.with_columns([
            pl.lit("File 2").alias("source_file"),
            pl.arange(df1.height, df1.height + df2_orig.height).alias("original_row_index")
        ])
        
        all_cols = list(set(df1.columns) | set(df2.columns))
        for col in all_cols:
            if col not in df1.columns:
                df1 = df1.with_columns(pl.lit(None).alias(col))
            if col not in df2.columns:
                df2 = df2.with_columns(pl.lit(None).alias(col))
        df1 = df1.select(all_cols)
        df2 = df2.select(all_cols)
        df = pl.concat([df1, df2])
    else:
        df = df1

    col_mapping = auto_map_columns(df.columns)
    lf = df.lazy()
    exprs = []
    
    # Phone Cleansing (conditional country code 1 stripping)
    phone_col = col_mapping.get("phone")
    if phone_col:
        clean_digits = pl.col(phone_col).fill_null("").cast(pl.Utf8).str.replace_all(r"[\+\-\(\)\.\s\[\]]", "")
        exprs.append(
            pl.when((clean_digits.str.len_chars() == 11) & clean_digits.str.starts_with("1"))
            .then(clean_digits.str.slice(1))
            .otherwise(clean_digits)
            .alias("clean_phone")
        )
    else:
        exprs.append(pl.lit("").alias("clean_phone"))
        
    # Email Cleansing
    email_col = col_mapping.get("email")
    if email_col:
        exprs.append(
            pl.col(email_col)
            .fill_null("")
            .cast(pl.Utf8)
            .str.to_lowercase()
            .str.strip_chars()
            .str.replace(r"\+[^@]*@", "@")
            .alias("clean_email")
        )
    else:
        exprs.append(pl.lit("").alias("clean_email"))

    # Website Cleansing
    web_col = col_mapping.get("website")
    if web_col:
        exprs.append(
            pl.col(web_col)
            .fill_null("")
            .cast(pl.Utf8)
            .str.to_lowercase()
            .str.strip_chars()
            .str.replace(r"^https?://", "")
            .str.replace(r"^www\.", "")
            .str.replace(r"/.*$", "")
            .alias("clean_website")
        )
    else:
        exprs.append(pl.lit("").alias("clean_website"))

    # Company Name Cleansing
    company_col = col_mapping.get("company")
    if company_col:
        exprs.append(
            pl.col(company_col)
            .fill_null("")
            .cast(pl.Utf8)
            .str.to_lowercase()
            .str.strip_chars()
            .str.replace_all(r"[.,\-_&()\[\]\"']", "")
            .str.replace_all(r"\b(llc|inc|corp|co|ltd|limited|corporation|incorporated)\b", "")
            .str.strip_chars()
            .alias("clean_company")
        )
    else:
        exprs.append(pl.lit("").alias("clean_company"))

    # Name Cleansing
    first_name_col = col_mapping.get("first_name")
    last_name_col = col_mapping.get("last_name")
    name_col = col_mapping.get("name")
    
    if first_name_col and last_name_col and first_name_col != last_name_col:
        exprs.append(
            (pl.col(first_name_col).fill_null("").cast(pl.Utf8) + pl.col(last_name_col).fill_null("").cast(pl.Utf8))
            .str.to_lowercase()
            .str.replace_all(r"\s+", "")
            .alias("clean_name")
        )
    elif name_col:
        exprs.append(
            pl.col(name_col)
            .fill_null("")
            .cast(pl.Utf8)
            .str.to_lowercase()
            .str.replace_all(r"\s+", "")
            .alias("clean_name")
        )
    else:
        exprs.append(pl.lit("").alias("clean_name"))

    lf = lf.with_columns(exprs)
    lf = lf.with_columns([
        (pl.col("clean_phone").str.len_chars() == 10).alias("is_valid_phone_length"),
        pl.col("clean_phone").str.slice(0, 3).is_in(list(VALID_NANP_AREA_CODES)).alias("is_valid_area_code"),
        pl.col("clean_email").str.replace(r"^[^@]*@", "").alias("email_domain")
    ]).with_columns([
        (pl.col("is_valid_phone_length") & pl.col("is_valid_area_code")).alias("is_valid_phone"),
        pl.col("clean_email").str.contains(r"^[^@]+@[^@]+\.[^@]+$").fill_null(False).alias("is_valid_email"),
        pl.when(pl.col("clean_website") != "")
        .then(pl.col("clean_website"))
        .otherwise(pl.col("email_domain"))
        .alias("domain_token")
    ])

    processed_df = lf.collect()
    total_rows = processed_df.height
    uf = UnionFind(total_rows)
    active_tokens = {}
    
    if "phone" in match_fields:
        active_tokens["phone"] = [(i, val) for i, val in enumerate(processed_df["clean_phone"].to_list()) if val and processed_df["is_valid_phone"][i]]
    if "email" in match_fields:
        active_tokens["email"] = [(i, val) for i, val in enumerate(processed_df["clean_email"].to_list()) if val and processed_df["is_valid_email"][i]]
    if "website" in match_fields:
        active_tokens["website"] = [(i, val) for i, val in enumerate(processed_df["clean_website"].to_list()) if val]
    if "domain" in match_fields:
        active_tokens["domain"] = [(i, val) for i, val in enumerate(processed_df["domain_token"].to_list()) if val and val not in FREE_EMAIL_DOMAINS]
    if "company" in match_fields:
        active_tokens["company"] = [(i, val) for i, val in enumerate(processed_df["clean_company"].to_list()) if val]
    if "name" in match_fields:
        active_tokens["name"] = [(i, val) for i, val in enumerate(processed_df["clean_name"].to_list()) if val]

    for field, items in active_tokens.items():
        groups = {}
        for idx, val in items:
            groups.setdefault(val, []).append(idx)
        for val, indices in groups.items():
            if len(indices) > 1:
                first = indices[0]
                for other in indices[1:]:
                    uf.union(first, other)

    root_to_indices = {}
    for i in range(total_rows):
        root = uf.find(i)
        root_to_indices.setdefault(root, []).append(i)

    cluster_ids = [None] * total_rows
    is_duplicate = [False] * total_rows
    cluster_counter = 1
    
    for root, indices in root_to_indices.items():
        if len(indices) > 1:
            file_sources = [processed_df["source_file"][idx] for idx in indices]
            if mode == "cross":
                has_file_1 = "File 1" in file_sources
                has_file_2 = "File 2" in file_sources
                if has_file_1 and has_file_2:
                    cid = f"CLUST_{cluster_counter:05d}"
                    cluster_counter += 1
                    for idx in indices:
                        cluster_ids[idx] = cid
                        if processed_df["source_file"][idx] == "File 2":
                            is_duplicate[idx] = True
            else:
                sorted_indices = sorted(indices)
                cid = f"CLUST_{cluster_counter:05d}"
                cluster_counter += 1
                for idx in sorted_indices:
                    cluster_ids[idx] = cid
                for idx in sorted_indices[1:]:
                    is_duplicate[idx] = True

    processed_df = processed_df.with_columns([
        pl.Series(cluster_ids).alias("cluster_id"),
        pl.Series(is_duplicate).alias("is_duplicate")
    ])

    if phone_col:
        is_altered_phone = []
        raw_phones = processed_df[phone_col].to_list()
        clean_phones = processed_df["clean_phone"].to_list()
        for raw, clean in zip(raw_phones, clean_phones):
            raw_digits = re.sub(r"\D", "", str(raw or ""))
            is_altered_phone.append(raw_digits != clean)
        processed_df = processed_df.with_columns(pl.Series(is_altered_phone).alias("is_altered_phone"))
    else:
        processed_df = processed_df.with_columns(pl.lit(False).alias("is_altered_phone"))

    return processed_df

# -------------------------------------------------------------
# 4. CACHE LAYER (IPC MEMORY-MAPPED FILES ON DISK)
# -------------------------------------------------------------
class FileCache:
    def _get_path(self, session_id: str) -> str:
        return os.path.join(CACHE_DIR, f"{session_id}.ipc")
    
    def _get_meta_path(self, session_id: str) -> str:
        return os.path.join(CACHE_DIR, f"{session_id}.json")

    def set_records(self, session_id: str, df: pl.DataFrame) -> None:
        df.write_ipc(self._get_path(session_id))

    def set_metadata(self, session_id: str, metadata: dict) -> None:
        with open(self._get_meta_path(session_id), "w") as f:
            json.dump(metadata, f)

    def get_metadata(self, session_id: str) -> Optional[dict]:
        path = self._get_meta_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    def get_dataframe(self, session_id: str) -> Optional[pl.DataFrame]:
        path = self._get_path(session_id)
        if not os.path.exists(path):
            return None
        return pl.read_ipc(path, memory_map=True)

    def get_page(self, session_id: str, page: int, page_size: int) -> List[Dict[str, Any]]:
        df = self.get_dataframe(session_id)
        if df is None:
            return []
        start = page * page_size
        return df.slice(start, page_size).to_dicts()

    def get_total_count(self, session_id: str) -> int:
        df = self.get_dataframe(session_id)
        return len(df) if df is not None else 0

    def get_metrics(self, session_id: str) -> Dict[str, Any]:
        df = self.get_dataframe(session_id)
        if df is None:
            return {}
        
        total_rows = len(df)
        invalid_phone_count = df.filter(~pl.col("is_valid_phone")).height if "is_valid_phone" in df.columns else 0
        invalid_email_count = df.filter(~pl.col("is_valid_email")).height if "is_valid_email" in df.columns else 0
        duplicate_count = df.filter(pl.col("is_duplicate")).height if "is_duplicate" in df.columns else 0
        
        if "cluster_id" in df.columns:
            clusters = df.filter(pl.col("cluster_id").is_not_null() & (pl.col("cluster_id") != ""))
            unique_clusters = clusters.select("cluster_id").n_unique()
        else:
            unique_clusters = 0

        return {
            "total_rows": total_rows,
            "invalid_phone_count": invalid_phone_count,
            "invalid_email_count": invalid_email_count,
            "duplicate_count": duplicate_count,
            "unique_clusters": unique_clusters
        }

    def has_session(self, session_id: str) -> bool:
        return os.path.exists(self._get_path(session_id))

cache = FileCache()

# -------------------------------------------------------------
# 5. FASTAPI APP & ENDPOINTS
# -------------------------------------------------------------
app = FastAPI(title="SaaS Data Deduplicator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        session_file_id = str(uuid.uuid4())
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".csv", ".txt"]:
            raise HTTPException(status_code=400, detail="Only CSV/TXT supported.")

        saved_path = os.path.join(UPLOAD_DIR, f"{session_file_id}{ext}")
        with open(saved_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
                
        line_count = 0
        with open(saved_path, "rb") as f:
            for _ in f:
                line_count += 1
                if line_count > 1000000:
                    break
        
        return {
            "file_id": session_file_id,
            "filename": file.filename,
            "file_path": saved_path,
            "approx_rows": max(0, line_count - 1)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/process")
async def process_dataset(
    file1_path: str = Form(...),
    file2_path: Optional[str] = Form(None),
    mode: str = Form("internal"),
    match_fields: List[str] = Form(...)
):
    try:
        session_id = str(uuid.uuid4())
        
        parsed_fields = []
        for f in match_fields:
            if "," in f:
                parsed_fields.extend([x.strip() for x in f.split(",")])
            else:
                parsed_fields.append(f.strip())
        parsed_fields = [x for x in parsed_fields if x]

        file1_cols = pl.scan_csv(file1_path).columns
        file2_cols = pl.scan_csv(file2_path).columns if file2_path else []

        processed_df = process_and_deduplicate(
            file_path_1=file1_path,
            file_path_2=file2_path,
            mode=mode,
            match_fields=parsed_fields
        )

        cache.set_records(session_id, processed_df)
        cache.set_metadata(session_id, {
            "mode": mode,
            "file1_cols": file1_cols,
            "file2_cols": file2_cols
        })

        metrics = cache.get_metrics(session_id)
        mappings = auto_map_columns(processed_df.columns)

        return {
            "session_id": session_id,
            "metrics": metrics,
            "column_mappings": mappings,
            "columns": processed_df.columns
        }
    except Exception as e:
        logger.error(f"Process failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/records")
async def get_records(
    session_id: str = Query(...),
    page: int = Query(0),
    limit: int = Query(100)
):
    if not cache.has_session(session_id):
        raise HTTPException(status_code=404, detail="Session expired.")
    records = cache.get_page(session_id, page, limit)
    total_records = cache.get_total_count(session_id)
    return {
        "records": records,
        "total_records": total_records,
        "page": page,
        "limit": limit
    }


def stream_df(df: pl.DataFrame, filename: str, format: str) -> StreamingResponse:
    if format == "xlsx":
        buffer = io.BytesIO()
        df.write_excel(buffer)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}.xlsx"}
        )
    else:
        buffer = io.BytesIO()
        df.write_csv(buffer)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}.csv"}
        )


@app.get("/api/export/clean")
async def export_clean_file(
    session_id: str = Query(...),
    format: str = Query("csv"),
    selection_mode: str = Query("keep_oldest"),
    exclude_ids: Optional[str] = Query(None)
):
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired.")

        meta = cache.get_metadata(session_id) or {}
        session_mode = meta.get("mode", "internal")
        file2_cols = meta.get("file2_cols", [])

        if selection_mode == "keep_all":
            filtered_df = df
        elif selection_mode == "custom" and exclude_ids:
            exclude_list = [int(x) for x in exclude_ids.split(",") if x.strip().isdigit()]
            filtered_df = df.filter(~pl.col("original_row_index").is_in(exclude_list))
        elif selection_mode == "keep_newest":
            if "cluster_id" in df.columns:
                clustered = df.filter(pl.col("cluster_id").is_not_null())
                non_clustered = df.filter(pl.col("cluster_id").is_null())
                kept_clustered = clustered.sort("original_row_index", descending=True).unique(subset=["cluster_id"], keep="first")
                filtered_df = pl.concat([non_clustered, kept_clustered]).sort("original_row_index")
            else:
                filtered_df = df
        else: # keep_oldest
            filtered_df = df.filter(~pl.col("is_duplicate"))

        if session_mode == "cross":
            filtered_df = filtered_df.filter(pl.col("source_file") == "File 2")
            export_cols = [c for c in file2_cols if c in filtered_df.columns]
        else:
            export_cols = [c for c in filtered_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code", "is_altered_phone", "is_valid_phone", "is_valid_email", "cluster_id", "is_duplicate", "original_row_index", "source_file"]]

        export_df = filtered_df.select(export_cols)
        return stream_df(export_df, f"cleaned_dataset_{session_id}", format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/duplicates")
async def export_duplicates_file(
    session_id: str = Query(...),
    format: str = Query("csv"),
    selection_mode: str = Query("keep_oldest"),
    exclude_ids: Optional[str] = Query(None)
):
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired.")

        meta = cache.get_metadata(session_id) or {}
        session_mode = meta.get("mode", "internal")
        file2_cols = meta.get("file2_cols", [])

        if selection_mode == "keep_all":
            excluded_df = df.filter(pl.lit(False))
        elif selection_mode == "custom" and exclude_ids:
            exclude_list = [int(x) for x in exclude_ids.split(",") if x.strip().isdigit()]
            excluded_df = df.filter(pl.col("original_row_index").is_in(exclude_list))
        elif selection_mode == "keep_newest":
            if "cluster_id" in df.columns:
                clustered = df.filter(pl.col("cluster_id").is_not_null())
                kept_indices = clustered.sort("original_row_index", descending=True).unique(subset=["cluster_id"], keep="first")["original_row_index"].to_list()
                excluded_df = clustered.filter(~pl.col("original_row_index").is_in(kept_indices)).sort("original_row_index")
            else:
                excluded_df = df.filter(pl.lit(False))
        else: # keep_oldest
            excluded_df = df.filter(pl.col("is_duplicate"))

        if session_mode == "cross":
            excluded_df = excluded_df.filter(pl.col("source_file") == "File 2")
            qa_cols = [c for c in file2_cols if c in excluded_df.columns] + ["cluster_id", "original_row_index", "source_file"]
        else:
            qa_cols = [c for c in excluded_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code", "is_altered_phone", "is_valid_phone", "is_valid_email"]]
        
        qa_cols = [c for c in qa_cols if c in excluded_df.columns]
        qa_df = excluded_df.select(qa_cols)
        return stream_df(qa_df, f"removed_duplicates_{session_id}", format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/invalid")
async def export_invalid_file(session_id: str = Query(...), format: str = Query("csv")):
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired.")
        invalid_df = df.filter(~pl.col("is_valid_phone") | ~pl.col("is_valid_email"))
        cols = [c for c in invalid_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code"]]
        return stream_df(invalid_df.select(cols), f"invalid_formats_{session_id}", format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/clusters")
async def export_clusters_file(session_id: str = Query(...), format: str = Query("csv")):
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired.")
        clustered_df = df.filter(pl.col("cluster_id").is_not_null()).sort("cluster_id")
        cols = [c for c in clustered_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code"]]
        return stream_df(clustered_df.select(cols), f"duplicate_clusters_{session_id}", format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/all")
async def export_all_file(session_id: str = Query(...), format: str = Query("csv")):
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired.")
        return stream_df(df, f"full_processed_dataset_{session_id}", format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------------
# 6. FRONTEND SINGLE-FILE SERVER ROUTE
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DedupeFlow SaaS</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
      tailwind.config = {
        theme: {
          extend: {
            fontSize: {
              'xxs': '0.7rem',
            }
          }
        }
      }
    </script>
    <!-- Google Fonts Outfit & Inter -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@600;700;900&display=swap" rel="stylesheet">
    <style>
      body {
        font-family: 'Inter', sans-serif;
      }
      .font-display {
        font-family: 'Outfit', sans-serif;
      }
      .bg-grid-glow {
        background-color: #050814;
        background-image: 
          radial-gradient(at 0% 0%, rgba(79, 70, 229, 0.1) 0px, transparent 50%),
          radial-gradient(at 100% 100%, rgba(236, 72, 153, 0.08) 0px, transparent 50%);
      }
      .glass-panel {
        background: rgba(13, 20, 38, 0.6);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.05);
      }
      .glow-btn {
        transition: all 0.3s ease;
      }
      .glow-btn:hover:not(:disabled) {
        box-shadow: 0 0 20px rgba(99, 102, 241, 0.4);
        transform: translateY(-1px);
      }
      /* Custom Scrollbar */
      ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
      }
      ::-webkit-scrollbar-track {
        background: #080d1a;
      }
      ::-webkit-scrollbar-thumb {
        background: #1e293b;
        border-radius: 4px;
      }
      ::-webkit-scrollbar-thumb:hover {
        background: #334155;
      }
    </style>
    <!-- React and Babel CDNs -->
    <script src="https://unpkg.com/react@18/umd/react.production.min.js" crossorigin></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js" crossorigin></script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
    <!-- Lucide Icons -->
    <script src="https://unpkg.com/lucide@latest"></script>
</head>
<body class="bg-grid-glow min-h-screen text-slate-200 pb-20 px-6 sm:px-12">
    <div id="root"></div>

    <script type="text/babel">
      const { useState, useEffect, useMemo, useRef } = React;

      // Simple wrapper components for Lucide icons using global lucide object
      function Icon({ name, className = "w-5 h-5", ...props }) {
        useEffect(() => {
          if (window.lucide) {
            window.lucide.createIcons();
          }
        }, [name]);
        return <i data-lucide={name} className={className} {...props}></i>;
      }

      function App() {
        // Files State
        const [file1, setFile1] = useState(null);
        const [file2, setFile2] = useState(null);
        const [isUploading1, setIsUploading1] = useState(false);
        const [isUploading2, setIsUploading2] = useState(false);
        const [uploadError, setUploadError] = useState(null);

        // Configuration State
        const [mode, setMode] = useState("internal");
        const [selectedFields, setSelectedFields] = useState(["email", "phone"]);
        const [isProcessing, setIsProcessing] = useState(false);
        
        // Results State
        const [processResult, setProcessResult] = useState(null);
        const [records, setRecords] = useState([]);
        const [totalRecordsCount, setTotalRecordsCount] = useState(0);
        const [currentPage, setCurrentPage] = useState(0);
        const [selectedExportMode, setSelectedExportMode] = useState("keep_oldest");
        const [customExcludes, setCustomExcludes] = useState(new Set());
        const [exportFormat, setExportFormat] = useState("csv");
        const [isDownloading, setIsDownloading] = useState(false);

        const fileInputRef1 = useRef(null);
        const fileInputRef2 = useRef(null);

        const pageSize = 100;

        // Reset state on file/mode change
        useEffect(() => {
          setProcessResult(null);
          setRecords([]);
          setCustomExcludes(new Set());
          setCurrentPage(0);
        }, [file1, file2, mode]);

        const handleFileUpload = async (e, fileNum) => {
          const file = e.target.files?.[0];
          if (!file) return;

          if (fileNum === 1) setIsUploading1(true);
          else setIsUploading2(true);
          setUploadError(null);

          const formData = new FormData();
          formData.append("file", file);

          try {
            const response = await fetch("/api/upload", {
              method: "POST",
              body: formData,
            });
            if (!response.ok) {
              const err = await response.json();
              throw new Error(err.detail || "Upload failed");
            }
            const data = await response.json();
            if (fileNum === 1) setFile1(data);
            else setFile2(data);
          } catch (err) {
            setUploadError(err.message || "File upload failed.");
          } finally {
            setIsUploading1(false);
            setIsUploading2(false);
          }
        };

        const toggleField = (field) => {
          setSelectedFields(prev => 
            prev.includes(field) ? prev.filter(f => f !== field) : [...prev, field]
          );
        };

        const handleProcess = async () => {
          if (!file1) return;
          setIsProcessing(true);
          setRecords([]);
          setCustomExcludes(new Set());
          setCurrentPage(0);

          const formData = new FormData();
          formData.append("file1_path", file1.file_path);
          if (file2) {
            formData.append("file2_path", file2.file_path);
          }
          formData.append("mode", mode);
          selectedFields.forEach(f => formData.append("match_fields", f));

          try {
            const response = await fetch("/api/process", {
              method: "POST",
              body: formData,
            });
            if (!response.ok) {
              const err = await response.json();
              throw new Error(err.detail || "Processing failed");
            }
            const data = await response.json();
            setProcessResult(data);
            setTotalRecordsCount(data.metrics.total_rows);
            // Fetch first page
            fetchPage(data.session_id, 0);
          } catch (err) {
            alert(err.message || "An error occurred.");
          } finally {
            setIsProcessing(false);
          }
        };

        const fetchPage = async (sessionId, pageNum) => {
          try {
            const response = await fetch(`/api/records?session_id=${sessionId}&page=${pageNum}&limit=${pageSize}`);
            if (!response.ok) throw new Error("Failed to load records");
            const data = await response.json();
            setRecords(data.records);
            setCurrentPage(pageNum);
          } catch (err) {
            console.error(err);
          }
        };

        const handleToggleExclude = (origIndex) => {
          setCustomExcludes(prev => {
            const next = new Set(prev);
            if (next.has(origIndex)) next.delete(origIndex);
            else next.add(origIndex);
            return next;
          });
        };

        const handleDownload = async (type) => {
          if (!processResult) return;
          setIsDownloading(true);
          try {
            let url = `/api/export/${type}?session_id=${processResult.session_id}&format=${exportFormat}&selection_mode=${selectedExportMode}`;
            if (selectedExportMode === "custom" && customExcludes.size > 0) {
              url += `&exclude_ids=${Array.from(customExcludes).join(",")}`;
            }
            const response = await fetch(url);
            if (!response.ok) throw new Error("Download failed");
            const blob = await response.blob();
            const downloadUrl = window.URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = downloadUrl;
            link.download = type === "clean" 
              ? `cleaned_dataset.${exportFormat}` 
              : `removed_duplicates_qa.${exportFormat}`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
          } catch (err) {
            alert("Download failed.");
          } finally {
            setIsDownloading(false);
          }
        };

        const handleDashboardDownload = async (type) => {
          if (!processResult) return;
          setIsDownloading(true);
          try {
            const response = await fetch(`/api/export/${type}?session_id=${processResult.session_id}&format=${exportFormat}`);
            if (!response.ok) throw new Error("Download failed");
            const blob = await response.blob();
            const downloadUrl = window.URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = downloadUrl;
            link.download = `${type}_records_${processResult.session_id}.${exportFormat}`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
          } catch (err) {
            alert("Download failed.");
          } finally {
            setIsDownloading(false);
          }
        };

        const tableHeaders = useMemo(() => {
          if (!processResult) return [];
          const mappings = processResult.column_mappings;
          const headers = ["source_file", "cluster_id", "Deduplication State"];
          
          processResult.columns.forEach(col => {
            if (["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code", "is_altered_phone", "is_valid_phone", "is_valid_email", "cluster_id", "is_duplicate", "original_row_index", "source_file"].includes(col)) {
              return;
            }
            headers.push(col);
            if (col === mappings["phone"]) headers.push("clean_phone");
            else if (col === mappings["email"]) headers.push("clean_email");
            else if (col === mappings["website"]) headers.push("clean_website");
            else if (col === mappings["company"]) headers.push("clean_company");
          });
          return headers;
        }, [processResult]);

        const totalPages = Math.ceil(totalRecordsCount / pageSize);

        return (
          <div className="max-w-7xl mx-auto space-y-10">
            {/* Header */}
            <header className="py-8 flex flex-col md:flex-row justify-between items-center border-b border-slate-800/80 mb-12">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-tr from-indigo-500 to-pink-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
                  <Icon name="layers" className="w-6 h-6 text-white" />
                </div>
                <div>
                  <h1 className="text-2xl font-bold tracking-tight text-white display font-display">Dedupe<span className="text-indigo-400">Flow</span></h1>
                  <p className="text-xs text-slate-400">Enterprise High-Throughput Cleansing & Duplicate Matrix</p>
                </div>
              </div>
              <div className="mt-4 md:mt-0 flex gap-3 text-xs bg-slate-900/60 p-1.5 rounded-lg border border-slate-800/50">
                <div className="px-3 py-1 rounded bg-indigo-500/10 text-indigo-400 font-medium">Polars Engine v0.20</div>
                <div className="px-3 py-1 rounded bg-emerald-500/10 text-emerald-400 font-medium">Memory Optimized</div>
              </div>
            </header>

            {/* Step 1: Upload Zones */}
            <section className="grid grid-cols-1 lg:grid-cols-2 gap-8">
              {/* File 1 Card */}
              <div className="glass-panel rounded-2xl p-6 relative overflow-hidden">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-semibold uppercase tracking-wider text-indigo-400">Master / Baseline</span>
                  <span className="text-xs text-slate-400">Required</span>
                </div>
                <h2 className="text-lg font-bold text-white mb-2">File 1: Primary Dataset</h2>
                <p className="text-xs text-slate-400 mb-6">This file acts as the primary reference database. In cross-deduplication, this dataset remains unmodified.</p>
                
                {file1 ? (
                  <div className="p-4 rounded-xl bg-indigo-950/20 border border-indigo-500/20 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded bg-indigo-500/10 text-indigo-400">
                        <Icon name="file-text" className="w-5 h-5" />
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-white truncate max-w-[200px]">{file1.filename}</p>
                        <p className="text-xs text-slate-400">~{file1.approx_rows.toLocaleString()} records detected</p>
                      </div>
                    </div>
                    <button onClick={() => setFile1(null)} className="p-2 rounded-lg text-slate-400 hover:text-red-400 transition-colors">
                      <Icon name="trash-2" className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <div 
                    onClick={() => fileInputRef1.current?.click()}
                    className="border-2 border-dashed border-slate-800 hover:border-indigo-500/50 rounded-xl p-8 text-center cursor-pointer transition-all hover:bg-slate-900/20 group"
                  >
                    <input 
                      type="file" 
                      ref={fileInputRef1} 
                      onChange={(e) => handleFileUpload(e, 1)} 
                      className="hidden" 
                      accept=".csv,.txt"
                    />
                    {isUploading1 ? (
                      <div className="flex flex-col items-center gap-3 py-4">
                        <Icon name="refresh-cw" className="w-8 h-8 text-indigo-400 animate-spin" />
                        <p className="text-sm font-medium text-slate-300">Chunking file uploads to disk...</p>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center gap-3">
                        <div className="w-12 h-12 rounded-xl bg-slate-900 flex items-center justify-center text-slate-400 group-hover:text-indigo-400 transition-colors">
                          <Icon name="upload" className="w-5 h-5" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-slate-200">Click to upload or drag & drop</p>
                          <p className="text-xs text-slate-400 mt-1">Accepts CSV or TXT up to 500MB</p>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* File 2 Card */}
              <div className="glass-panel rounded-2xl p-6 relative overflow-hidden">
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-semibold uppercase tracking-wider text-pink-400">Target / Secondary</span>
                  <span className="text-xs text-slate-500">Optional</span>
                </div>
                <h2 className="text-lg font-bold text-white mb-2">File 2: Comparison Dataset</h2>
                <p className="text-xs text-slate-400 mb-6">Compare duplicates against File 1. Useful for scrubbing new lead lists before importing to systems.</p>
                
                {file2 ? (
                  <div className="p-4 rounded-xl bg-pink-950/20 border border-pink-500/20 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded bg-pink-500/10 text-pink-400">
                        <Icon name="file-text" className="w-5 h-5" />
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-white truncate max-w-[200px]">{file2.filename}</p>
                        <p className="text-xs text-slate-400">~{file2.approx_rows.toLocaleString()} records detected</p>
                      </div>
                    </div>
                    <button onClick={() => setFile2(null)} className="p-2 rounded-lg text-slate-400 hover:text-red-400 transition-colors">
                      <Icon name="trash-2" className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <div 
                    onClick={() => fileInputRef2.current?.click()}
                    className="border-2 border-dashed border-slate-800 hover:border-pink-500/50 rounded-xl p-8 text-center cursor-pointer transition-all hover:bg-slate-900/20 group"
                  >
                    <input 
                      type="file" 
                      ref={fileInputRef2} 
                      onChange={(e) => handleFileUpload(e, 2)} 
                      className="hidden" 
                      accept=".csv,.txt"
                    />
                    {isUploading2 ? (
                      <div className="flex flex-col items-center gap-3 py-4">
                        <Icon name="refresh-cw" className="w-8 h-8 text-pink-400 animate-spin" />
                        <p className="text-sm font-medium text-slate-300">Chunking file uploads to disk...</p>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center gap-3">
                        <div className="w-12 h-12 rounded-xl bg-slate-900 flex items-center justify-center text-slate-400 group-hover:text-pink-400 transition-colors">
                          <Icon name="upload" className="w-5 h-5" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-slate-200">Click to upload or drag & drop</p>
                          <p className="text-xs text-slate-400 mt-1">Accepts CSV or TXT up to 500MB</p>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </section>

            {uploadError && (
              <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center gap-3 text-red-400 text-sm">
                <Icon name="alert-circle" className="w-5 h-5 shrink-0" />
                <p>{uploadError}</p>
              </div>
            )}

            {/* Step 2: Settings Controls */}
            {file1 && (
              <section className="glass-panel rounded-2xl p-6 sm:p-8 space-y-6">
                <div className="flex items-center gap-3 mb-2">
                  <Icon name="settings" className="w-5 h-5 text-indigo-400" />
                  <h2 className="text-lg font-bold text-white font-display">Configure Cleansing & Matching Matrix</h2>
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                  {/* Combination Mode */}
                  <div className="space-y-3">
                    <label className="text-sm font-semibold text-slate-300">Deduplication Mode</label>
                    <div className="grid grid-cols-2 gap-4">
                      <div 
                        onClick={() => setMode("internal")}
                        className={`p-4 rounded-xl border cursor-pointer transition-all ${mode === "internal" ? "bg-indigo-500/10 border-indigo-500 text-white shadow-lg shadow-indigo-500/5" : "border-slate-800 hover:border-slate-700 text-slate-400"}`}
                      >
                        <p className="font-bold text-sm">Internal Deduplication</p>
                        <p className="text-xxs text-slate-400 mt-1">Check duplicates independently within the uploaded file(s).</p>
                      </div>
                      <div 
                        onClick={() => {
                          if (!file2) {
                            alert("Please upload File 2 to use Cross-File mode.");
                            return;
                          }
                          setMode("cross");
                        }}
                        className={`p-4 rounded-xl border cursor-pointer transition-all ${!file2 ? "opacity-50 cursor-not-allowed" : ""} ${mode === "cross" ? "bg-pink-500/10 border-pink-500 text-white shadow-lg shadow-pink-500/5" : "border-slate-800 hover:border-slate-700 text-slate-400"}`}
                      >
                        <p className="font-bold text-sm">Cross-File Deduplication</p>
                        <p className="text-xxs text-slate-400 mt-1">Identify records in File 2 that already exist in File 1. File 1 remains untouched.</p>
                      </div>
                    </div>
                  </div>

                  {/* Match Fields Checkbox matrix */}
                  <div className="space-y-3">
                    <label className="text-sm font-semibold text-slate-300">Identify Duplicates Using (Match Tokens):</label>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                      {[
                        { id: "email", label: "Email Address" },
                        { id: "phone", label: "Phone Number" },
                        { id: "website", label: "Website URL" },
                        { id: "domain", label: "Domain Name" },
                        { id: "company", label: "Company Name" },
                        { id: "name", label: "Full Name" }
                      ].map(field => (
                        <div 
                          key={field.id}
                          onClick={() => toggleField(field.id)}
                          className={`p-3 rounded-lg border text-xs font-semibold flex items-center justify-between cursor-pointer transition-all ${selectedFields.includes(field.id) ? "bg-indigo-500/10 border-indigo-500/60 text-white" : "border-slate-800 text-slate-400 hover:border-slate-700"}`}
                        >
                          <span>{field.label}</span>
                          <Icon name="check-square" className={`w-4 h-4 shrink-0 transition-opacity ${selectedFields.includes(field.id) ? "opacity-100 text-indigo-400" : "opacity-30"}`} />
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="flex justify-end pt-4 border-t border-slate-800/80">
                  <button 
                    onClick={handleProcess}
                    disabled={isProcessing || selectedFields.length === 0}
                    className="glow-btn bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-600 hover:to-indigo-700 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed text-white font-semibold py-3 px-8 rounded-xl shadow-lg shadow-indigo-500/20 text-sm flex items-center gap-2"
                  >
                    {isProcessing ? (
                      <>
                        <Icon name="refresh-cw" className="w-4 h-4 animate-spin" />
                        Executing Parallel Cleansing Pipeline...
                      </>
                    ) : (
                      <>
                        <Icon name="settings" className="w-4 h-4" />
                        Clean & Process Data Matrix
                      </>
                    )}
                  </button>
                </div>
              </section>
            )}

            {/* Step 3: Metrics Dashboard & Export checkout */}
            {processResult && (
              <>
                <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                  <div 
                    onClick={() => handleDashboardDownload("all")}
                    className="glass-panel rounded-2xl p-5 flex items-center gap-4 cursor-pointer hover:border-indigo-500/40 hover:shadow-lg hover:shadow-indigo-500/5 transition-all group relative"
                    title="Click to download all processed records"
                  >
                    <div className="p-3.5 rounded-xl bg-slate-900 text-indigo-400 group-hover:bg-indigo-500/10 transition-colors">
                      <Icon name="file-text" className="w-6 h-6" />
                    </div>
                    <div>
                      <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                        Total Processed <Icon name="download" className="w-3 h-3 text-slate-500 group-hover:text-indigo-400 transition-colors" />
                      </p>
                      <p className="text-2xl font-black text-white mt-1 display font-display">
                        {processResult.metrics.total_rows.toLocaleString()}
                      </p>
                    </div>
                  </div>

                  <div 
                    onClick={() => handleDashboardDownload("invalid")}
                    className="glass-panel rounded-2xl p-5 flex items-center gap-4 cursor-pointer hover:border-amber-500/40 hover:shadow-lg hover:shadow-amber-500/5 transition-all group relative"
                    title="Click to download invalid records"
                  >
                    <div className="p-3.5 rounded-xl bg-slate-900 text-amber-400 group-hover:bg-amber-500/10 transition-colors">
                      <Icon name="alert-triangle" className="w-6 h-6" />
                    </div>
                    <div>
                      <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                        Invalid Formats <Icon name="download" className="w-3 h-3 text-slate-500 group-hover:text-amber-400 transition-colors" />
                      </p>
                      <p className="text-2xl font-black text-white mt-1 display font-display">
                        {(processResult.metrics.invalid_phone_count + processResult.metrics.invalid_email_count).toLocaleString()}
                      </p>
                    </div>
                  </div>

                  <div 
                    onClick={() => handleDashboardDownload("duplicates")}
                    className="glass-panel rounded-2xl p-5 flex items-center gap-4 cursor-pointer hover:border-rose-500/40 hover:shadow-lg hover:shadow-rose-500/5 transition-all group relative"
                    title="Click to download all duplicate records"
                  >
                    <div className="p-3.5 rounded-xl bg-slate-900 text-rose-400 group-hover:bg-rose-500/10 transition-colors">
                      <Icon name="trash-2" className="w-6 h-6" />
                    </div>
                    <div>
                      <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                        Duplicate Records <Icon name="download" className="w-3 h-3 text-slate-500 group-hover:text-rose-400 transition-colors" />
                      </p>
                      <p className="text-2xl font-black text-white mt-1 display font-display">
                        {processResult.metrics.duplicate_count.toLocaleString()}
                      </p>
                    </div>
                  </div>

                  <div 
                    onClick={() => handleDashboardDownload("clusters")}
                    className="glass-panel rounded-2xl p-5 flex items-center gap-4 cursor-pointer hover:border-purple-500/40 hover:shadow-lg hover:shadow-purple-500/5 transition-all group relative"
                    title="Click to download sorted duplicate clusters"
                  >
                    <div className="p-3.5 rounded-xl bg-slate-900 text-purple-400 group-hover:bg-purple-500/10 transition-colors">
                      <Icon name="layers" className="w-6 h-6" />
                    </div>
                    <div>
                      <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                        Duplicate Clusters <Icon name="download" className="w-3 h-3 text-slate-500 group-hover:text-purple-400 transition-colors" />
                      </p>
                      <p className="text-2xl font-black text-white mt-1 display font-display">
                        {processResult.metrics.unique_clusters.toLocaleString()}
                      </p>
                    </div>
                  </div>
                </section>

                {/* Explanation Guide */}
                <div className="glass-panel rounded-2xl p-5 border border-slate-800/80 bg-slate-950/20 text-xs text-slate-400 space-y-3 shadow-sm">
                  <h4 className="font-bold text-white flex items-center gap-1.5"><Icon name="info" className="w-4 h-4 text-indigo-400" /> Cleansing & Deduplication Metrics Explanation</h4>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div>
                      <p className="font-semibold text-slate-200">1. Duplicate Record vs Duplicate Cluster</p>
                      <p className="mt-1 leading-relaxed">
                        A <strong>Duplicate Record</strong> is any redundant row that matches a master baseline record. 
                        A <strong>Duplicate Cluster</strong> is the parent group of these matching duplicates. For example, 
                        if 3 rows contain the same email, they form <strong>1 Cluster</strong> containing <strong>1 Master</strong> (which is kept) and <strong>2 Duplicate Records</strong> (which are removed).
                      </p>
                    </div>
                    <div>
                      <p className="font-semibold text-slate-200">2. Invalid Formats</p>
                      <p className="mt-1 leading-relaxed">
                        Rows containing phone numbers or email addresses that fail format rules. This includes phone numbers with invalid NANP area codes (e.g. starting with 0/1 or having 9 as the middle digit) or wrong digit counts, and malformed emails.
                      </p>
                    </div>
                    <div>
                      <p className="font-semibold text-slate-200">3. Interactive Downloads</p>
                      <p className="mt-1 leading-relaxed">
                        All dashboard boxes above are <strong>fully interactive</strong>! Click on any metric card to directly download a filtered CSV report of those specific items (e.g. click "Invalid Formats" to download the error list).
                      </p>
                    </div>
                  </div>
                </div>

                {/* Export & Checkout Panel */}
                <section className="glass-panel rounded-2xl p-6 sm:p-8 space-y-6 border border-indigo-500/20 shadow-xl shadow-indigo-500/5">
                  <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
                    <div>
                      <h2 className="text-lg font-bold text-white font-display">Checkout & Export Dataset</h2>
                      <p className="text-xs text-slate-400 mt-1">Configure selection criteria to resolve clusters and download final sheets.</p>
                    </div>

                    <div className="flex flex-wrap items-center gap-4 text-xs font-semibold">
                      {/* Export format Selection */}
                      <div className="flex items-center bg-slate-900/60 p-1 rounded-lg border border-slate-800/80">
                        <button 
                          onClick={() => setExportFormat("csv")} 
                          className={`px-3 py-1.5 rounded-md ${exportFormat === "csv" ? "bg-slate-800 text-white" : "text-slate-400"}`}
                        >CSV</button>
                        <button 
                          onClick={() => setExportFormat("xlsx")} 
                          className={`px-3 py-1.5 rounded-md ${exportFormat === "xlsx" ? "bg-slate-800 text-white" : "text-slate-400"}`}
                        >EXCEL</button>
                      </div>

                      {/* Duplicate cluster Resolution */}
                      <div className="flex items-center bg-slate-900/60 p-1 rounded-lg border border-slate-800/80">
                        <button 
                          onClick={() => setSelectedExportMode("keep_oldest")} 
                          className={`px-3 py-1.5 rounded-md ${selectedExportMode === "keep_oldest" ? "bg-slate-800 text-white" : "text-slate-400"}`}
                          title="Keep first row in each cluster and drop the rest"
                        >Keep Oldest</button>
                        <button 
                          onClick={() => setSelectedExportMode("keep_newest")} 
                          className={`px-3 py-1.5 rounded-md ${selectedExportMode === "keep_newest" ? "bg-slate-800 text-white" : "text-slate-400"}`}
                          title="Keep last row in each cluster and drop the rest"
                        >Keep Newest</button>
                        <button 
                          onClick={() => setSelectedExportMode("custom")} 
                          className={`px-3 py-1.5 rounded-md ${selectedExportMode === "custom" ? "bg-slate-800 text-white" : "text-slate-400"}`}
                          title="Manually select duplicates via table checkmarks"
                        >Manual Checkout</button>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col sm:flex-row justify-between gap-4 pt-6 border-t border-slate-800/80">
                    <div className="text-xs text-slate-400 flex items-center gap-2">
                      <Icon name="info" className="w-4 h-4 text-indigo-400 shrink-0" />
                      {selectedExportMode === "custom" ? (
                        <span>Manual selection active: <strong>{customExcludes.size}</strong> duplicates will be dropped in final file.</span>
                      ) : (
                        <span>Resolution set to <strong>{selectedExportMode === "keep_oldest" ? "Keep Oldest" : "Keep Newest"}</strong>. Filter runs automatically on download.</span>
                      )}
                    </div>

                    <div className="flex gap-4">
                      <button 
                        onClick={() => handleDownload("duplicates")}
                        disabled={isDownloading}
                        className="flex items-center gap-2 bg-slate-900 hover:bg-slate-800 text-slate-200 border border-slate-800 font-semibold py-2.5 px-5 rounded-xl text-xs transition-colors"
                      >
                        <Icon name="download" className="w-4 h-4" />
                        Download Removed Duplicates (QA Audit Sheet)
                      </button>

                      <button 
                        onClick={() => handleDownload("clean")}
                        disabled={isDownloading}
                        className="glow-btn flex items-center gap-2 bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-600 hover:to-indigo-700 text-white font-semibold py-2.5 px-6 rounded-xl text-xs shadow-lg shadow-indigo-500/10"
                      >
                        <Icon name="download" className="w-4 h-4" />
                        Download Cleaned File
                      </button>
                    </div>
                  </div>
                </section>

                {/* Grid View */}
                <section className="glass-panel rounded-2xl overflow-hidden border border-slate-800/80">
                  <div className="p-5 border-b border-slate-800/80 flex justify-between items-center bg-slate-900/10">
                    <div>
                      <h3 className="text-base font-bold text-white font-display">Live Data Grid</h3>
                      <p className="text-xs text-slate-400 mt-0.5">Showing original raw fields side-by-side with pipeline transformations.</p>
                    </div>
                    {selectedExportMode === "custom" && (
                      <span className="text-xxs px-2.5 py-1 rounded bg-yellow-500/10 border border-yellow-500/20 text-yellow-400 font-medium">
                        Checkout Mode: Check records to REMOVE/DROP
                      </span>
                    )}
                  </div>

                  <div className="overflow-x-auto bg-[#0c101d]">
                    <table className="w-full border-collapse text-left text-xs min-w-full">
                      <thead className="bg-[#0e1324] text-slate-400 font-semibold border-b border-slate-800/80 sticky top-0">
                        <tr>
                          {selectedExportMode === "custom" && (
                            <th className="px-4 py-3.5 w-12 text-center bg-[#0e1324]">Drop</th>
                          )}
                          {tableHeaders.map((header) => (
                            <th key={header} className="px-4 py-3.5 font-medium whitespace-nowrap capitalize">
                              {header.replace(/_/g, " ")}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {records.map((record, rIdx) => {
                          const isOdd = rIdx % 2 === 1;
                          const isDuplicate = record.is_duplicate;
                          const isAlteredPhone = record.is_altered_phone;
                          const isValidPhone = record.is_valid_phone;
                          const isValidEmail = record.is_valid_email;
                          const clusterId = record.cluster_id;
                          const originalRowIndex = record.original_row_index;

                          let rowBg = isOdd ? "bg-slate-900/20" : "bg-transparent";
                          if (isDuplicate) rowBg = "bg-rose-500/5 hover:bg-rose-500/10";
                          else if (clusterId) rowBg = "bg-indigo-500/5 hover:bg-indigo-500/10";

                          return (
                            <tr key={rIdx} className={`border-b border-slate-900/60 transition-colors ${rowBg}`}>
                              {selectedExportMode === "custom" && (
                                <td className="px-4 py-3 w-12 text-center">
                                  <input
                                    type="checkbox"
                                    checked={customExcludes.has(originalRowIndex) || isDuplicate}
                                    onChange={() => handleToggleExclude(originalRowIndex)}
                                    className="rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 w-4 h-4 cursor-pointer"
                                  />
                                </td>
                              )}

                              {tableHeaders.map((header) => {
                                let cellVal = record[header];
                                let content = <span className="text-slate-300">{String(cellVal !== null ? cellVal : "")}</span>;

                                if (header === "source_file") {
                                  const isFile1 = cellVal === "File 1";
                                  content = (
                                    <span className={`px-2 py-0.5 rounded text-xxs font-semibold ${isFile1 ? "bg-indigo-500/10 text-indigo-400 border border-indigo-500/10" : "bg-pink-500/10 text-pink-400 border border-pink-500/10"}`}>
                                      {cellVal}
                                    </span>
                                  );
                                } else if (header === "cluster_id") {
                                  content = cellVal ? (
                                    <span className="font-semibold text-xxs text-indigo-300 tracking-wider font-mono bg-indigo-500/10 px-1.5 py-0.5 rounded">
                                      {cellVal}
                                    </span>
                                  ) : (
                                    <span className="text-slate-600">-</span>
                                  );
                                } else if (header === "Deduplication State") {
                                  if (isDuplicate) {
                                    content = (
                                      <span className="px-2 py-0.5 rounded text-xxs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/10 flex items-center gap-1 w-max">
                                        <Icon name="trash-2" className="w-3 h-3" /> Duplicate
                                      </span>
                                    );
                                  } else if (clusterId) {
                                    content = (
                                      <span className="px-2 py-0.5 rounded text-xxs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/10 flex items-center gap-1 w-max">
                                        <Icon name="check-circle" className="w-3 h-3" /> Keep Master
                                      </span>
                                    );
                                  } else {
                                    content = (
                                      <span className="px-2 py-0.5 rounded text-xxs font-semibold bg-slate-900 text-slate-400 border border-slate-800 flex items-center gap-1 w-max">
                                        Unique
                                      </span>
                                    );
                                  }
                                } else if (header === "clean_phone") {
                                  content = cellVal ? (
                                    <span className="font-semibold text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded border border-emerald-500/15 font-mono">
                                      {String(cellVal)}
                                    </span>
                                  ) : (
                                    <span className="text-slate-600">-</span>
                                  );
                                } else if (header === "clean_email") {
                                  content = cellVal ? (
                                    <span className="text-indigo-400 bg-indigo-500/10 px-2.5 py-1 rounded border border-indigo-500/15">
                                      {String(cellVal)}
                                    </span>
                                  ) : (
                                    <span className="text-slate-600">-</span>
                                  );
                                } else if (header === "clean_website") {
                                  content = cellVal ? (
                                    <span className="text-slate-400 bg-slate-500/10 px-2.5 py-1 rounded border border-slate-500/15">
                                      {String(cellVal)}
                                    </span>
                                  ) : (
                                    <span className="text-slate-600">-</span>
                                  );
                                } else if (header === "clean_company") {
                                  content = cellVal ? (
                                    <span className="text-pink-400 bg-pink-500/10 px-2.5 py-1 rounded border border-pink-500/15">
                                      {String(cellVal)}
                                    </span>
                                  ) : (
                                    <span className="text-slate-600">-</span>
                                  );
                                } else {
                                  const isPhoneCol = header === processResult.column_mappings["phone"];
                                  const isEmailCol = header === processResult.column_mappings["email"];
                                  
                                  if (isPhoneCol) {
                                    content = (
                                      <div className="flex items-center gap-2">
                                        <span className={!isValidPhone ? "text-red-400 line-through opacity-70" : isAlteredPhone ? "text-yellow-400 font-medium" : "text-slate-200"}>
                                          {String(cellVal !== null ? cellVal : "")}
                                        </span>
                                        {isAlteredPhone && isValidPhone && (
                                          <span className="text-[10px] text-yellow-500 font-bold bg-yellow-500/10 px-1 rounded">Cleansed</span>
                                        )}
                                        {!isValidPhone && cellVal && (
                                          <span className="text-[10px] text-red-500 font-bold bg-red-500/10 px-1 rounded flex items-center gap-0.5" title="Invalid NANP area code or format length">
                                            <Icon name="alert-circle" className="w-2.5 h-2.5" /> Invalid
                                          </span>
                                        )}
                                      </div>
                                    );
                                  } else if (isEmailCol) {
                                    content = (
                                      <div className="flex items-center gap-2">
                                        <span className={!isValidEmail ? "text-red-400 line-through opacity-70" : "text-slate-200"}>
                                          {String(cellVal !== null ? cellVal : "")}
                                        </span>
                                        {!isValidEmail && cellVal && (
                                          <span className="text-[10px] text-red-500 font-bold bg-red-500/10 px-1 rounded flex items-center gap-0.5" title="Invalid email structure">
                                            <Icon name="alert-circle" className="w-2.5 h-2.5" /> Invalid
                                          </span>
                                        )}
                                      </div>
                                    );
                                  }
                                }

                                return (
                                  <td key={header} className="px-4 py-3 whitespace-nowrap overflow-hidden text-ellipsis max-w-[200px]">
                                    {content}
                                  </td>
                                );
                              })}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  {/* Pagination Controls */}
                  {totalPages > 1 && (
                    <div className="p-4 border-t border-slate-800/80 bg-slate-900/20 flex items-center justify-between text-xs text-slate-400">
                      <div>
                        Showing page <strong>{currentPage + 1}</strong> of <strong>{totalPages}</strong> ({(currentPage * pageSize + 1).toLocaleString()} - {Math.min((currentPage + 1) * pageSize, totalRecordsCount).toLocaleString()} of {totalRecordsCount.toLocaleString()} total rows)
                      </div>
                      <div className="flex items-center gap-2">
                        <button 
                          onClick={() => fetchPage(processResult.session_id, currentPage - 1)}
                          disabled={currentPage === 0}
                          className="px-3 py-1.5 rounded-lg border border-slate-800 hover:border-slate-700 bg-slate-950 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                        >
                          Previous
                        </button>
                        <button 
                          onClick={() => fetchPage(processResult.session_id, currentPage + 1)}
                          disabled={currentPage >= totalPages - 1}
                          className="px-3 py-1.5 rounded-lg border border-slate-800 hover:border-slate-700 bg-slate-950 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                        >
                          Next
                        </button>
                      </div>
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        );
      }

      const root = ReactDOM.createRoot(document.getElementById('root'));
      root.render(<App />);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

# -------------------------------------------------------------
# 7. RUN SERVER
# -------------------------------------------------------------
if __name__ == "__main__":
    print("\n==========================================================")
    print("        LAUNCHING SINGLE-FILE DEDUPEFLOW SAAS             ")
    print("==========================================================")
    print("Visit in browser: -> http://127.0.0.1:8000")
    print("==========================================================\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
