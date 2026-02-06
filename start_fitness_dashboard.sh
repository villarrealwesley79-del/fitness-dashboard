#!/bin/bash
# Fitness Dashboard Startup Script
# Run this to start your fitness dashboard at the gym

cd "$(dirname "$0")"
echo "🏋️‍♀️ Starting Fitness Dashboard..."
echo "📂 Working directory: $(pwd)"

# Activate virtual environment
source venv/bin/activate

# Start the Flask app
echo "🚀 Starting server at http://localhost:5000"
echo "💡 Press Ctrl+C to stop"
echo ""

python3 app.py