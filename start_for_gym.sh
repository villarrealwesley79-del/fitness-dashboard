#!/bin/bash
# Fitness Dashboard for Gym Access
# Creates public URL for cellular access

cd "$(dirname "$0")"
echo "🏋️‍♀️ Starting Fitness Dashboard for Gym Access"
echo "📱 This will create a public URL for your phone"
echo ""

# Start Flask app in background
source venv/bin/activate
echo "🚀 Starting Flask app..."
python3 app.py &
FLASK_PID=$!

# Wait for Flask to start
sleep 3

# Start ngrok tunnel
echo "🌐 Creating public tunnel with ngrok..."
echo "📋 Your gym URL will be displayed below:"
echo ""
ngrok http 5050

# Cleanup when script ends
trap "kill $FLASK_PID 2>/dev/null" EXIT