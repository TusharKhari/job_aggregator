#!/usr/bin/env bash
# ==============================================================================
# Unified Job Aggregator & Google Sheets Sync Pipeline
# 
# 1. Activates .venv in /Users/tushar/Documents/intp/job_aggregator
# 2. Runs 'python aggregator.py --limit-queries 0'
# 3. Activates .venv in /Users/tushar/Documents/intp/jobs_to_Gsheets
# 4. Runs '.venv/bin/python upload_to_sheets.py aggregated'
# 5. Logs full terminal output to /Users/tushar/Documents/intp/job_aggregator/logs/
# ==============================================================================

set -eo pipefail

AGGREGATOR_DIR="/Users/tushar/Documents/intp/job_aggregator"
GSHEETS_DIR="/Users/tushar/Documents/intp/jobs_to_Gsheets"
LOGS_DIR="$AGGREGATOR_DIR/logs"

mkdir -p "$LOGS_DIR"

TIMESTAMP=$(date '+%Y_%m_%d_%H_%M_%S')
LOG_FILE="$LOGS_DIR/pipeline_${TIMESTAMP}.log"
LATEST_LOG="$LOGS_DIR/pipeline_latest.log"

# Duplicate all stdout and stderr to both console and log files
exec > >(tee -a "$LOG_FILE" > "$LATEST_LOG") 2>&1

echo "=================================================================="
echo "🚀 STARTING JOB PIPELINE AT $(date)"
echo "📄 Log file: $LOG_FILE"
echo "=================================================================="

# Ensure Chrome is running on port 9222 for Xing & StepStone CDP
if ! curl -s http://localhost:9222/json/version >/dev/null 2>&1; then
    echo "[i] Chrome not detected on port 9222. Launching background Chrome..."
    if [ -d "/Applications/Google Chrome.app" ]; then
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
            --remote-debugging-port=9222 \
            --user-data-dir="$HOME/xing_clean_profile" \
            --no-first-run \
            --no-default-browser-check >/dev/null 2>&1 &
        sleep 3
    else
        echo "[!] Warning: Chrome not found at standard path. Xing/StepStone CDP may fail."
    fi
else
    echo "[✓] Chrome CDP detected active on port 9222."
fi

# ------------------------------------------------------------------------------
# STEP 1: Aggregate Jobs
# ------------------------------------------------------------------------------
echo ""
echo "=================================================================="
echo "▶ STEP 1: Aggregating jobs in $AGGREGATOR_DIR"
echo "  Command: python aggregator.py --limit-queries 0"
echo "=================================================================="

cd "$AGGREGATOR_DIR"

if [ ! -d ".venv" ]; then
    echo "[!] Error: .venv not found in $AGGREGATOR_DIR"
    exit 1
fi

source .venv/bin/activate
python aggregator.py --limit-queries 0 "$@"
deactivate

# ------------------------------------------------------------------------------
# STEP 2: Upload to Google Sheets
# ------------------------------------------------------------------------------
echo ""
echo "=================================================================="
echo "▶ STEP 2: Uploading aggregated jobs to Google Sheets"
echo "  Directory: $GSHEETS_DIR"
echo "  Command:   .venv/bin/python upload_to_sheets.py aggregated"
echo "=================================================================="

cd "$GSHEETS_DIR"

if [ ! -d ".venv" ]; then
    echo "[!] Error: .venv not found in $GSHEETS_DIR"
    exit 1
fi

source .venv/bin/activate
.venv/bin/python upload_to_sheets.py aggregated
deactivate

echo ""
echo "=================================================================="
echo "🎉 PIPELINE COMPLETED SUCCESSFULLY AT $(date)"
echo "📄 Log saved to: $LOG_FILE"
echo "=================================================================="
