import os
import uuid
import logging
import io
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import polars as pl
from backend.config import UPLOAD_DIR
from backend.cache_layer import get_cache
from backend.processing import process_and_deduplicate

logger = logging.getLogger("dedupe_api")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Data Quality & Deduplication SaaS API")

# Configure CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production specify exact frontend url
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

cache = get_cache()

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Uploads a file chunk by chunk directly to disk storage to prevent RAM spikes."""
    try:
        session_file_id = str(uuid.uuid4())
        file_extension = os.path.splitext(file.filename)[1].lower()
        if file_extension not in [".csv", ".txt"]:
            raise HTTPException(status_code=400, detail="Only CSV or TXT files are supported.")

        saved_path = os.path.join(UPLOAD_DIR, f"{session_file_id}{file_extension}")
        
        # Stream file to disk in 1MB chunks
        with open(saved_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
                
        # Get quick line count (without loading full file into RAM)
        line_count = 0
        with open(saved_path, "rb") as f:
            for _ in f:
                line_count += 1
                if line_count > 1000000: # cap check for safety
                    break
        
        return {
            "file_id": session_file_id,
            "filename": file.filename,
            "file_path": saved_path,
            "approx_rows": max(0, line_count - 1) # subtract header
        }
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")


@app.post("/api/process")
async def process_dataset(
    file1_path: str = Form(...),
    file2_path: Optional[str] = Form(None),
    mode: str = Form("internal"), # "internal" or "cross"
    match_fields: List[str] = Form(...)
):
    """Executes the deduplication & cleansing pipeline and caches results."""
    try:
        session_id = str(uuid.uuid4())
        
        # Check files exist
        if not os.path.exists(file1_path):
            raise HTTPException(status_code=404, detail="Primary file path not found.")
        if file2_path and not os.path.exists(file2_path):
            raise HTTPException(status_code=404, detail="Secondary file path not found.")
        
        # Parse match fields (FastAPI Form might send list as single comma-separated string or list)
        parsed_fields = []
        for f in match_fields:
            if "," in f:
                parsed_fields.extend([x.strip() for x in f.split(",")])
            else:
                parsed_fields.append(f.strip())

        # Clean/normalize
        parsed_fields = [f for f in parsed_fields if f]
        logger.info(f"Processing session {session_id} with match fields: {parsed_fields}")

        # Get original headers of File 1 and File 2
        file1_cols = pl.scan_csv(file1_path).columns
        file2_cols = pl.scan_csv(file2_path).columns if file2_path else []

        # Run pipeline
        processed_df = process_and_deduplicate(
            file_path_1=file1_path,
            file_path_2=file2_path if file2_path else None,
            mode=mode,
            match_fields=parsed_fields
        )

        # Cache result
        cache.set_records(session_id, processed_df)

        # Cache metadata
        metadata = {
            "mode": mode,
            "file1_cols": file1_cols,
            "file2_cols": file2_cols
        }
        cache.set_metadata(session_id, metadata)

        # Retrieve metrics
        metrics = cache.get_metrics(session_id)
        
        # Auto-map columns helper to return to UI
        from backend.processing import auto_map_columns
        mappings = auto_map_columns(processed_df.columns)

        return {
            "session_id": session_id,
            "metrics": metrics,
            "column_mappings": mappings,
            "columns": processed_df.columns
        }
    except Exception as e:
        logger.error(f"Error during processing: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Data processing failed: {str(e)}")


@app.get("/api/records")
async def get_records(
    session_id: str = Query(...),
    page: int = Query(0),
    limit: int = Query(100)
):
    """Fetches virtualized grid records paginated from the cache."""
    try:
        if not cache.has_session(session_id):
            raise HTTPException(status_code=404, detail="Session expired or not found.")
            
        records = cache.get_page(session_id, page, limit)
        total_records = cache.get_total_count(session_id)
        
        return {
            "records": records,
            "total_records": total_records,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        logger.error(f"Error fetching page: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/clean")
async def export_clean_file(
    session_id: str = Query(...),
    format: str = Query("csv"), # "csv" or "xlsx"
    selection_mode: str = Query("keep_oldest"), # "keep_oldest", "keep_newest", "keep_all"
    exclude_ids: Optional[str] = Query(None) # Comma-separated list of original_row_indexes to exclude (for manual check)
):
    """Generates the downloadable cleaned dataset (duplicates and invalid items filtered based on settings)."""
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired or not found.")

        # Get metadata
        metadata = cache.get_metadata(session_id) or {}
        session_mode = metadata.get("mode", "internal")
        file2_cols = metadata.get("file2_cols", [])

        # Apply selection rules
        if selection_mode == "keep_all":
            # Just keep everything
            filtered_df = df
        elif selection_mode == "custom" and exclude_ids:
            # Drop row indices explicitly checked in manual checkbox mode
            exclude_list = [int(x.strip()) for x in exclude_ids.split(",") if x.strip().isdigit()]
            filtered_df = df.filter(~pl.col("original_row_index").is_in(exclude_list))
        elif selection_mode == "keep_newest":
            # In process_and_deduplicate, duplicates are marked where is_duplicate=True.
            # However, under "keep_newest", we want to keep the LATEST/NEWEST record inside each cluster.
            # In our pipeline, the oldest (first row) was kept and the rest were marked as duplicate.
            # To keep the newest: we filter where is_duplicate is True, BUT we must swap which row is kept.
            # Let's implement this by identifying clusters, keeping the last row in each, and dropping the rest.
            if "cluster_id" in df.columns:
                # Find all records with duplicate markers
                # Let's drop duplicate records but keep the newest:
                # For each cluster_id, the record with the maximum original_row_index is kept.
                # If a record has cluster_id = null, it is not duplicate, so keep it.
                # We can write a Polars expression:
                # Group by cluster_id, filter to keep the maximum original_row_index, and merge back.
                # A simpler way is to sort df by original_row_index descending, drop duplicates by cluster_id (subset), and sort back.
                
                # Split df into clustered and non-clustered
                clustered = df.filter(pl.col("cluster_id").is_not_null())
                non_clustered = df.filter(pl.col("cluster_id").is_null())
                
                # Keep latest for each cluster (by sorting descending and taking first)
                kept_clustered = clustered.sort("original_row_index", descending=True).unique(subset=["cluster_id"], keep="first")
                
                filtered_df = pl.concat([non_clustered, kept_clustered]).sort("original_row_index")
            else:
                filtered_df = df
        else: # "keep_oldest" (default)
            # Filter where is_duplicate == False (only keep oldest/original unique rows)
            filtered_df = df.filter(~pl.col("is_duplicate"))

        # Strip internal tracking columns and isolate correct records based on mode
        if session_mode == "cross":
            # In cross mode, we only export File 2 cleaned records
            filtered_df = filtered_df.filter(pl.col("source_file") == "File 2")
            export_cols = [c for c in file2_cols if c in filtered_df.columns]
        else:
            # Strip internal tracking columns before returning
            export_cols = [c for c in filtered_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code", "is_altered_phone", "is_valid_phone", "is_valid_email", "cluster_id", "is_duplicate", "original_row_index", "source_file"]]
        
        export_df = filtered_df.select(export_cols)

        # Output format
        if format == "xlsx":
            buffer = io.BytesIO()
            export_df.write_excel(buffer)
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename=cleaned_dataset_{session_id}.xlsx"}
            )
        else: # csv
            buffer = io.BytesIO()
            export_df.write_csv(buffer)
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=cleaned_dataset_{session_id}.csv"}
            )
            
    except Exception as e:
        logger.error(f"Error exporting clean file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/duplicates")
async def export_duplicates_file(
    session_id: str = Query(...),
    format: str = Query("csv"), # "csv" or "xlsx"
    selection_mode: str = Query("keep_oldest"), # keep_oldest, keep_newest, custom
    exclude_ids: Optional[str] = Query(None)
):
    """Generates a downloadable file containing ONLY the removed duplicate rows for QA auditing."""
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired or not found.")

        # Get metadata
        metadata = cache.get_metadata(session_id) or {}
        session_mode = metadata.get("mode", "internal")
        file2_cols = metadata.get("file2_cols", [])

        # Identify which rows are excluded based on the selection settings
        if selection_mode == "keep_all":
            # Nothing was excluded
            excluded_df = df.filter(pl.lit(False)) # empty dataframe
        elif selection_mode == "custom" and exclude_ids:
            exclude_list = [int(x.strip()) for x in exclude_ids.split(",") if x.strip().isdigit()]
            excluded_df = df.filter(pl.col("original_row_index").is_in(exclude_list))
        elif selection_mode == "keep_newest":
            # Excluded records are the ones that were NOT kept under keep_newest
            if "cluster_id" in df.columns:
                # Find the maximum original_row_index for each cluster
                clustered = df.filter(pl.col("cluster_id").is_not_null())
                kept_indices = clustered.sort("original_row_index", descending=True).unique(subset=["cluster_id"], keep="first")["original_row_index"].to_list()
                
                # Excluded are all clustered records not in kept_indices
                excluded_df = clustered.filter(~pl.col("original_row_index").is_in(kept_indices)).sort("original_row_index")
            else:
                excluded_df = df.filter(pl.lit(False))
        else: # "keep_oldest"
            # Excluded are all rows where is_duplicate == True
            excluded_df = df.filter(pl.col("is_duplicate"))

        # Strip internal helper columns, but keep source_file, cluster_id, and original_row_index for QA reference!
        if session_mode == "cross":
            excluded_df = excluded_df.filter(pl.col("source_file") == "File 2")
            qa_cols = [c for c in file2_cols if c in excluded_df.columns] + ["cluster_id", "original_row_index", "source_file"]
        else:
            qa_cols = [c for c in excluded_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code", "is_altered_phone", "is_valid_phone", "is_valid_email"]]
        
        qa_cols = [c for c in qa_cols if c in excluded_df.columns]
        qa_df = excluded_df.select(qa_cols)

        # Output format
        if format == "xlsx":
            buffer = io.BytesIO()
            qa_df.write_excel(buffer)
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename=removed_duplicates_{session_id}.xlsx"}
            )
        else: # csv
            buffer = io.BytesIO()
            qa_df.write_csv(buffer)
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=removed_duplicates_{session_id}.csv"}
            )
            
    except Exception as e:
        logger.error(f"Error exporting duplicates file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def stream_dataframe(df: pl.DataFrame, filename: str, format: str) -> StreamingResponse:
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


@app.get("/api/export/invalid")
async def export_invalid_file(
    session_id: str = Query(...),
    format: str = Query("csv")
):
    """Generates a downloadable file containing only invalid records."""
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired or not found.")
        
        # Filter where phone is invalid or email is invalid
        invalid_df = df.filter(~pl.col("is_valid_phone") | ~pl.col("is_valid_email"))
        
        # Remove helper columns for clean view, but keep error identifiers
        cols = [c for c in invalid_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code"]]
        return stream_dataframe(invalid_df.select(cols), f"invalid_formats_{session_id}", format)
    except Exception as e:
        logger.error(f"Error exporting invalid file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/clusters")
async def export_clusters_file(
    session_id: str = Query(...),
    format: str = Query("csv")
):
    """Generates a downloadable file containing all rows in duplicate clusters sorted by cluster ID."""
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired or not found.")
        
        # Filter clustered records
        clustered_df = df.filter(pl.col("cluster_id").is_not_null()).sort("cluster_id")
        
        cols = [c for c in clustered_df.columns if c not in ["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code"]]
        return stream_dataframe(clustered_df.select(cols), f"duplicate_clusters_{session_id}", format)
    except Exception as e:
        logger.error(f"Error exporting clusters file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/all")
async def export_all_file(
    session_id: str = Query(...),
    format: str = Query("csv")
):
    """Generates a downloadable file containing all records with both original and cleansed values side by side."""
    try:
        df = cache.get_dataframe(session_id)
        if df is None:
            raise HTTPException(status_code=404, detail="Session expired or not found.")
        return stream_dataframe(df, f"full_processed_dataset_{session_id}", format)
    except Exception as e:
        logger.error(f"Error exporting all records file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

