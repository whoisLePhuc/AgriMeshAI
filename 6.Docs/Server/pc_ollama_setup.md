# PC Ollama Server Setup — AgriMeshAI

> **Mục đích:** Cấu hình PC (RTX 3050, 6GB VRAM) làm LLM Server để Edge Gateway (Jetson Nano) kết nối qua Tailscale VPN.
> **Phiên bản:** 1.0 | **Ngày:** 12/06/2026

---

## 1. Yêu cầu phần cứng

| Thành phần | Tối thiểu | Khuyến nghị |
|-----------|----------|------------|
| GPU | 4GB VRAM | 6GB+ VRAM (RTX 3050 trở lên) |
| RAM | 8GB | 16GB |
| OS | Ubuntu 22.04 | Ubuntu 22.04 / 24.04 |
| Network | Internet + Tailscale | Tailscale MagicDNS enabled |

---

## 2. Cài Ollama + pull model

### 2.1 Cài Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Verify:
```bash
ollama --version
# ollama version is 0.5.x
```

### 2.2 Pull model Qwen2.5 7B

```bash
# Q4_K_M quantization — cân bằng tốc độ / chất lượng
ollama pull qwen2.5:7b

# Kiểm tra model đã có
ollama list
# NAME            ID              SIZE      MODIFIED
# qwen2.5:7b     845d7e5e5e5e    4.7 GB    2 days ago
```

> **VRAM usage:** Qwen2.5 7B Q4_K_M dùng ~4.7GB VRAM. RTX 3050 6GB còn ~1.3GB headroom.

### 2.3 Test local

```bash
ollama run qwen2.5:7b "Hello, respond in one sentence."
# → Hello! How can I assist you today?
```

---

## 3. Cấu hình Ollama listen 0.0.0.0

Mặc định Ollama chỉ listen `127.0.0.1` — không cho phép kết nối từ máy khác. Cần mở ra `0.0.0.0:11434`.

### 3.1 Dùng systemd (khuyến nghị)

```bash
sudo systemctl edit ollama.service
```

Dán nội dung sau vào file editor mở ra:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=5m"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

| Biến | Ý nghĩa |
|------|---------|
| `OLLAMA_HOST=0.0.0.0:11434` | Listen mọi interface, port 11434 |
| `OLLAMA_KEEP_ALIVE=5m` | Giữ model trong VRAM 5 phút sau request cuối |
| `OLLAMA_NUM_PARALLEL=1` | Xử lý 1 request tại một thời điểm (RTX 3050 có 6GB) |
| `OLLAMA_MAX_LOADED_MODELS=1` | Chỉ load 1 model (tiết kiệm VRAM) |

Áp dụng:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
sudo systemctl status ollama
```

### 3.2 Verify listen

```bash
ss -tlnp | grep 11434
# LISTEN  0  4096  0.0.0.0:11434  0.0.0.0:*
```

Nếu thấy `0.0.0.0:11434` → OK. Nếu chỉ `127.0.0.1:11434` → chưa cấu hình đúng.

---

## 4. Cài & cấu hình Tailscale

### 4.1 Cài trên PC

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Sau lệnh trên, mở link hiện ra trong browser để authenticate.

### 4.2 Lấy IP Tailscale

```bash
tailscale ip -4
# → 100.125.217.6
```

Ghi lại IP này — sẽ dùng trong `config/models.yaml` ở Edge Gateway.

### 4.3 Enable MagicDNS (tùy chọn)

```bash
# Trên Tailscale admin console: https://login.tailscale.com/admin/dns
# Bật "MagicDNS" → có thể dùng hostname thay vì IP
# Ví dụ: http://agrimesh-llm:11434/v1
```

---

## 5. Firewall (UFW)

### 5.1 Cho phép Tailscale

```bash
sudo ufw allow in on tailscale0
sudo ufw allow 41641/udp   # Tailscale direct connect
```

### 5.2 KHÔNG mở port 11434 ra internet

```bash
# ❌ KHÔNG làm:
# sudo ufw allow 11434

# ✅ Chỉ cần rule Tailscale ở trên là đủ
# Tailscale tự tạo tunnel, không cần mở port công khai
```

### 5.3 Verify

```bash
sudo ufw status verbose
# tailscale0             ALLOW IN    Anywhere
# 41641/udp              ALLOW IN    Anywhere
```

---

## 6. Test kết nối từ Edge Gateway

Trên Jetson Nano (đã cài Tailscale + kết nối cùng network):

```bash
# Test Tailscale reachability
ping 100.125.217.6

# Test Ollama API
curl http://100.125.217.6:11434/api/tags
# → {"models":[{"name":"qwen2.5:7b","model":"qwen2.5:7b",...}]}

# Test inference
curl http://100.125.217.6:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:7b",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.2,
    "max_tokens": 100
  }'
# → {"choices":[{"message":{"content":"Hello! How can I..."}}]}
```

---

## 7. Cấu hình Edge Gateway

Trên Jetson Nano, sửa `config/models.yaml`:

```yaml
llm:
  provider: ollama
  model: qwen2.5:7b
  api_url: http://100.125.217.6:11434/v1   # ← IP Tailscale của PC
  max_tokens: 4096
  temperature: 0.2
  keep_alive: 5m
```

Khởi động agent:

```bash
cd /path/to/AgriMeshAI
python main.py agent
# ✓ Gateway ready — 1 device(s), 6 tool(s)
# ✓ Agent ready (model: qwen2.5:7b)
```

---

## 8. Auto-start trên PC

### 8.1 Ollama auto-start (đã tự động sau khi cài)

```bash
sudo systemctl enable ollama
```

### 8.2 Tailscale auto-start

```bash
sudo systemctl enable tailscaled
```

---

## 9. Monitoring

### 9.1 Check Ollama đang chạy

```bash
systemctl status ollama
ollama ps
# NAME            ID              SIZE      PROCESSOR    UNTIL
# qwen2.5:7b     845d7e5e5e5e    5.6 GB    100% GPU     4 minutes from now
```

### 9.2 Check GPU usage

```bash
nvidia-smi
# +-----------------------------------------------------------------------------+
# | Processes:                                                                  |
# |  GPU   GI   CI        PID   Type   Process name                  GPU Memory |
# |   0     N/A  N/A     12345      C   ollama_llama_server            4668MiB  |
# +-----------------------------------------------------------------------------+
```

### 9.3 Check Tailscale status

```bash
tailscale status
# 100.125.217.6  agrimesh-pc        phuc@      linux   active; direct
# 100.91.80.113  agrimesh-jetson    phuc@      linux   active; direct
```

---

## 10. Troubleshooting

| Vấn đề | Kiểm tra | Fix |
|--------|---------|-----|
| Edge không curl được | `ss -tlnp \| grep 11434` trên PC | Đảm bảo `OLLAMA_HOST=0.0.0.0:11434` |
| `ollama serve` báo CUDA error | `nvidia-smi` | `sudo apt install nvidia-driver-535` |
| Tailscale không direct connect | `tailscale status` | Mở port 41641/udp trên firewall |
| Model không load được | `ollama list` | `ollama pull qwen2.5:7b` |
| Out of memory | `nvidia-smi` — VRAM > 5GB | Dùng model nhỏ hơn: `qwen2.5:1.5b` |
| Response chậm (>10s) | Check `nvidia-smi` — GPU usage | Tăng `OLLAMA_KEEP_ALIVE`, giảm `OLLAMA_NUM_PARALLEL` |
