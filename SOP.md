# Standard Operating Procedure (SOP) - Data Quality & Deduplication SaaS

## 1. Document Overview
This document outlines the standard operating procedures for installing, configuring, running, and QA-verifying the Data Quality and Deduplication SaaS application.

### 1.1 Single-File Running Guide (Recommended for Sharing & Client Execution)
The entire SaaS application (FastAPI backend + React/Tailwind frontend) is compiled into a single portable Python file: `app.py`. This offers a zero-config setup to run and share the application instantly.

#### Prerequisites
* Python 3.10 or higher
* Required packages: `polars`, `fastapi`, `uvicorn`, `python-multipart`, `openpyxl`, `xlsxwriter`

#### How to Run:
1. **Install dependencies**:
   ```bash
   pip install polars fastapi uvicorn python-multipart openpyxl xlsxwriter
   ```
2. **Run the app**:
   ```bash
   python app.py
   ```
3. **Access the interface**: Open your web browser and navigate to `http://127.0.0.1:8000/`

---

## 2. System Architecture & Setup

The application is structured into two main components:
1. **Backend API**: Built with FastAPI and Polars. It handles multi-gigabyte files efficiently via memory-mapped streams and Union-Find (DSU) clustering.
2. **Frontend UI**: Built with React, Vite, TailwindCSS, and `@tanstack/react-virtual` for a 60FPS virtualized infinite-scroll grid that handles up to 10 Lakh (1 Million) rows.

### Prerequisites
* Python 3.10 or higher
* Node.js v18 or higher
* (Optional) Redis server (if Redis is not found, the system automatically falls back to high-performance disk IPC FileCache)

### Local Installation

#### Step 1: Clone or copy the project files to your local directory.
Ensure the directory structure matches:
```
Data Dedupe SaaS/
├── backend/
│   ├── uploads/
│   ├── cache/
│   ├── area_codes.py
│   ├── cache_layer.py
│   ├── config.py
│   ├── main.py
│   └── processing.py
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── README.md
└── SOP.md
```

#### Step 2: Set up the Backend
1. Open terminal and navigate to the project directory:
   ```bash
   cd "Data Dedupe SaaS"
   ```
2. Install Python dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
3. Start the FastAPI Uvicorn server:
   ```bash
   python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
   ```

#### Step 3: Set up the Frontend
1. Open a new terminal and navigate to the `frontend/` directory:
   ```bash
   cd "Data Dedupe SaaS/frontend"
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the Vite dev server:
   ```bash
   npm run dev
   ```
4. Access the local dashboard in your browser at `http://127.0.0.1:3000`.

---

## 3. Data Cleansing & Deduplication Logic

The cleansing pipeline normalizes raw, dirty data into standard match tokens using strictly ordered rules:

### A. Phone Number Cleansing & Validation
1. **Symbols Removal**: Strips characters: `+`, `-`, `(`, `)`, `.`, `[`, `]`, and spaces.
2. **Country Code Normalization**: If the digits length is exactly 11 and starts with `1`, the country code `1` is stripped. Valid 10-digit numbers starting with 1 (e.g. Area Code 151) are preserved.
3. **NANP Area Code Validation**: The first 3 digits of the phone number are checked against the North American Numbering Plan Index (345+ active area codes).
4. **Altered Flags**: If the normalized number differs from the raw input digits, it is flagged as `is_altered_phone` in the database.

### B. Email Cleansing & Validation
1. **Formatting**: Lowercases the text and strips leading/trailing whitespaces.
2. **Subaddress Tag Stripping**: Removes email subaddress tags (e.g. `user+marketing@domain.com` normalizes to `user@domain.com`).
3. **Syntax Validation**: Validates the email contains a single `@` and at least one dot `.` in the domain.

### C. Website & Domain Cleansing
1. **Protocols Stripping**: Removes `http://`, `https://`, and `www.`.
2. **Subpath Removal**: Truncates paths and query strings (e.g., `https://google.com/about?q=test` becomes `google.com`).
3. **Email Domain Extraction**: If a website is missing, the domain is extracted from the cleaned email.
4. **Public Host Filtering**: Common public email hosts (e.g. `gmail.com`, `yahoo.com`, `hotmail.com`) are ignored for domain-matching rules.

### D. Company Name Cleansing
1. **Punctuation Stripping**: Removes characters: `.`, `,`, `-`, `_`, `&`, `(`, `)`, `[`, `]`, `"`, `'`.
2. **Suffix Removal**: Automatically replaces corporate suffix tags (e.g. `llc`, `inc`, `corp`, `co`, `ltd`, `limited`, `corporation`) globally.
3. **Whitespace Normalization**: Compresses multiple spaces to a single space.
*(Example: `Smith Corp LLC` and `Smith Corp.` both normalize to `smith` and match successfully).*

---

## 4. User Guide: Step-by-Step Operations

### Step 1: Uploading Datasets
* **File 1 (Primary Master)**: Upload your baseline reference dataset (e.g., CRM database).
  *(For testing, you can use the sample file: `mock_data/mock_file_1.csv`)*
* **File 2 (Secondary Target) [Optional]**: Upload the target dataset you want to clean (e.g., new lead list).
  *(For testing, you can use the sample file: `mock_data/mock_file_2.csv`)*



### Step 2: Choosing Deduplication Mode
* **Internal Deduplication**: Identifies duplicates within File 1 (and File 2 if uploaded), merging them into clusters. The oldest record is kept; subsequent duplicates are marked to be dropped.
* **Cross-File Deduplication**: Scans File 2 against File 1. File 1 remains untouched. Any record in File 2 that matches File 1 is flagged as a duplicate to be dropped.

### Step 3: Configuring Match Fields
Choose one or more fields to match on:
* **Email Address**: Matches exact normalized emails.
* **Phone Number**: Matches exact normalized 10-digit numbers.
* **Domain Name**: Matches corporate email domains/websites (excluding public hosts).
* **Company Name**: Matches normalized company names.
* **Full Name**: Matches concatenated first and last names.
*(If multiple fields are checked, they are evaluated transitively via Disjoint Set Union (DSU) Union-Find).*

### Step 4: Run Cleansing
Click the **"Clean & Process Data Matrix"** button. The backend processes the files and returns the metrics dashboard and live grid.

---

## 5. QA Verification & Auditing Procedure

After processing, operators must verify the results before downloading:

### 1. Dashboard Metrics Validation
* **Total Processed**: Verify this matches the sum of input rows.
* **Invalid Formats**: Displays counts of phone/emails failing validation. Click the card to download the **Invalid Formats Audit Sheet** to verify.
* **Duplicate Records**: The number of duplicate rows flagged to be removed.
* **Duplicate Clusters**: The number of active duplicate groups. Each cluster has exactly 1 Master (kept) and 1 or more Duplicates (removed).
* **QA Cluster Check**: Verify that `Duplicate Records` >= `Duplicate Clusters`.

### 2. Live UI Grid Manual Auditing
* **Color Highlights**: Red-shaded rows indicate duplicates; green-shaded rows indicate Master baseline records.
* **Original vs. Cleansed Side-by-Side**: Inspect columns (e.g., raw `phone` side-by-side with `clean_phone`) to confirm cleansing rules worked.
* **Manual Checkout (Optional)**: Switch selection mode to **"Manual Checkout"** in the export panel. Use the checkboxes in the table grid to manually check/uncheck rows to exclude from the final download.

---

## 6. Exporting Cleansed Outputs

Once QA is complete, download the resolved sheets:
1. **Select Export Format**: Choose **CSV** or **EXCEL**.
2. **Select Resolution Mode**:
   * **Keep Oldest**: The oldest record in each cluster is retained.
   * **Keep Newest**: The newest record in each cluster is retained.
   * **Manual Checkout**: Retains only rows not checked in the grid.
3. **Download Cleaned File**: Downloads the unique records. In cross-file mode, this contains **only File 2 rows** with **File 2 original columns**.
4. **Download Removed Duplicates**: Downloads the duplicate records for your QA audit.

---

## 7. Exposing SaaS on the Internet

To share the application with clients:
1. Open a terminal on the host machine.
2. Run localtunnel pointing to the running server port:
   * **For Single-File App (Recommended)**: Expose port 8000:
     ```bash
     npx -y localtunnel --port 8000 --local-host 127.0.0.1
     ```
   * **For Multi-File Dev Setup**: Expose port 3000:
     ```bash
     npx -y localtunnel --port 3000 --local-host 127.0.0.1
     ```
3. Copy the generated public URL (e.g., `https://clean-teeth-bake.loca.lt`).
4. Find the public IP of the host machine (which serves as the localtunnel bypass password):
   * Windows PowerShell command:
     ```powershell
     Invoke-RestMethod -Uri https://ipinfo.io/ip
     ```
5. Send the **URL** and the **IP Bypass Password** (the public IP) to the client.

