#!/bin/bash
# Campaign Finance Apps Startup Script

echo "🚀 Campaign Finance Search Applications"
echo "======================================"
echo ""

# Check if conda is available and fec environment exists
if command -v conda &> /dev/null; then
    if conda env list | grep -q "fec"; then
        echo "📋 Activating conda environment 'fec'..."
        eval "$(conda shell.bash hook)"
        conda activate fec
    else
        echo "⚠️  Note: 'fec' conda environment not found. Using current environment."
    fi
else
    echo "⚠️  Note: conda not found. Using current Python environment."
fi

echo ""
echo "Choose how you want to run the applications:"
echo ""
echo "1) 🔄 Unified App (Recommended) - Single app with database toggle on port 5000"
echo "2) ⚡ Both Apps Separately - FEC on :5000, CA on :5001" 
echo "3) 🇺🇸 FEC Only - National federal data"
echo "4) 🏛️ CA Only - California state data"
echo ""
read -p "Enter your choice (1-4): " choice

case $choice in
    1)
        echo "🔄 Starting Unified App on http://localhost:5000"
        echo "   Toggle between FEC ↔ CA databases using the switch button"
        python3 unified_app.py
        ;;
    2) 
        echo "⚡ Starting both apps separately..."
        echo "🇺🇸 FEC App: http://localhost:5000"
        echo "🏛️ CA App: http://localhost:5001"
        python3 app.py &
        cd CA && python3 ca_app_simple.py
        ;;
    3)
        echo "🇺🇸 Starting FEC App only on http://localhost:5000"
        python3 app.py
        ;;
    4)
        echo "🏛️ Starting Unified App (CA Default) on http://localhost:5000"
        python3 unified_app.py --default-db ca
        ;;
    *)
        echo "❌ Invalid choice. Please run the script again."
        exit 1
        ;;
esac
