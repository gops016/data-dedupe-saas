import os
import tempfile
import polars as pl
import pytest
from backend.processing import process_and_deduplicate, auto_map_columns

def test_auto_map_columns():
    cols = ["first_name", "last_name", "phone_number", "email_address", "company_name"]
    mappings = auto_map_columns(cols)
    assert mappings["first_name"] == "first_name"
    assert mappings["last_name"] == "last_name"
    assert mappings["phone"] == "phone_number"
    assert mappings["email"] == "email_address"
    assert mappings["company"] == "company_name"

def test_cleansing_pipeline():
    # Write a small CSV file
    csv_data = (
        "first_name,last_name,phone,email,website,company\n"
        "John,Doe,+1 (415) 555-0199, John.Doe+marketing@Gmail.com ,https://www.Google.com/about,Google LLC\n"
        "Jane,Smith,14155550199,jane@yahoo.com,http://yahoo.com,Yahoo Inc\n"
        "Bob,Jones,0123456789,bob@bademail,www.badurl,Bad Co\n"
    )
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as temp:
        temp.write(csv_data)
        temp_path = temp.name

    try:
        # Run processing
        df = process_and_deduplicate(temp_path, match_fields=["email", "phone"])
        
        # Row 1 tests (John Doe)
        assert df["clean_phone"][0] == "4155550199"
        assert df["is_valid_phone"][0] is True
        assert df["is_altered_phone"][0] is True
        assert df["clean_email"][0] == "john.doe@gmail.com"
        assert df["is_valid_email"][0] is True
        assert df["clean_website"][0] == "google.com"
        assert df["clean_company"][0] == "google"
        assert df["clean_name"][0] == "johndoe"
        assert df["domain_token"][0] == "google.com"

        # Row 2 tests (Jane Smith)
        assert df["clean_phone"][1] == "4155550199"
        assert df["is_valid_phone"][1] is True
        assert df["clean_email"][1] == "jane@yahoo.com"
        assert df["is_valid_email"][1] is True

        # Row 3 tests (Bob Jones - Invalid items)
        assert df["is_valid_phone"][2] is False # Starts with 0
        assert df["is_valid_email"][2] is False # No dot in domain
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def test_internal_deduplication():
    # Setup CSV with duplicates on email or phone
    # Row 0 and Row 1 are duplicates on email
    # Row 1 and Row 2 are duplicates on phone
    # Through transitive Union-Find, Row 0, 1, and 2 should be in the same cluster.
    csv_data = (
        "first_name,last_name,phone,email\n"
        "Alice,Smith,4155550100,alice@test.com\n"
        "Alice,Jones,4155550200,alice@test.com\n"
        "Bob,Jones,4155550200,bob@test.com\n"
        "Charlie,Brown,4155550300,charlie@test.com\n" # Unique
    )
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as temp:
        temp.write(csv_data)
        temp_path = temp.name

    try:
        df = process_and_deduplicate(temp_path, match_fields=["email", "phone"])
        
        # Check clusters
        assert df["cluster_id"][0] is not None
        assert df["cluster_id"][0] == df["cluster_id"][1]
        assert df["cluster_id"][1] == df["cluster_id"][2]
        assert df["cluster_id"][3] is None # Unique
        
        # Keep oldest means first row is kept, subsequent are duplicates
        assert df["is_duplicate"][0] is False
        assert df["is_duplicate"][1] is True
        assert df["is_duplicate"][2] is True
        assert df["is_duplicate"][3] is False
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def test_cross_file_deduplication():
    # File 1 (Master)
    csv1 = (
        "first_name,last_name,phone,email\n"
        "Alice,Smith,4155550100,alice@test.com\n"
    )
    
    # File 2 (Secondary)
    # Row 0 duplicate of File 1 Alice
    # Row 1 unique
    csv2 = (
        "first_name,last_name,phone,email\n"
        "Alice,Jones,4155550200,alice@test.com\n"
        "Bob,Jones,4155550300,bob@test.com\n"
    )
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as t1, \
         tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as t2:
        t1.write(csv1)
        t2.write(csv2)
        p1 = t1.name
        p2 = t2.name

    try:
        df = process_and_deduplicate(p1, p2, mode="cross", match_fields=["email"])
        
        # Height should be 3 (1 from File 1, 2 from File 2)
        assert df.height == 3
        
        # Row 0 (File 1) and Row 1 (File 2) match on email
        assert df["cluster_id"][0] is not None
        assert df["cluster_id"][0] == df["cluster_id"][1]
        assert df["cluster_id"][2] is None
        
        # In Cross mode: File 1 remains untouched (is_duplicate = False)
        # File 2 matching record is marked duplicate (is_duplicate = True)
        assert df["source_file"][0] == "File 1"
        assert df["is_duplicate"][0] is False
        
        assert df["source_file"][1] == "File 2"
        assert df["is_duplicate"][1] is True
        
        assert df["source_file"][2] == "File 2"
        assert df["is_duplicate"][2] is False
        
    finally:
        for p in [p1, p2]:
            if os.path.exists(p):
                os.remove(p)
