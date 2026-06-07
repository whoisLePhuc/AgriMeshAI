#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "============================================"
echo "  AgriMeshAI — Starting Agent"
echo "============================================"
echo ""

# 1. Kiểm tra Ollama
if ! command -v ollama &>/dev/null; then
  echo "✗ Ollama chưa được cài đặt."
  echo "  Xem hướng dẫn trong README.md để cài Ollama."
  exit 1
fi
echo "✓ Ollama đã cài"

# 2. Kiểm tra Ollama service
if ! curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "→ Ollama chưa chạy. Đang khởi động..."
  ollama serve &>/dev/null &
  OLLAMA_PID=$!
  for i in $(seq 1 10); do
    if curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
      echo "✓ Ollama service đã sẵn sàng"
      break
    fi
    if [ "$i" -eq 10 ]; then
      echo "✗ Ollama không khởi động được (PID: $OLLAMA_PID)"
      exit 1
    fi
    sleep 1
  done
else
  echo "✓ Ollama service đang chạy"
fi

# 3. Lấy model name từ config
MODEL=$(python3 -c "
import yaml
with open('config/models.yaml') as f:
    c = yaml.safe_load(f)
print(c['llm']['model'])
")

# 4. Kiểm tra model
if ollama list 2>/dev/null | grep -q "$MODEL"; then
  echo "✓ Model $MODEL đã sẵn sàng"
else
  echo "→ Model $MODEL chưa có. Đang pull..."
  ollama pull "$MODEL"
  echo "✓ Model $MODEL đã sẵn sàng"
fi

# 5. Activate venv (nếu có)
if [ -d "venv" ]; then
  source venv/bin/activate
fi

# 6. Chạy agent
echo ""
echo "============================================"
echo "  Starting AI Agent..."
echo "============================================"
python3 agent/main.py
