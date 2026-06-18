import React, { useState, useRef, useEffect, useMemo } from "react";
import { 
  Upload, CheckCircle, AlertTriangle, Download, Trash2, Settings, 
  Layers, FileText, CheckSquare, RefreshCw, AlertCircle, Info
} from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";

interface UploadResponse {
  file_id: string;
  filename: string;
  file_path: string;
  approx_rows: number;
}

interface Metrics {
  total_rows: number;
  invalid_phone_count: number;
  invalid_email_count: number;
  duplicate_count: number;
  unique_clusters: number;
}

interface ProcessResponse {
  session_id: string;
  metrics: Metrics;
  columns: string[];
  column_mappings: Record<string, string>;
}

export default function App() {
  // Files State
  const [file1, setFile1] = useState<UploadResponse | null>(null);
  const [file2, setFile2] = useState<UploadResponse | null>(null);
  const [isUploading1, setIsUploading1] = useState(false);
  const [isUploading2, setIsUploading2] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Configuration State
  const [mode, setMode] = useState<"internal" | "cross">("internal");
  const [selectedFields, setSelectedFields] = useState<string[]>(["email", "phone"]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  // Results State
  const [processResult, setProcessResult] = useState<ProcessResponse | null>(null);
  const [recordsCount, setRecordsCount] = useState(0);
  
  // Virtualized Data Grid States
  const [loadedPages, setLoadedPages] = useState<Record<number, any[]>>({});
  const [fetchingPages, setFetchingPages] = useState<Record<number, boolean>>({});
  const [selectedExportMode, setSelectedExportMode] = useState<"keep_oldest" | "keep_newest" | "custom">("keep_oldest");
  const [customExcludes, setCustomExcludes] = useState<Set<number>>(new Set());
  const [exportFormat, setExportFormat] = useState<"csv" | "xlsx">("csv");
  const [isDownloading, setIsDownloading] = useState(false);

  // Drag and drop refs
  const fileInputRef1 = useRef<HTMLInputElement>(null);
  const fileInputRef2 = useRef<HTMLInputElement>(null);

  // Reset when files change
  useEffect(() => {
    setProcessResult(null);
    setLoadedPages({});
    setFetchingPages({});
    setCustomExcludes(new Set());
  }, [file1, file2, mode]);

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>, fileNum: 1 | 2) => {
    const file = event.target.files?.[0];
    if (!file) return;
    
    if (fileNum === 1) {
      setIsUploading1(true);
    } else {
      setIsUploading2(true);
    }
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

      const data: UploadResponse = await response.json();
      if (fileNum === 1) {
        setFile1(data);
      } else {
        setFile2(data);
      }
    } catch (e: any) {
      setUploadError(e.message || "An error occurred during file upload.");
    } finally {
      setIsUploading1(false);
      setIsUploading2(false);
    }
  };

  const toggleField = (field: string) => {
    setSelectedFields(prev => 
      prev.includes(field) 
        ? prev.filter(f => f !== field)
        : [...prev, field]
    );
  };

  const handleProcess = async () => {
    if (!file1) return;
    setIsProcessing(true);
    setLoadedPages({});
    setFetchingPages({});
    setCustomExcludes(new Set());

    const formData = new FormData();
    formData.append("file1_path", file1.file_path);
    if (file2 && mode === "cross") {
      formData.append("file2_path", file2.file_path);
    } else if (file2) {
      // If Mode is internal, but two files are uploaded, send both to process
      formData.append("file2_path", file2.file_path);
    }
    formData.append("mode", mode);
    
    // Append match fields
    selectedFields.forEach(field => {
      formData.append("match_fields", field);
    });

    try {
      const response = await fetch("/api/process", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Processing failed");
      }

      const data: ProcessResponse = await response.json();
      setProcessResult(data);
      setRecordsCount(data.metrics.total_rows);
    } catch (e: any) {
      alert(e.message || "An error occurred during processing.");
    } finally {
      setIsProcessing(false);
    }
  };

  // Virtualized table parent ref
  const tableContainerRef = useRef<HTMLDivElement>(null);
  const pageSize = 100;

  // Infinite Scroll Paginated Fetch
  const fetchPage = async (pageNumber: number) => {
    if (!processResult) return;
    if (loadedPages[pageNumber] || fetchingPages[pageNumber]) return;

    setFetchingPages(prev => ({ ...prev, [pageNumber]: true }));

    try {
      const response = await fetch(
        `/api/records?session_id=${processResult.session_id}&page=${pageNumber}&limit=${pageSize}`
      );
      if (!response.ok) throw new Error("Failed to load records");
      const data = await response.json();
      
      setLoadedPages(prev => ({ ...prev, [pageNumber]: data.records }));
    } catch (e) {
      console.error(e);
    } finally {
      setFetchingPages(prev => ({ ...prev, [pageNumber]: false }));
    }
  };

  // Resolve raw record from indices
  const getRecord = (index: number) => {
    const pageIndex = Math.floor(index / pageSize);
    const itemOffset = index % pageSize;
    
    const pageData = loadedPages[pageIndex];
    if (pageData) {
      return pageData[itemOffset];
    }
    
    // Trigger lazy loading
    fetchPage(pageIndex);
    return null;
  };

  // Virtualizer setup
  const rowVirtualizer = useVirtualizer({
    count: recordsCount,
    getScrollElement: () => tableContainerRef.current,
    estimateSize: () => 52, // height in pixels of standard row
    overscan: 10,
  });

  const virtualRows = rowVirtualizer.getVirtualItems();

  const handleToggleExclude = (origIndex: number) => {
    setCustomExcludes(prev => {
      const next = new Set(prev);
      if (next.has(origIndex)) {
        next.delete(origIndex);
      } else {
        next.add(origIndex);
      }
      return next;
    });
  };

  const handleDownload = async (type: "clean" | "duplicates") => {
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
        ? `deduplicated_dataset.${exportFormat}` 
        : `removed_duplicates_qa.${exportFormat}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (e) {
      alert("Download failed.");
    } finally {
      setIsDownloading(false);
    }
  };

  const handleDashboardDownload = async (type: "all" | "invalid" | "duplicates" | "clusters") => {
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
    } catch (e) {
      alert("Download failed.");
    } finally {
      setIsDownloading(false);
    }
  };

  // Render original columns
  const tableHeaders = useMemo(() => {
    if (!processResult) return [];
    
    const mappings = processResult.column_mappings;
    const headers = [
      "source_file",
      "cluster_id",
      "Deduplication State"
    ];
    
    processResult.columns.forEach(col => {
      if (["clean_phone", "clean_email", "clean_website", "clean_company", "clean_name", "email_domain", "domain_token", "is_valid_phone_length", "is_valid_area_code", "is_altered_phone", "is_valid_phone", "is_valid_email", "cluster_id", "is_duplicate", "original_row_index", "source_file"].includes(col)) {
        return;
      }
      
      headers.push(col);
      
      if (col === mappings["phone"]) {
        headers.push("clean_phone");
      } else if (col === mappings["email"]) {
        headers.push("clean_email");
      } else if (col === mappings["website"]) {
        headers.push("clean_website");
      } else if (col === mappings["company"]) {
        headers.push("clean_company");
      }
    });
    
    return headers;
  }, [processResult]);

  return (
    <div className="min-h-screen bg-grid-glow pb-20 px-6 sm:px-12 text-slate-200">
      
      {/* Header */}
      <header className="py-8 flex flex-col md:flex-row justify-between items-center border-b border-slate-800/80 mb-12">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-tr from-indigo-500 to-pink-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Layers className="w-6 h-6 text-white" />
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

      <main className="max-w-7xl mx-auto space-y-10">
        
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
                    <FileText className="w-5 h-5" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-white truncate max-w-[200px]">{file1.filename}</p>
                    <p className="text-xs text-slate-400">~{file1.approx_rows.toLocaleString()} records detected</p>
                  </div>
                </div>
                <button onClick={() => setFile1(null)} className="p-2 rounded-lg text-slate-400 hover:text-red-400 transition-colors">
                  <Trash2 className="w-4 h-4" />
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
                    <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin" />
                    <p className="text-sm font-medium text-slate-300">Chunking file uploads to disk...</p>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-3">
                    <div className="w-12 h-12 rounded-xl bg-slate-900 flex items-center justify-center text-slate-400 group-hover:text-indigo-400 transition-colors">
                      <Upload className="w-5 h-5" />
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
                    <FileText className="w-5 h-5" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-white truncate max-w-[200px]">{file2.filename}</p>
                    <p className="text-xs text-slate-400">~{file2.approx_rows.toLocaleString()} records detected</p>
                  </div>
                </div>
                <button onClick={() => setFile2(null)} className="p-2 rounded-lg text-slate-400 hover:text-red-400 transition-colors">
                  <Trash2 className="w-4 h-4" />
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
                    <RefreshCw className="w-8 h-8 text-pink-400 animate-spin" />
                    <p className="text-sm font-medium text-slate-300">Chunking file uploads to disk...</p>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-3">
                    <div className="w-12 h-12 rounded-xl bg-slate-900 flex items-center justify-center text-slate-400 group-hover:text-pink-400 transition-colors">
                      <Upload className="w-5 h-5" />
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
            <AlertCircle className="w-5 h-5 shrink-0" />
            <p>{uploadError}</p>
          </div>
        )}

        {/* Step 2: Settings Controls */}
        {file1 && (
          <section className="glass-panel rounded-2xl p-6 sm:p-8 space-y-6">
            <div className="flex items-center gap-3 mb-2">
              <Settings className="w-5 h-5 text-indigo-400" />
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
                      <CheckSquare className={`w-4 h-4 shrink-0 transition-opacity ${selectedFields.includes(field.id) ? "opacity-100 text-indigo-400" : "opacity-30"}`} />
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
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Executing Parallel Cleansing Pipeline...
                  </>
                ) : (
                  <>
                    <Settings className="w-4 h-4" />
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
                  <FileText className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                    Total Processed <Download className="w-3 h-3 text-slate-500 group-hover:text-indigo-400 transition-colors" />
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
                  <AlertTriangle className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                    Invalid Formats <Download className="w-3 h-3 text-slate-500 group-hover:text-amber-400 transition-colors" />
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
                  <Trash2 className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                    Duplicate Records <Download className="w-3 h-3 text-slate-500 group-hover:text-rose-400 transition-colors" />
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
                  <Layers className="w-6 h-6" />
                </div>
                <div>
                  <p className="text-xxs uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1">
                    Duplicate Clusters <Download className="w-3 h-3 text-slate-500 group-hover:text-purple-400 transition-colors" />
                  </p>
                  <p className="text-2xl font-black text-white mt-1 display font-display">
                    {processResult.metrics.unique_clusters.toLocaleString()}
                  </p>
                </div>
              </div>

            </section>

            {/* Explanation Guide */}
            <div className="glass-panel rounded-2xl p-5 border border-slate-800/80 bg-slate-950/20 text-xs text-slate-400 space-y-3 shadow-sm">
              <h4 className="font-bold text-white flex items-center gap-1.5"><Info className="w-4 h-4 text-indigo-400" /> Cleansing & Deduplication Metrics Explanation</h4>
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
                  <Info className="w-4 h-4 text-indigo-400 shrink-0" />
                  {selectedExportMode === "custom" ? (
                    <span>Manual selection active: <strong>{customExcludes.size}</strong> duplicates will be dropped in final file.</span>
                  ) : (
                    <span>Resolution set to <strong>{selectedExportMode === "keep_oldest" ? "Keep Oldest" : "Keep Newest"}</strong>. Filter runs automatically on download.</span>
                  )}
                </div>

                <div className="flex gap-4">
                  
                  {/* Download Excluded/Removed duplicates for QA */}
                  <button 
                    onClick={() => handleDownload("duplicates")}
                    disabled={isDownloading}
                    className="flex items-center gap-2 bg-slate-900 hover:bg-slate-800 text-slate-200 border border-slate-800 font-semibold py-2.5 px-5 rounded-xl text-xs transition-colors"
                  >
                    <Download className="w-4 h-4" />
                    Download Removed Duplicates (QA Audit Sheet)
                  </button>

                  {/* Clean export */}
                  <button 
                    onClick={() => handleDownload("clean")}
                    disabled={isDownloading}
                    className="glow-btn flex items-center gap-2 bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-600 hover:to-indigo-700 text-white font-semibold py-2.5 px-6 rounded-xl text-xs shadow-lg shadow-indigo-500/10"
                  >
                    <Download className="w-4 h-4" />
                    Download Cleaned File
                  </button>

                </div>
              </div>
            </section>

            {/* Virtualized Grid */}
            <section className="glass-panel rounded-2xl overflow-hidden border border-slate-800/80">
              
              <div className="p-5 border-b border-slate-800/80 flex justify-between items-center bg-slate-900/10">
                <div>
                  <h3 className="text-base font-bold text-white font-display">Non-Destructive Live UI Grid</h3>
                  <p className="text-xs text-slate-400 mt-0.5">Showing original raw fields side-by-side with pipeline transformations in real time.</p>
                </div>
                {selectedExportMode === "custom" && (
                  <span className="text-xxs px-2.5 py-1 rounded bg-yellow-500/10 border border-yellow-500/20 text-yellow-400 font-medium">
                    Checkout Mode: Check records to REMOVE/DROP
                  </span>
                )}
              </div>

              {/* Infinite Scroll Virtual Grid Container */}
              <div 
                ref={tableContainerRef}
                className="overflow-auto max-h-[600px] bg-[#0c101d]"
              >
                <div
                  style={{
                    height: `${rowVirtualizer.getTotalSize()}px`,
                    width: "100%",
                    position: "relative",
                  }}
                >
                  <table className="w-full border-collapse text-left text-xs absolute top-0 left-0">
                    <thead className="sticky top-0 bg-[#0e1324] text-slate-400 font-semibold border-b border-slate-800/80 z-10 shadow-md">
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
                      {virtualRows.map((virtualRow) => {
                        const record = getRecord(virtualRow.index);
                        const isOdd = virtualRow.index % 2 === 1;

                        if (!record) {
                          // Skeleton Loader Row
                          return (
                            <tr
                              key={virtualRow.key}
                              style={{
                                height: `${virtualRow.size}px`,
                                transform: `translateY(${virtualRow.start}px)`,
                              }}
                              className="absolute top-0 left-0 w-full flex items-center border-b border-slate-900/60"
                            >
                              <td className="px-4 py-3 w-full flex items-center gap-3">
                                <div className="h-4 bg-slate-900 animate-pulse rounded w-1/4"></div>
                                <div className="h-4 bg-slate-900 animate-pulse rounded w-1/3"></div>
                                <div className="h-4 bg-slate-900 animate-pulse rounded w-1/5"></div>
                              </td>
                            </tr>
                          );
                        }

                        // Colors and badges based on status
                        const isDuplicate = record.is_duplicate;
                        const isAlteredPhone = record.is_altered_phone;
                        const isValidPhone = record.is_valid_phone;
                        const isValidEmail = record.is_valid_email;
                        const clusterId = record.cluster_id;
                        const originalRowIndex = record.original_row_index;

                        // Highlight duplicate clusters using beautiful pastel backdrops
                        let rowBg = isOdd ? "bg-slate-900/20" : "bg-transparent";
                        if (isDuplicate) {
                          rowBg = "bg-rose-500/5 hover:bg-rose-500/10";
                        } else if (clusterId) {
                          rowBg = "bg-indigo-500/5 hover:bg-indigo-500/10";
                        }

                        return (
                          <tr
                            key={virtualRow.key}
                            style={{
                              height: `${virtualRow.size}px`,
                              transform: `translateY(${virtualRow.start}px)`,
                            }}
                            className={`absolute top-0 left-0 w-full flex items-center border-b border-slate-900/60 transition-colors ${rowBg}`}
                          >
                            
                            {/* Checkbox for custom QA exclusion */}
                            {selectedExportMode === "custom" && (
                              <td className="px-4 py-3 w-12 text-center flex items-center justify-center">
                                <input
                                  type="checkbox"
                                  checked={customExcludes.has(originalRowIndex) || isDuplicate}
                                  onChange={() => handleToggleExclude(originalRowIndex)}
                                  className="rounded border-slate-700 bg-slate-900 text-indigo-500 focus:ring-indigo-500 w-4 h-4"
                                />
                              </td>
                            )}

                            {tableHeaders.map((header) => {
                              let cellVal = record[header];
                              let content = <span className="text-slate-300">{String(cellVal !== null ? cellVal : "")}</span>;

                              // Render beautiful custom columns
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
                                      <Trash2 className="w-3 h-3" /> Duplicate
                                    </span>
                                  );
                                } else if (clusterId) {
                                  content = (
                                    <span className="px-2 py-0.5 rounded text-xxs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/10 flex items-center gap-1 w-max">
                                      <CheckCircle className="w-3 h-3" /> Keep Master
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
                                // Highlight field alterations and validation flags
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
                                          <AlertCircle className="w-2.5 h-2.5" /> Invalid
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
                                          <AlertCircle className="w-2.5 h-2.5" /> Invalid
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
              </div>

            </section>
          </>
        )}

      </main>
    </div>
  );
}
