import re
import polars as pl
from typing import List, Dict, Any, Set, Tuple, Optional
from backend.area_codes import VALID_NANP_AREA_CODES

# Common free/public email domains to exclude from domain matching
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "zoho.com", "protonmail.com", "yandex.com", "mail.com",
    "gmx.com", "live.com", "msn.com", "comcast.net", "sbcglobal.net",
    "bellsouth.net", "verizon.net", "cox.net", "charter.net", "att.net"
}

class UnionFind:
    """An optimized Union-Find (Disjoint Set Union) structure for index clustering."""
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
    """Auto-detect columns based on standard naming conventions."""
    mappings = {}
    col_lower = [c.lower().replace("_", "").replace(" ", "") for c in columns]
    
    # Mapping definitions: keys are standard field names, values are list of keywords
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
            # Match exact or prefix
            if any(keyword == norm_col or (len(keyword) > 3 and norm_col.startswith(keyword)) for keyword in keywords):
                # Don't overwrite if already mapped
                if standard_name not in mappings:
                    mappings[standard_name] = col
                    break
                    
    # Fallbacks for names
    if "first_name" not in mappings and "name" in mappings:
        mappings["first_name"] = mappings["name"]
        
    return mappings


def process_and_deduplicate(
    file_path_1: str,
    file_path_2: Optional[str] = None,
    mode: str = "internal",  # "internal" or "cross"
    match_fields: Optional[List[str]] = None
) -> pl.DataFrame:
    """
    Ingests files using Polars LazyFrames, runs the cleaning pipeline,
    and identifies duplicate rows using Union-Find clustering.
    """
    if match_fields is None:
        match_fields = ["email", "phone"]

    # 1. Read files lazily
    lf1 = pl.scan_csv(file_path_1, infer_schema_length=10000, ignore_errors=True)
    df1_orig = lf1.collect()
    
    # Insert source file tag and row ID
    df1 = df1_orig.with_columns([
        pl.lit("File 1").alias("source_file"),
        pl.arange(0, df1_orig.height).alias("original_row_index")
    ])
    
    if file_path_2 and mode == "cross":
        lf2 = pl.scan_csv(file_path_2, infer_schema_length=10000, ignore_errors=True)
        df2_orig = lf2.collect()
        df2 = df2_orig.with_columns([
            pl.lit("File 2").alias("source_file"),
            pl.arange(df1.height, df1.height + df2_orig.height).alias("original_row_index")
        ])
        # Align schemas by adding missing columns to both
        all_cols = list(set(df1.columns) | set(df2.columns))
        for col in all_cols:
            if col not in df1.columns:
                df1 = df1.with_columns(pl.lit(None).alias(col))
            if col not in df2.columns:
                df2 = df2.with_columns(pl.lit(None).alias(col))
        # Ensure identical column order
        df1 = df1.select(all_cols)
        df2 = df2.select(all_cols)
        df = pl.concat([df1, df2])
    else:
        df = df1
        if file_path_2 and mode == "internal":
            # Just process file 1 and 2 concatenated but run internal dedup on both
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

    # 2. Map standard fields to file headers
    col_mapping = auto_map_columns(df.columns)
    
    # 3. Apply Polars transformations lazily on the combined dataframe
    lf = df.lazy()
    
    # Expressions list
    exprs = []
    
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

    # Combined Full Name
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

    # Evaluate expressions
    lf = lf.with_columns(exprs)
    
    # 4. Validations
    lf = lf.with_columns([
        # Validate Phone
        (pl.col("clean_phone").str.len_chars() == 10).alias("is_valid_phone_length"),
        pl.col("clean_phone").str.slice(0, 3).is_in(list(VALID_NANP_AREA_CODES)).alias("is_valid_area_code"),
        # Extract Domain from clean_email
        pl.col("clean_email").str.replace(r"^[^@]*@", "").alias("email_domain")
    ]).with_columns([
        # Combined Phone Validation
        (pl.col("is_valid_phone_length") & pl.col("is_valid_area_code")).alias("is_valid_phone"),
        # Email Validation (must contain @ and at least one dot in domain)
        pl.col("clean_email").str.contains(r"^[^@]+@[^@]+\.[^@]+$").fill_null(False).alias("is_valid_email"),
        # Isolate final Domain Token (use clean_website domain, fallback to email_domain)
        pl.when(pl.col("clean_website") != "")
        .then(pl.col("clean_website"))
        .otherwise(pl.col("email_domain"))
        .alias("domain_token")
    ])

    # Collect DataFrame to perform clustering
    processed_df = lf.collect()
    total_rows = processed_df.height

    # 5. Union-Find Clustering Logic
    uf = UnionFind(total_rows)
    
    # Build indexing arrays for fast Union-Find matching
    # Map from match value -> row indices
    
    # Decide which matching fields are active
    active_tokens = {} # field_name -> list of values
    
    if "phone" in match_fields:
        # Only match on valid cleaned phones
        active_tokens["phone"] = [
            (i, val) for i, val in enumerate(processed_df["clean_phone"].to_list()) 
            if val and processed_df["is_valid_phone"][i]
        ]
        
    if "email" in match_fields:
        active_tokens["email"] = [
            (i, val) for i, val in enumerate(processed_df["clean_email"].to_list()) 
            if val and processed_df["is_valid_email"][i]
        ]
        
    if "website" in match_fields:
        active_tokens["website"] = [
            (i, val) for i, val in enumerate(processed_df["clean_website"].to_list()) 
            if val
        ]
        
    if "domain" in match_fields:
        # Domain token matching, excluding free public email hosts
        active_tokens["domain"] = [
            (i, val) for i, val in enumerate(processed_df["domain_token"].to_list()) 
            if val and val not in FREE_EMAIL_DOMAINS
        ]
        
    if "company" in match_fields:
        active_tokens["company"] = [
            (i, val) for i, val in enumerate(processed_df["clean_company"].to_list()) 
            if val
        ]
        
    if "name" in match_fields:
        active_tokens["name"] = [
            (i, val) for i, val in enumerate(processed_df["clean_name"].to_list()) 
            if val
        ]

    # Run Union-Find unions for each field
    for field, items in active_tokens.items():
        # Group indices by value
        groups: Dict[str, List[int]] = {}
        for idx, val in items:
            groups.setdefault(val, []).append(idx)
            
        for val, indices in groups.items():
            if len(indices) > 1:
                first = indices[0]
                for other in indices[1:]:
                    # If mode is cross-file: we only mark duplicates in File 2 that exist in File 1.
                    # Wait! In cross-file deduplication, a record in File 2 is duplicate if it matches File 1.
                    # Under DSU, we union all records together. Later, we can identify which cluster contains
                    # at least one File 1 record, and label File 2 records in that cluster as duplicate.
                    uf.union(first, other)

    # 6. Group components and assign Cluster IDs
    root_to_indices: Dict[int, List[int]] = {}
    for i in range(total_rows):
        root = uf.find(i)
        root_to_indices.setdefault(root, []).append(i)

    # Prepare columns for duplicate markings
    cluster_ids = [None] * total_rows
    is_duplicate = [False] * total_rows
    
    cluster_counter = 1
    
    for root, indices in root_to_indices.items():
        if len(indices) > 1:
            # Check file source distribution in this cluster
            file_sources = [processed_df["source_file"][idx] for idx in indices]
            
            if mode == "cross":
                # In cross-file mode, a cluster represents duplicates *only if* it contains at least one record from File 1.
                # If a cluster only contains File 2 records, they are not duplicates of File 1 (unless we also want internal dedup).
                # The user says: "identify records in File 2 that already exist inside File 1 without modifying File 1."
                has_file_1 = "File 1" in file_sources
                has_file_2 = "File 2" in file_sources
                if has_file_1 and has_file_2:
                    cid = f"CLUST_{cluster_counter:05d}"
                    cluster_counter += 1
                    for idx in indices:
                        cluster_ids[idx] = cid
                        # Only File 2 records are flagged as duplicates to drop, File 1 is the Master and remains untouched!
                        if processed_df["source_file"][idx] == "File 2":
                            is_duplicate[idx] = True
            else:
                # Internal deduplication: group duplicates within and across files.
                # In each cluster, we keep one record (the oldest/first) and mark all others as duplicates.
                # We sort by original row index to ensure oldest is first.
                sorted_indices = sorted(indices)
                cid = f"CLUST_{cluster_counter:05d}"
                cluster_counter += 1
                for idx in sorted_indices:
                    cluster_ids[idx] = cid
                # Mark everything except the first record in the sorted cluster as duplicate
                for idx in sorted_indices[1:]:
                    is_duplicate[idx] = True

    # Add clustering columns to DataFrame
    processed_df = processed_df.with_columns([
        pl.Series(cluster_ids).alias("cluster_id"),
        pl.Series(is_duplicate).alias("is_duplicate")
    ])

    # Detect if phone was altered during cleaning
    if phone_col:
        # Compare original raw value (stripped of whitespace) with cleaned phone
        is_altered_phone = []
        raw_phones = processed_df[phone_col].to_list()
        clean_phones = processed_df["clean_phone"].to_list()
        for raw, clean in zip(raw_phones, clean_phones):
            raw_digits = re.sub(r"\D", "", str(raw or ""))
            # If country code 1 was stripped, it counts as altered
            is_altered_phone.append(raw_digits != clean)
        processed_df = processed_df.with_columns(pl.Series(is_altered_phone).alias("is_altered_phone"))
    else:
        processed_df = processed_df.with_columns(pl.lit(False).alias("is_altered_phone"))

    return processed_df
