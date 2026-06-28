#!/bin/bash
# AI Cover Studio - Start Script

set -e
cd "$(dirname "$0")/backend"

echo "🎵 AI Cover Studio Phase 1"
echo "================================"

# Create dirs
mkdir -p uploads outputs models/voices

# Copy frontend to static
mkdir -p frontend/dist
cp -r ../frontend/index.html frontend/dist/index.html 2>/dev/null || true

echo "✅ Directories ready"
echo "🚀 Starting FastAPI server on http://0.0.0.0:8080"
echo "   API Docs: http://localhost:8080/docs"
echo "   Studio:   http://localhost:8080/"
echo ""

python3 -m uvicorn main:app \
  --host 0.0.0.0 \
  --port 8080 \
  --reload \
  --log-level info
