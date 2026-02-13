#!/usr/bin/env bash
#
# Setup cron jobs for automated FEC and CalAccess data updates.
# Staggered schedules to avoid resource contention on Raspberry Pi:
#   - FEC:       Sundays at 2:00 AM
#   - CalAccess: Wednesdays at 3:00 AM
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
LOG_DIR="${SCRIPT_DIR}/logs"

# Ensure logs directory exists
mkdir -p "$LOG_DIR"

# Check for venv python
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Warning: venv Python not found at $VENV_PYTHON"
    echo "Using system python3 instead"
    VENV_PYTHON="$(which python3)"
fi

# Define cron entries
FEC_CRON="0 2 * * 0 cd ${SCRIPT_DIR} && ${VENV_PYTHON} ${SCRIPT_DIR}/update_fec.py >> ${LOG_DIR}/cron_fec.log 2>&1"
CA_CRON="0 3 * * 3 cd ${SCRIPT_DIR} && ${VENV_PYTHON} ${SCRIPT_DIR}/CA/update_calaccess.py >> ${LOG_DIR}/cron_calaccess.log 2>&1"

# Marker comments to identify our cron entries
FEC_MARKER="# FEC weekly update"
CA_MARKER="# CalAccess weekly update"

# Get existing crontab (suppress "no crontab" error)
EXISTING_CRONTAB=$(crontab -l 2>/dev/null || true)

# Remove any existing FEC/CalAccess entries (by marker)
CLEANED_CRONTAB=$(echo "$EXISTING_CRONTAB" | grep -v "$FEC_MARKER" | grep -v "update_fec.py" | grep -v "$CA_MARKER" | grep -v "update_calaccess.py" || true)

# Add new entries
NEW_CRONTAB="${CLEANED_CRONTAB}
${FEC_MARKER}
${FEC_CRON}
${CA_MARKER}
${CA_CRON}
"

# Remove leading/trailing blank lines and install
echo "$NEW_CRONTAB" | sed '/^$/N;/^\n$/d' | crontab -

echo "Cron jobs installed successfully:"
echo ""
echo "FEC Update:       Sundays at 2:00 AM"
echo "CalAccess Update: Wednesdays at 3:00 AM"
echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "Log files:"
echo "  FEC:       ${LOG_DIR}/cron_fec.log"
echo "  CalAccess: ${LOG_DIR}/cron_calaccess.log"
echo "  Detailed:  ${LOG_DIR}/update_fec.log"
echo "  Detailed:  ${LOG_DIR}/update_calaccess.log"
