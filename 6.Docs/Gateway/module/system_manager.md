# System Manager — Thiết kế module

> **Module:** `system/`
> **Phiên bản:** 2.0
> **Ngày:** 30/06/2026

---

## 1. Tổng quan

### Mục đích

`SystemManager` là orchestrator trung tâm — module duy nhất chịu trách nhiệm khởi tạo, kết nối và quản lý vòng đời của tất cả module trong hệ thống:

- **Orchestration** — khởi tạo module đúng thứ tự, dừng đúng thứ tự
- **Dependency Injection** — các module không tự khởi tạo, SystemManager inject dependencies
- **Lifecycle** — start/stop toàn bộ hệ thống qua một lệnh
- **Health** — tổng hợp trạng thái tất cả module

**Quy tắc thiết kế:** SystemManager không biết gì về MCP, HTTP, hay CLI. Nó chỉ quản lý module. MCP server nhận SystemManager qua DI.

### Vị trí trong hệ thống

```
main.py (composition root)
    │
    ▼
Config → SystemManager (orchestrator)
    │
    ├── EventBus + EventQueueManager    ← event_bus module
    ├── ReadingStore                    ← recorder module
    ├── DeviceManager + FleetTools      ← device_manager + mcp_server
    ├── RuleEngine                      ← rule_engine module
    └── NotifierManager                 ← notifier module
    │
    ▼
AgriMeshAIServer(system)               ← chỉ MCP protocol
```

### Ràng buộc thiết kế

- Không biết MCP, HTTP, CLI — pure orchestration
- Lifecycle rõ ràng: start theo thứ tự dependency, stop ngược lại
- Exception safety: nếu start fail → rollback tất cả
- Health check đầy đủ: mọi module đều có health check
- Module registry: extension có thể đăng ký module riêng

---

## 2. Kiến trúc

### Các thành phần

```
system/
├── __init__.py       Export: Config, SystemManager, Module, HealthStatus
├── config.py         Config dataclass — tất cả path config
├── manager.py        SystemManager — orchestrator trung tâm (~210 dòng)
└── module.py         Module ABC + HealthStatus
```

### Luồng khởi động

```
Config()
    │
    ▼
SystemManager(config)
    │
    ├── event_queue.start()             # 1. Queue worker
    ├── store.init()                    # 2. SQLite + WAL
    ├── device_manager.reload_catalog() # 3. Đọc TOML profiles
    ├── validate reserved names         # 4. Kiểm tra "fleet" conflict
    ├── device_manager.connect_all()    # 5. Kết nối adapters
    ├── subscribe event_queue → bus     # 6. Bridge queue → rule_engine
    ├── registered modules start()      # 7. Module mở rộng
    └── _running = True
```

### Luồng dừng

```
stop()
    │
    ├── registered modules stop()       # 1. Module mở rộng (reverse order)
    ├── device_manager.disconnect_all() # 2. Ngắt kết nối hardware
    ├── event_queue.stop()              # 3. Drain queue + stop worker
    ├── store.close()                   # 4. Đóng SQLite
    └── _running = False
```

Mỗi bước đều có try/except — nếu một bước fail, các bước sau vẫn chạy. Lỗi được gom lại log warning.

### Exception safety

```python
try:
    await self.event_queue.start()
    await self.store.init()
    self.device_manager.reload_catalog()
    ...
    self._running = True
except Exception:
    await self.stop()  # Rollback tất cả
    raise
```

---

## 3. Các thành phần

### 3.1 module.py — Module ABC

```python
class Module(ABC):
    """Base class cho tất cả system modules."""

    @abstractmethod
    async def start(self) -> None:
        """Start the module. Called once by SystemManager.start()."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop and cleanup. Called once by SystemManager.stop()."""

    @abstractmethod
    async def health(self) -> HealthStatus:
        """Return current health status."""


@dataclass
class HealthStatus:
    healthy: bool
    message: str = ""
```

### 3.2 config.py — Config

```python
@dataclass
class Config:
    config_dir: str | Path = "config"
    profiles_dir: str | Path = "device_manager/device_profiles"
    db_path: str | Path = "data/agrimesh.db"
    rules_path: str | Path = "config/rules.yaml"
    notifiers_path: str | Path = "config/notifiers.yaml"
```

**Xử lý path:**
- `__post_init__` tự động `mkdir(parents=True, exist_ok=True)` nếu path chưa tồn tại
- Tất cả path được `.resolve()` về absolute
- `db_path.parent` được tạo nếu chưa có

### 3.3 manager.py — SystemManager

```python
class SystemManager:
    def __init__(self, config: Config)

    # Module registry
    def register_module(name: str, module: Module)

    # Lifecycle
    async def start() -> DiscoveryResult
    async def stop()

    # Tool routing (hợp nhất device + fleet)
    def list_tools() -> list[Tool]
    async def call_tool(name: str, args: dict) -> AdapterResult

    # Health
    async def health() -> dict[str, HealthStatus]
```

**Các module được khởi tạo:**

| Attribute | Class | File |
|-----------|-------|------|
| `event_bus` | `EventBus` | `event_bus/bus.py` |
| `event_queue` | `EventQueueManager` | `event_bus/manager.py` |
| `store` | `ReadingStore` | `database_manager/store.py` |
| `device_manager` | `DeviceManager` | `device_manager/manager.py` |
| `rule_engine` | `RuleEngine` | `rule_engine/engine.py` |
| `notifier` | `NotifierManager` | `notifier/manager.py` |
| `fleet` | `FleetTools` | `mcp_server/fleet.py` |

---

## 4. Tool routing

SystemManager hợp nhất tools từ DeviceManager (device tools) và FleetTools (fleet tools):

```python
def list_tools(self) -> list[Tool]:
    return self.device_manager.tools + self.fleet.tools

async def call_tool(self, name: str, args: dict) -> AdapterResult:
    from mcp_server.adapters.base import AdapterResult

    if not name or "." not in name:
        return AdapterResult.fail(f"invalid tool name: {name}")

    if name.startswith("fleet."):
        try:
            result = await self.fleet.call(name, args)
            if isinstance(result, dict) and "error" in result:
                return AdapterResult.fail(result["error"])
            return AdapterResult.ok(result)
        except Exception as e:
            return AdapterResult.fail(str(e))

    return await self.device_manager.call_tool(name, args)
```

**Routing rules:**
- Tool name phải có dạng `device_name.tool_name`
- Prefix `fleet.` → route đến FleetTools
- Còn lại → route đến DeviceManager
- Nếu không hợp lệ → `AdapterResult.fail`

**Reserved names:** Device name `fleet` bị cấm (validate trong `DeviceManager._init_catalog()`).

---

## 5. Health check

```python
async def health(self) -> dict[str, HealthStatus]:
    checks = [
        ("store", self._check_store()),
        ("device_manager", self._check_devices()),
        ("event_queue", self._check_queue()),
        ("rule_engine", self._check_rule_engine()),
        ("notifier", self._check_notifier()),
    ]
    for name, coro in checks:
        try:
            result[name] = await coro
        except Exception as e:
            result[name] = HealthStatus(healthy=False, message=str(e))
    # + registered modules
```

| Check | Logic | Healthy khi |
|-------|-------|-------------|
| `store` | `store is not None` | Store đã init |
| `device_manager` | Tất cả device connected | Không device disconnected |
| `event_queue` | `dlq_size < 10` | DLQ dưới ngưỡng |
| `rule_engine` | `rule_engine is not None` | Đã khởi tạo |
| `notifier` | Có ít nhất 1 channel | Console hoặc Telegram/SMS |

---

## 6. Extension: đăng ký module riêng

```python
from system import Module, HealthStatus

class MySensor(Module):
    async def start(self): ...
    async def stop(self): ...
    async def health(self) -> HealthStatus:
        return HealthStatus(healthy=True)

system.register_module("my_sensor", MySensor())
```

---

## 7. Quyết định thiết kế

### Tại sao không để AgriMeshAIServer tự khởi tạo module?

| Tiêu chí | Server tự khởi tạo | SystemManager |
|----------|-------------------|---------------|
| SRP | Server vừa MCP vừa orchestration | Server chỉ MCP |
| Tái sử dụng | Muốn dùng rule engine → qua MCP | `system.rule_engine` trực tiếp |
| Test | Phải mock MCP | Test SystemManager riêng, MCP riêng |

### Exception safety

`start()` dùng try-except → gọi `stop()` để rollback. Không module nào bị mồ côi nếu start fail.

### Stop không fail-fast

`stop()` dùng try/except từng bước, gom lỗi vào list. Module vẫn được dừng dù module trước fail.

### Reserved names

Device name `fleet` bị cấm vì tool routing dùng prefix `fleet.` để phân biệt fleet tools với device tools.

---

## 8. Composition root (main.py)

```python
def run_agent(...):
    config = Config(profiles_dir="device_manager/device_profiles")
    system = SystemManager(config)
    asyncio.run(system.start())

    tools = _build_tool_bridge(system, loop)
    agent = Agent(provider=OllamaProvider(...), tools=tools)
    Session(agent=agent).start()

    asyncio.run(system.stop())

def run_daemon(...):
    config = Config(...)
    system = SystemManager(config)
    server = AgriMeshAIServer(system)
    asyncio.run(server.run_daemon(host=host, port=port))
```

---

## 9. Giới hạn

- **Module registry chỉ start/stop** — chưa có dependency graph
- **Health check đơn giản** — chưa có timeout, retry health check
- **FleetTools phụ thuộc DeviceManager** — không thể dùng fleet riêng
- **NotifierManager không có stop()** — chưa cleanup resources

### ✅ Đã giải quyết (v2.0)

- **Hot-reload detector config:** `config_updated` EventBus → MLDetector reconfigure params, enable/disable — không restart
- **Health reporting:** `DetectorHealth` dataclass, `get_health()`, periodic `detector_health` event (60s)
- **EnrichmentPipeline:** Tự động gắn 24h context + LLM explanation vào alert (best-effort)
- **Stop sequence:** EnrichmentPipeline task được cancel sạch khi SystemManager stop

---

## 10. Ví dụ

```python
from system import Config, SystemManager

config = Config(profiles_dir="device_manager/device_profiles")
system = SystemManager(config)
await system.start()

tools = system.list_tools()          # 17 tools
devices = system.device_manager.device_names  # 4 devices
health = await system.health()       # 5 checks

await system.stop()
```

---

## Tham khảo

- Mode Service — github.com/ask/mode
- facet — pypi.org/project/facet/
- EdgeX SMA — docs.edgexfoundry.org
- Cosmic Python DI — cosmicpython.com
