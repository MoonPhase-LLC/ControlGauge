"""Evidence ingestion limits (owner decision PD-016, resolving
`OPEN_QUESTIONS.md` S-2; `SECURITY.md` T-02/T-03/T-04).

One place for every bound, shared by the upload route, the parser worker
and the sandbox. Raising one is a code change reviewed like any other.
"""

from __future__ import annotations

from .sandbox import SandboxLimits

MIB = 1024 * 1024

# --- upload (checked before anything is stored or parsed) ------------------
MAX_FILE_BYTES = 50 * MIB
#: Enforced by the desktop app per selection (the service accepts one file
#: per request); listed here so the documented limits live in one place.
MAX_FILES_PER_BATCH = 50
MAX_ARTIFACTS_PER_ASSESSMENT = 1000
MAX_ASSESSMENT_BYTES = 2 * 1024 * MIB
MAX_FILENAME_CHARS = 255

# --- parsing (inside the worker) ---------------------------------------------
MAX_PDF_PAGES = 2000
MAX_CSV_ROWS = 200_000
MAX_CSV_COLUMNS = 500
#: DOCX (OOXML zip) bounds, checked from the zip directory before inflating
#: and again while inflating (a directory can lie about sizes).
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_TOTAL_UNCOMPRESSED = 200 * MIB
MAX_ZIP_RATIO = 100
#: Extracted text kept per file. Beyond it the artifact is `partial`.
MAX_EXTRACTED_CHARS = 10 * MIB
#: Target size of one artifact section (chunk).
SECTION_TARGET_CHARS = 4000
MAX_SECTIONS = 5000

# --- worker process (the sandbox's Job Object + service-side bounds) --------
WORKER_LIMITS = SandboxLimits(
    memory_bytes=1024 * MIB,
    cpu_seconds=60,
    wall_seconds=120,
    # 10 MiB of text as JSON-escaped UTF-8 can grow; this caps the pipe.
    output_bytes=48 * MIB,
)
