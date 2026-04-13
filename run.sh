#!/bin/bash
# run.sh — start both services
# Usage: ./run.sh

set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Copy .env.example and fill in TELEGRAM_BOT_TOKEN."
  exit 1
fi

echo "📦 Installing dependencies..."
pip install -r requirements.txt -q

echo "🚀 Starting FastAPI engine on :8000..."
uvicorn coach_core.main:app --host 0.0.0.0 --port 8000 --reload &
API_PID=$!

sleep 2

echo "🤖 Starting Telegram bot..."
python -m telegram_bot.bot &
BOT_PID=$!

echo ""
echo "✅ Run Coach is live."
echo "   API: http://localhost:8000/docs"
echo "   API PID: $API_PID"
echo "   Bot PID: $BOT_PID"
echo ""
echo "Press Ctrl+C to stop both."

trap "kill $API_PID $BOT_PID 2>/dev/null; echo 'Stopped.'" INT TERM
wait
