import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


HOME = Path.home()
SOURCE_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = SOURCE_ROOT.parent.parent
LOCAL_CONFIG_PATH = SOURCE_ROOT / "bridge-console.local.json"
LEGACY_CTI_HOME = HOME / ".claude-to-im"
INSTANCE_HOMES = {
    "feishu": HOME / ".claude-to-im-feishu",
    "qq": HOME / ".claude-to-im-qq",
}
INSTANCE_LABELS = {"feishu": "飞书桥接", "qq": "QQ 桥接"}
HAPPY_HOME = HOME / ".happy"
HAPPY_DAEMON = HAPPY_HOME / "daemon.state.json"
CODEX_HOME = HOME / ".codex"
CODEX_AUTH = CODEX_HOME / "auth.json"
MANAGER_HOME = HOME / ".bridge-console"
MANAGER_SETTINGS = MANAGER_HOME / "settings.json"
PROFILE_HOME = MANAGER_HOME / "codex-profiles"
DEFAULT_CTI_SOURCE = WORKSPACE_ROOT / "Claude-to-IM-skill" / "Claude-to-IM-skill-source"
APP_ID = "Develop.BridgeConsole"


def load_local_config() -> dict:
    try:
        return json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_config_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = (SOURCE_ROOT / target).resolve()
    return target


LOCAL_CONFIG = load_local_config()
PROJECT_ROOT = resolve_config_path(
    LOCAL_CONFIG.get("ctiSource") or os.environ.get("BRIDGE_CONSOLE_CTI_SOURCE"), DEFAULT_CTI_SOURCE
)
APP_ICON = resolve_config_path(
    LOCAL_CONFIG.get("appIcon") or os.environ.get("BRIDGE_CONSOLE_APP_ICON"),
    SOURCE_ROOT / "assets" / "bridge-console.ico",
)


def read_text(path: Path, fallback: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return fallback


def read_json(path: Path, fallback):
    try:
        return json.loads(read_text(path))
    except Exception:
        return fallback


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_env(path: Path) -> dict:
    data = {}
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def write_env(env_path: Path, data: dict) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in data.items()]
    env_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def tail_lines(path: Path | None, count: int = 120) -> str:
    if not path:
        return ""
    return "\n".join(read_text(path).splitlines()[-count:])


_CREATE_NO_WINDOW = 0x08000000


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)


def apply_windows_app_identity() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def path_ok(target: str) -> bool:
    try:
        return bool(target) and Path(target).exists() and Path(target).is_dir()
    except Exception:
        return False


def pid_alive(pid) -> bool:
    try:
        if not pid:
            return False
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def daemon_pids(instance: str | None = None, unlabeled_only: bool = False) -> list[int]:
    if os.name != "nt":
        return []
    filters = [
        "$_.Name -eq 'node.exe'",
        "$_.CommandLine -match 'dist[/\\\\]daemon\\.mjs'",
    ]
    if instance:
        filters.append(f"$_.CommandLine -match '--instance={instance}'")
    if unlabeled_only:
        filters.append("$_.CommandLine -notmatch '--instance='")
    command = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ {' -and '.join(filters)} }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            creationflags=_no_window_flags(),
        )
        pids = []
        for raw in result.stdout.splitlines():
            raw = raw.strip()
            if raw.isdigit():
                pids.append(int(raw))
        return pids
    except Exception:
        return []


def kill_pids(pids: list[int]) -> list[int]:
    killed = []
    for pid in sorted({int(pid) for pid in pids if pid}):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=_no_window_flags(),
            )
            killed.append(pid)
        except Exception:
            pass
    return killed


def stop_instance_processes(instance: str) -> list[int]:
    status = read_json(instance_paths(instance)["status"], {})
    known_pid = status.get("pid")
    matching = daemon_pids(instance=instance)
    stale_unlabeled = daemon_pids(unlabeled_only=True)
    killed = kill_pids(([known_pid] if known_pid else []) + matching + stale_unlabeled)
    if killed:
        status["running"] = False
        status["lastCheckedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_json(instance_paths(instance)["status"], status)
    return killed


def decode_jwt_payload(token: str) -> dict:
    if not token or "." not in token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


def codex_account_info() -> dict:
    auth = read_json(CODEX_AUTH, {})
    tokens = auth.get("tokens", {})
    id_payload = decode_jwt_payload(tokens.get("id_token", ""))
    access_payload = decode_jwt_payload(tokens.get("access_token", ""))
    auth_payload = id_payload.get("https://api.openai.com/auth", {}) or access_payload.get(
        "https://api.openai.com/auth", {}
    )
    profile_payload = access_payload.get("https://api.openai.com/profile", {})
    orgs = auth_payload.get("organizations", [])
    default_org = next((org for org in orgs if org.get("is_default")), orgs[0] if orgs else {})
    return {
        "email": profile_payload.get("email") or id_payload.get("email") or "-",
        "provider": id_payload.get("auth_provider", "-"),
        "plan": auth_payload.get("chatgpt_plan_type", "-"),
        "mode": auth.get("auth_mode", "-"),
        "org": default_org.get("title", "-"),
        "last_refresh": auth.get("last_refresh", "-"),
    }


def ensure_manager() -> dict:
    MANAGER_HOME.mkdir(parents=True, exist_ok=True)
    PROFILE_HOME.mkdir(parents=True, exist_ok=True)
    data = read_json(MANAGER_SETTINGS, None)
    if data is None:
        data = {
            "projectPresets": [],
            "happyRecent": [],
            "codexProfiles": [],
        }
    if "projectPresets" not in data and "happyPresets" in data:
        data["projectPresets"] = data.get("happyPresets", [])
    data.setdefault("projectPresets", [])
    data.setdefault("happyRecent", [])
    data.setdefault("codexProfiles", [])
    write_json(MANAGER_SETTINGS, data)
    return data


def instance_paths(instance: str) -> dict:
    home = INSTANCE_HOMES[instance]
    return {
        "home": home,
        "config": home / "config.env",
        "status": home / "runtime" / "status.json",
        "log": home / "logs" / "bridge.log",
        "error_log": home / "logs" / "bridge.err.log",
        "audit": home / "data" / "audit.json",
        "bindings": home / "data" / "bindings.json",
    }


def legacy_paths() -> dict:
    return {
        "home": LEGACY_CTI_HOME,
        "config": LEGACY_CTI_HOME / "config.env",
        "status": LEGACY_CTI_HOME / "runtime" / "status.json",
        "log": LEGACY_CTI_HOME / "logs" / "bridge.log",
        "audit": LEGACY_CTI_HOME / "data" / "audit.json",
        "bindings": LEGACY_CTI_HOME / "data" / "bindings.json",
    }


def build_instance_env(instance: str) -> dict:
    source = parse_env(LEGACY_CTI_HOME / "config.env") if (LEGACY_CTI_HOME / "config.env").exists() else {}
    source.setdefault("CTI_RUNTIME", "codex")
    source.setdefault("CTI_DEFAULT_MODE", "code")
    source.setdefault("CTI_DEFAULT_WORKDIR", str(HOME))
    source["CTI_ENABLED_CHANNELS"] = instance
    return source


def read_legacy_state() -> dict:
    paths = legacy_paths()
    status = read_json(paths["status"], {})
    env = parse_env(paths["config"])
    channels = status.get("channels") or env.get("CTI_ENABLED_CHANNELS", "").split(",")
    channels = [item.strip() for item in channels if item and item.strip()]
    running = bool(status.get("running")) and pid_alive(status.get("pid"))
    if status and status.get("running") != running:
        status["running"] = running
        if not running:
            status["lastCheckedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_json(paths["status"], status)
    return {"paths": paths, "status": status, "channels": channels}


def stop_legacy_bridge_for(instance: str | None = None) -> tuple[bool, str]:
    legacy = read_legacy_state()
    status = legacy["status"]
    channels = legacy["channels"]
    if not status.get("running"):
        return False, ""
    if instance and instance not in channels:
        return False, ""
    pid = status.get("pid")
    if not pid:
        return False, ""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=_no_window_flags(),
    )
    status["running"] = False
    status["lastCheckedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_json(legacy["paths"]["status"], status)
    channel_text = ",".join(channels) if channels else "legacy"
    return True, f"已先停止旧合并桥接（{channel_text}，PID {pid}）。"


def ensure_instance_config(instance: str) -> None:
    paths = instance_paths(instance)
    for sub in ["data", "logs", "runtime", "data/messages"]:
        (paths["home"] / sub).mkdir(parents=True, exist_ok=True)
    if not paths["config"].exists():
        write_env(paths["config"], build_instance_env(instance))
    if not paths["bindings"].exists():
        legacy_bindings = read_json(LEGACY_CTI_HOME / "data" / "bindings.json", {})
        filtered = {
            key: value
            for key, value in legacy_bindings.items()
            if value.get("channelType") == instance
        }
        write_json(paths["bindings"], filtered)
    if not paths["audit"].exists():
        legacy_audit = read_json(LEGACY_CTI_HOME / "data" / "audit.json", [])
        filtered_audit = [item for item in legacy_audit if item.get("channelType") == instance]
        write_json(paths["audit"], filtered_audit)


def update_instance_default_workdir(instance: str, target: str) -> None:
    ensure_instance_config(instance)
    env = parse_env(instance_paths(instance)["config"])
    env["CTI_DEFAULT_WORKDIR"] = target
    env["CTI_ENABLED_CHANNELS"] = instance
    write_env(instance_paths(instance)["config"], env)


def read_instance_state(instance: str) -> dict:
    ensure_instance_config(instance)
    paths = instance_paths(instance)
    status = read_json(paths["status"], {})
    running = bool(status.get("running")) and pid_alive(status.get("pid"))
    if not running:
        live_pids = daemon_pids(instance=instance)
        if live_pids:
            status["pid"] = live_pids[0]
            status["running"] = True
            running = True
    if status and status.get("running") != running:
        status["running"] = running
        if not running:
            status["lastCheckedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_json(paths["status"], status)
    return {
        "env": parse_env(paths["config"]),
        "status": status,
        "audit": read_json(paths["audit"], []),
        "bindings": read_json(paths["bindings"], {}),
        "paths": paths,
    }


def update_instance_binding_workdir(instance: str, target: str) -> int:
    paths = instance_paths(instance)
    bindings = read_json(paths["bindings"], {})
    count = 0
    for key, value in bindings.items():
        if value.get("channelType") == instance:
            value["workingDirectory"] = target
            value["codepilotSessionId"] = ""
            value["sdkSessionId"] = ""
            bindings[key] = value
            count += 1
    write_json(paths["bindings"], bindings)
    return count


def instance_binding_dir(instance: str, bindings: dict) -> str:
    for value in bindings.values():
        if value.get("channelType") == instance:
            return value.get("workingDirectory", "-")
    return "-"


def start_bridge(instance: str) -> str:
    if not PROJECT_ROOT.exists():
        return f"Bridge 源码目录不存在：{PROJECT_ROOT}"
    daemon_entry = PROJECT_ROOT / "dist" / "daemon.mjs"
    if not daemon_entry.exists():
        return f"未找到 Bridge 入口文件：{daemon_entry}"
    ensure_instance_config(instance)
    legacy_stopped, legacy_message = stop_legacy_bridge_for(instance)
    stopped_pids = stop_instance_processes(instance)
    paths = instance_paths(instance)
    out = open(paths["log"], "a", encoding="utf-8")
    err = open(paths["error_log"], "a", encoding="utf-8")
    env = os.environ.copy()
    env["CTI_HOME"] = str(paths["home"])
    subprocess.Popen(
        ["node", str(daemon_entry), f"--instance={instance}"],
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=err,
        close_fds=True,
        env=env,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | _no_window_flags(),
    )
    details = []
    if legacy_stopped and legacy_message:
        details.append(legacy_message)
    if stopped_pids:
        details.append(f"已先清理旧进程 PID {', '.join(str(pid) for pid in stopped_pids)}")
    details.append(f"已请求启动{INSTANCE_LABELS[instance]}")
    return "；".join(details) + "。"


def stop_bridge(instance: str) -> str:
    legacy_stopped, legacy_message = stop_legacy_bridge_for(instance)
    paths = instance_paths(instance)
    status = read_instance_state(instance)["status"]
    pid = status.get("pid")
    stopped_pids = stop_instance_processes(instance)
    if not pid and not stopped_pids:
        if legacy_stopped and legacy_message:
            return f"{legacy_message} {INSTANCE_LABELS[instance]}当前没有在运行。"
        return f"{INSTANCE_LABELS[instance]}当前没有在运行。"
    status["running"] = False
    status["lastCheckedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_json(paths["status"], status)
    details = []
    if legacy_stopped and legacy_message:
        details.append(legacy_message)
    if stopped_pids:
        details.append(f"已停止{INSTANCE_LABELS[instance]}，PID {', '.join(str(item) for item in stopped_pids)}")
    else:
        details.append(f"已停止{INSTANCE_LABELS[instance]}，PID {pid}")
    return "；".join(details) + "。"


def restart_bridge(instance: str) -> str:
    stop_bridge(instance)
    return start_bridge(instance)


def launch_happy(workdir: str) -> str:
    subprocess.Popen(
        ["powershell.exe", "-NoExit", "-Command", "happy codex"],
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    data = ensure_manager()
    recent = [item for item in data["happyRecent"] if item.get("path") != workdir]
    recent.insert(0, {"path": workdir, "startedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    data["happyRecent"] = recent[:12]
    write_json(MANAGER_SETTINGS, data)
    return f"已在 {workdir} 启动 Happy Codex。"


def save_current_codex_profile(name: str) -> None:
    if not CODEX_AUTH.exists():
        raise FileNotFoundError("没有找到当前 Codex 认证文件。")
    settings = ensure_manager()
    info = codex_account_info()
    profile_id = str(uuid.uuid4())
    profile_path = PROFILE_HOME / f"{profile_id}.json"
    shutil.copy2(CODEX_AUTH, profile_path)
    settings["codexProfiles"].append(
        {
            "id": profile_id,
            "name": name,
            "path": str(profile_path),
            "email": info["email"],
            "plan": info["plan"],
            "provider": info["provider"],
            "savedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    write_json(MANAGER_SETTINGS, settings)


def switch_codex_profile(profile_path: str) -> None:
    source = Path(profile_path)
    if not source.exists():
        raise FileNotFoundError("所选账号存档文件不存在。")
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, CODEX_AUTH)


class MetricCard(QFrame):
    def __init__(self, icon: str, title: str, subtitle: str, bg_color: str):
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        icon_label = QLabel(icon)
        icon_label.setObjectName("metricIcon")
        icon_label.setStyleSheet(f"background:{bg_color};")
        layout.addWidget(icon_label, 0, Qt.AlignLeft)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("metricValue")
        layout.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("metricSub")
        layout.addWidget(self.subtitle_label)

    def set_values(self, title: str, subtitle: str):
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)


class BridgeConsoleWindow(QMainWindow):
    operation_finished = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bridge Console")
        self.resize(1580, 980)
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))
        self.manager = ensure_manager()
        self.current_notice = "已切换到双桥接模式：飞书和 QQ 可以分别单独启动。"
        self.current_page = "dashboard"
        self.operation_finished.connect(self.on_operation_finished)
        self._build_ui()
        self.apply_styles()
        self.refresh_all()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(15000)

    def apply_styles(self):
        QApplication.instance().setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(
            """
            QWidget { color:#15213b; }
            QMainWindow, #appRoot, QScrollArea, QScrollArea > QWidget > QWidget { background:#f5f7fc; }
            QLabel { background:transparent; }
            #pageTitle { font-size: 22px; font-weight: 700; color:#13213d; }
            #pageSub { font-size: 11px; color:#6b7a99; }
            #noticeBar { background:#f7faff; border:1px solid #e9eef8; border-radius:18px; padding:14px 18px; color:#4f7cff; font-weight:600; }
            #panel { background:white; border:1px solid #e6ebf5; border-radius:22px; }
            #subPanel { background:#f8faff; border:1px solid #e6ebf5; border-radius:18px; }
            #panelTitle { font-size:16px; font-weight:700; color:#15213b; }
            #panelSub { font-size:11px; color:#7b88a6; line-height:1.5; }
            #softInfo { color:#6f7d99; font-size:11px; line-height:1.5; }
            #infoBlock { background:#fbfcff; border:1px solid #edf1f7; border-radius:16px; padding:14px 16px; color:#24324d; line-height:1.65; }
            #metricCard { background:white; border:1px solid #e6ebf5; border-radius:22px; }
            #metricIcon { border-radius:12px; min-width:44px; max-width:44px; min-height:44px; max-height:44px; padding:4px; font-size:18px; }
            #metricValue { font-size:18px; font-weight:700; color:#13213d; }
            #metricSub { font-size:11px; color:#6b7a99; }
            QPushButton { border:none; border-radius:16px; padding:11px 16px; font-weight:600; background:#eef3ff; color:#13213d; }
            QPushButton:hover { background:#e5ecff; }
            QPushButton[role="primary"] { background:#4f7cff; color:white; }
            QPushButton[role="primary"]:hover { background:#4472eb; }
            QPushButton[role="danger"] { background:#fff0f4; color:#ef5f7a; }
            QPushButton[role="ghost"] { background:white; border:1px solid #e6ebf5; color:#6b7a99; }
            QLineEdit, QComboBox, QPlainTextEdit { background:white; border:1px solid #e8edf6; border-radius:14px; padding:10px 12px; }
            QPlainTextEdit[readonly="true"] { background:#fbfcff; border:1px solid #edf1f7; }
            QTableWidget { background:white; alternate-background-color:#fbfcff; border:1px solid #e8edf6; border-radius:14px; gridline-color:#f3f6fb; selection-background-color:#edf3ff; selection-color:#15213b; }
            QHeaderView::section { background:#f9fbff; border:none; border-bottom:1px solid #edf1f7; padding:10px; font-weight:600; }
            QTabWidget::pane { border:1px solid #e6ebf5; border-radius:16px; background:white; top:-1px; }
            QTabBar::tab { background:#eef3ff; color:#6b7a99; border:none; padding:10px 16px; margin-right:6px; border-top-left-radius:14px; border-top-right-radius:14px; font-weight:600; }
            QTabBar::tab:selected { background:white; color:#15213b; }
            """
        )

    def make_button(self, text, slot, role=None):
        btn = QPushButton(text)
        if role:
            btn.setProperty("role", role)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        btn.clicked.connect(slot)
        return btn

    def style_nav_button(self, button, active):
        if active:
            button.setStyleSheet(
                "background:#12192b; color:white; border:none; border-radius:18px; padding:11px 18px; font-weight:700;"
            )
        else:
            button.setStyleSheet(
                "background:#eef2f8; color:#15213b; border:none; border-radius:18px; padding:11px 18px; font-weight:600;"
            )

    def show_page(self, page_name):
        self.current_page = page_name
        for name, button in self.nav_buttons.items():
            self.style_nav_button(button, name == page_name)
        self.page_stack.setCurrentWidget(self.pages[page_name])

    def card(self, title, subtitle=""):
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        title_label = QLabel(title)
        title_label.setObjectName("panelTitle")
        layout.addWidget(title_label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setObjectName("panelSub")
            layout.addWidget(sub)
        return panel, layout

    def make_info_label(self):
        label = QLabel()
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        label.setObjectName("infoBlock")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def make_table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        return table

    def wrap_page(self, widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setWidget(widget)
        return area

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = QHBoxLayout()
        left = QVBoxLayout()
        title = QLabel("Bridge Console")
        title.setObjectName("pageTitle")
        sub = QLabel("统一管理飞书桥接、QQ 桥接、Happy Codex 和 Codex 账号切换。")
        sub.setObjectName("pageSub")
        left.addWidget(title)
        left.addWidget(sub)
        header.addLayout(left)
        header.addStretch(1)
        self.nav_buttons = {}
        for key, label in [("dashboard", "仪表盘"), ("workspace", "工作空间"), ("accounts", "账号管理"), ("logs", "日志")]:
            btn = self.make_button(label, lambda _checked=False, name=key: self.show_page(name))
            self.nav_buttons[key] = btn
            header.addWidget(btn)
        root.addLayout(header)

        self.notice_label = QLabel(self.current_notice)
        self.notice_label.setObjectName("noticeBar")
        root.addWidget(self.notice_label)

        self.page_stack = QStackedWidget()
        root.addWidget(self.page_stack, 1)

        self.pages = {
            "dashboard": self.wrap_page(self.build_dashboard_page()),
            "workspace": self.wrap_page(self.build_workspace_page()),
            "accounts": self.wrap_page(self.build_accounts_page()),
            "logs": self.wrap_page(self.build_logs_page()),
        }
        for key in ["dashboard", "workspace", "accounts", "logs"]:
            self.page_stack.addWidget(self.pages[key])
        self.show_page("dashboard")

    def build_dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        metrics_widget = QWidget()
        metrics = QGridLayout(metrics_widget)
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setHorizontalSpacing(14)
        self.metric_feishu = MetricCard("飞", "-", "飞书桥接", "#eafaf2")
        self.metric_qq = MetricCard("Q", "-", "QQ 桥接", "#eef3ff")
        self.metric_happy = MetricCard("⌘", "-", "Happy 端口", "#fff4e8")
        self.metric_account = MetricCard("◎", "-", "当前 Codex 账号", "#fff0f4")
        for idx, widget in enumerate([self.metric_feishu, self.metric_qq, self.metric_happy, self.metric_account]):
            metrics.addWidget(widget, 0, idx)
        layout.addWidget(metrics_widget)

        self.overview_panel, overview_layout = self.card("今日总览", "现在飞书和 QQ 是两套独立桥接实例，可以分别启停和分别设置默认工作空间。")
        self.dashboard_text = self.make_info_label()
        overview_layout.addWidget(self.dashboard_text)
        dash_actions = QHBoxLayout()
        dash_actions.addWidget(self.make_button("前往工作空间", lambda: self.show_page("workspace"), "primary"))
        dash_actions.addWidget(self.make_button("前往账号管理", lambda: self.show_page("accounts")))
        dash_actions.addWidget(self.make_button("前往日志", lambda: self.show_page("logs"), "ghost"))
        dash_actions.addStretch(1)
        overview_layout.addLayout(dash_actions)
        layout.addWidget(self.overview_panel)

        bridge_row = QHBoxLayout()
        self.feishu_service_panel, feishu_layout = self.card("飞书桥接服务", "独立实例：只负责飞书消息，不再和 QQ 共用进程。")
        self.feishu_service_text = self.make_info_label()
        feishu_layout.addWidget(self.feishu_service_text)
        feishu_actions = QHBoxLayout()
        feishu_actions.addWidget(self.make_button("启动飞书桥接", lambda: self.instance_start_action("feishu"), "primary"))
        feishu_actions.addWidget(self.make_button("停止飞书桥接", lambda: self.instance_stop_action("feishu"), "danger"))
        feishu_actions.addWidget(self.make_button("重启飞书桥接", lambda: self.instance_restart_action("feishu")))
        feishu_actions.addStretch(1)
        feishu_layout.addLayout(feishu_actions)
        bridge_row.addWidget(self.feishu_service_panel, 1)

        self.qq_service_panel, qq_layout = self.card("QQ 桥接服务", "独立实例：只负责 QQ 消息，可以单独停掉，不影响飞书。")
        self.qq_service_text = self.make_info_label()
        qq_layout.addWidget(self.qq_service_text)
        qq_actions = QHBoxLayout()
        qq_actions.addWidget(self.make_button("启动 QQ 桥接", lambda: self.instance_start_action("qq"), "primary"))
        qq_actions.addWidget(self.make_button("停止 QQ 桥接", lambda: self.instance_stop_action("qq"), "danger"))
        qq_actions.addWidget(self.make_button("重启 QQ 桥接", lambda: self.instance_restart_action("qq")))
        qq_actions.addStretch(1)
        qq_layout.addLayout(qq_actions)
        bridge_row.addWidget(self.qq_service_panel, 1)
        layout.addLayout(bridge_row)

        self.happy_panel, happy_layout = self.card("Happy Codex", "Happy 可以同时开启多个对话，所以这里直接做多工作空间入口。")
        happy_actions = QHBoxLayout()
        self.dashboard_happy_combo = QComboBox()
        self.dashboard_happy_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        happy_actions.addWidget(self.dashboard_happy_combo, 1)
        happy_actions.addWidget(self.make_button("从选中工作空间启动 Happy", self.launch_happy_from_dashboard, "primary"))
        happy_actions.addWidget(self.make_button("刷新工作空间列表", self.refresh_all, "ghost"))
        happy_layout.addLayout(happy_actions)
        self.happy_dashboard_text = self.make_info_label()
        happy_layout.addWidget(self.happy_dashboard_text)
        layout.addWidget(self.happy_panel)
        layout.addStretch(1)
        return page

    def build_workspace_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.workspace_panel, ws_layout = self.card("工作空间选择器", "从预设里选一个目录，直接设置成飞书、QQ 或 Happy 使用的工作目录。")
        self.feishu_dir_label = QLabel()
        self.qq_dir_label = QLabel()
        for text in [self.feishu_dir_label, self.qq_dir_label]:
            text.setObjectName("softInfo")
            ws_layout.addWidget(text)

        form = QGridLayout()
        form.addWidget(QLabel("预设名称"), 0, 0)
        form.addWidget(QLabel("工作空间路径"), 0, 1)
        self.name_input = QLineEdit()
        self.path_input = QLineEdit()
        form.addWidget(self.name_input, 1, 0)
        form.addWidget(self.path_input, 1, 1)
        ws_layout.addLayout(form)

        actions = QHBoxLayout()
        actions.addWidget(self.make_button("选择文件夹", self.pick_folder))
        actions.addWidget(self.make_button("新增 / 保存预设", self.add_preset, "primary"))
        actions.addWidget(self.make_button("删除预设", self.delete_preset, "danger"))
        actions.addStretch(1)
        actions.addWidget(self.make_button("刷新", self.refresh_all, "ghost"))
        ws_layout.addLayout(actions)

        split = QHBoxLayout()
        left_box = QFrame()
        left_box.setObjectName("subPanel")
        left_layout = QVBoxLayout(left_box)
        left_layout.addWidget(QLabel("项目预设"))
        self.preset_table = self.make_table(["名称", "路径"])
        self.preset_table.itemSelectionChanged.connect(self.on_preset_select)
        left_layout.addWidget(self.preset_table)
        split.addWidget(left_box, 3)

        right_box = QFrame()
        right_box.setObjectName("subPanel")
        right_layout = QVBoxLayout(right_box)
        right_layout.addWidget(QLabel("一键分发"))
        right_layout.addWidget(self.make_button("设为飞书工作目录", self.set_feishu_workspace))
        right_layout.addWidget(self.make_button("设为 QQ 工作目录", self.set_qq_workspace))
        right_layout.addWidget(self.make_button("同时设为飞书和 QQ 工作目录", self.set_both_workspace, "primary"))
        hint = QLabel("这里会同时更新该通道的新会话目录和当前聊天使用目录，你不用区分底层状态。")
        hint.setWordWrap(True)
        hint.setObjectName("softInfo")
        right_layout.addWidget(hint)
        right_layout.addStretch(1)
        split.addWidget(right_box, 2)
        ws_layout.addLayout(split)
        layout.addWidget(self.workspace_panel)

        self.binding_panel, bind_layout = self.card("通道工作空间", "下面分别展示飞书实例和 QQ 实例各自的当前绑定情况。")
        self.binding_tabs = QTabWidget()
        self.feishu_binding_table = self.make_table(["聊天 ID", "工作空间", "模式"])
        feishu_tab = QWidget()
        feishu_tab_layout = QVBoxLayout(feishu_tab)
        feishu_tab_layout.addWidget(self.feishu_binding_table)
        self.qq_binding_table = self.make_table(["聊天 ID", "工作空间", "模式"])
        qq_tab = QWidget()
        qq_tab_layout = QVBoxLayout(qq_tab)
        qq_tab_layout.addWidget(self.qq_binding_table)
        self.binding_tabs.addTab(feishu_tab, "飞书")
        self.binding_tabs.addTab(qq_tab, "QQ")
        bind_layout.addWidget(self.binding_tabs)
        layout.addWidget(self.binding_panel, 1)

        self.recent_panel, recent_layout = self.card("Happy 多工作空间", "Happy 可以多开对话，这里保留最近目录和从预设快速启动。")
        top_actions = QHBoxLayout()
        top_actions.addWidget(self.make_button("从选中预设启动 Happy", self.launch_happy_selected, "primary"))
        top_actions.addWidget(self.make_button("启动最近目录", self.launch_happy_recent))
        top_actions.addWidget(self.make_button("把最近目录加入预设", self.add_recent_to_presets, "ghost"))
        top_actions.addStretch(1)
        recent_layout.addLayout(top_actions)
        split_recent = QHBoxLayout()
        self.recent_table = self.make_table(["最近启动目录", "时间"])
        split_recent.addWidget(self.recent_table, 2)
        self.happy_hint = self.make_info_label()
        split_recent.addWidget(self.happy_hint, 1)
        recent_layout.addLayout(split_recent)
        layout.addWidget(self.recent_panel, 1)
        layout.addStretch(1)
        return page

    def build_accounts_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.account_panel, account_layout = self.card("Codex 账号管理", "这里的账号存档，本质上是当前 auth.json 的一份可切换存档。新增账号时才需要终端登录。")
        split = QHBoxLayout()
        current_box = QFrame()
        current_box.setObjectName("subPanel")
        current_layout = QVBoxLayout(current_box)
        current_layout.addWidget(QLabel("当前账号"))
        self.account_text = self.make_info_label()
        current_layout.addWidget(self.account_text)
        split.addWidget(current_box, 1)

        quick_box = QFrame()
        quick_box.setObjectName("subPanel")
        quick_layout = QVBoxLayout(quick_box)
        quick_layout.addWidget(QLabel("快捷操作"))
        self.profile_name_input = QLineEdit()
        self.profile_name_input.setPlaceholderText("账号存档名称")
        quick_layout.addWidget(self.profile_name_input)
        quick_layout.addWidget(self.make_button("保存当前账号存档", self.save_codex_archive, "primary"))
        quick_layout.addWidget(self.make_button("登录新账号", self.codex_login))
        quick_layout.addWidget(self.make_button("打开认证目录", self.open_codex_folder, "ghost"))
        quick_layout.addStretch(1)
        split.addWidget(quick_box, 1)
        account_layout.addLayout(split)

        self.profile_table = self.make_table(["存档名称", "账号", "方案", "登录源", "保存时间"])
        account_layout.addWidget(self.profile_table)
        actions = QHBoxLayout()
        actions.addWidget(self.make_button("切换到选中存档", self.switch_codex_archive))
        actions.addWidget(self.make_button("删除存档", self.delete_codex_archive, "danger"))
        actions.addStretch(1)
        actions.addWidget(self.make_button("刷新账号信息", self.refresh_all, "ghost"))
        account_layout.addLayout(actions)
        layout.addWidget(self.account_panel)
        layout.addStretch(1)
        return page

    def build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.log_panel, log_layout = self.card("日志与运行状态", "这里同时看飞书桥接日志、QQ 桥接日志、Happy 日志和审计记录。")
        ctrl = QHBoxLayout()
        ctrl.addStretch(1)
        ctrl.addWidget(QLabel("自动刷新"))
        self.refresh_combo = QComboBox()
        self.refresh_combo.addItems(["5", "10", "15", "30", "60"])
        self.refresh_combo.setCurrentText("15")
        self.refresh_combo.currentTextChanged.connect(self.change_refresh)
        ctrl.addWidget(self.refresh_combo)
        log_layout.addLayout(ctrl)

        self.log_tabs = QTabWidget()
        self.audit_table = self.make_table(["实例", "通道", "方向", "摘要", "时间"])
        audit_tab = QWidget()
        audit_layout = QVBoxLayout(audit_tab)
        audit_layout.addWidget(self.audit_table)

        self.feishu_log = QPlainTextEdit()
        self.feishu_log.setReadOnly(True)
        feishu_tab = QWidget()
        feishu_layout = QVBoxLayout(feishu_tab)
        feishu_layout.addWidget(self.feishu_log)

        self.qq_log = QPlainTextEdit()
        self.qq_log.setReadOnly(True)
        qq_tab = QWidget()
        qq_layout = QVBoxLayout(qq_tab)
        qq_layout.addWidget(self.qq_log)

        self.happy_log = QPlainTextEdit()
        self.happy_log.setReadOnly(True)
        happy_tab = QWidget()
        happy_layout = QVBoxLayout(happy_tab)
        happy_layout.addWidget(self.happy_log)

        self.log_tabs.addTab(audit_tab, "审计记录")
        self.log_tabs.addTab(feishu_tab, "飞书 Bridge 日志")
        self.log_tabs.addTab(qq_tab, "QQ Bridge 日志")
        self.log_tabs.addTab(happy_tab, "Happy 日志")
        log_layout.addWidget(self.log_tabs)
        layout.addWidget(self.log_panel, 1)
        return page

    def set_notice(self, text: str):
        self.current_notice = text
        self.notice_label.setText(text)

    def run_async(self, worker, pending_text: str):
        self.set_notice(pending_text)

        def target():
            try:
                result = worker()
            except Exception as exc:
                result = f"操作失败：{exc}"
            self.operation_finished.emit(result)

        threading.Thread(target=target, daemon=True).start()

    def on_operation_finished(self, message: str):
        self.set_notice(message)
        self.refresh_all()

    def selected_preset(self):
        row = self.preset_table.currentRow()
        if row < 0 or row >= len(self.manager["projectPresets"]):
            return None
        return self.manager["projectPresets"][row]

    def selected_profile(self):
        row = self.profile_table.currentRow()
        if row < 0 or row >= len(self.manager["codexProfiles"]):
            return None
        return self.manager["codexProfiles"][row]

    def fill_table(self, table, rows):
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(value)))

    def prune_invalid_presets(self) -> list[str]:
        removed = []
        kept = []
        for item in self.manager["projectPresets"]:
            target = item.get("path", "")
            if path_ok(target):
                kept.append(item)
            else:
                removed.append(target or item.get("name", ""))
        if removed:
            self.manager["projectPresets"] = kept
            write_json(MANAGER_SETTINGS, self.manager)
        return removed

    def remove_invalid_target_from_presets(self, target: str) -> bool:
        before = len(self.manager["projectPresets"])
        self.manager["projectPresets"] = [
            item for item in self.manager["projectPresets"] if item.get("path") != target
        ]
        changed = len(self.manager["projectPresets"]) != before
        if changed:
            write_json(MANAGER_SETTINGS, self.manager)
        return changed

    def validate_target_or_cleanup(self, target: str, context: str) -> bool:
        if path_ok(target):
            return True
        removed = self.remove_invalid_target_from_presets(target)
        message = f"{context}失败：路径无效或已不存在。\n\n{target}"
        if removed:
            message += "\n\n该路径已从项目预设中自动删除。"
        QMessageBox.warning(self, "路径无效", message)
        self.refresh_all()
        return False

    def validate_instance_workspace_before_start(self, instance: str) -> bool:
        state = read_instance_state(instance)
        bindings = state["bindings"]
        target = instance_binding_dir(instance, bindings)
        if target == "-":
            target = state["env"].get("CTI_DEFAULT_WORKDIR", "")
        if path_ok(target):
            return True
        removed = self.remove_invalid_target_from_presets(target)
        label = INSTANCE_LABELS[instance]
        message = f"{label}未启动：当前工作路径无效或已不存在。\n\n{target}"
        if removed:
            message += "\n\n该路径已从项目预设中自动删除。"
        QMessageBox.warning(self, "无法启动桥接", message)
        self.refresh_all()
        return False

    def state(self):
        self.manager = ensure_manager()
        return {
            "feishu": read_instance_state("feishu"),
            "qq": read_instance_state("qq"),
            "happy": read_json(HAPPY_DAEMON, {}),
            "account": codex_account_info(),
        }

    def refresh_all(self):
        removed_presets = self.prune_invalid_presets()
        st = self.state()
        feishu = st["feishu"]
        qq = st["qq"]
        happy = st["happy"]
        account = st["account"]
        if removed_presets:
            joined = "，".join(removed_presets[:3])
            suffix = " 等路径" if len(removed_presets) > 3 else ""
            self.set_notice(f"已自动清理无效工作路径：{joined}{suffix}")

        self.metric_feishu.set_values("运行中" if feishu["status"].get("running") else "未运行", "飞书桥接")
        self.metric_qq.set_values("运行中" if qq["status"].get("running") else "未运行", "QQ 桥接")
        self.metric_happy.set_values(str(happy.get("httpPort", "-")), "Happy 端口")
        self.metric_account.set_values(account.get("email", "-"), f"当前 Codex · {account.get('plan', '-')}")

        self.dashboard_text.setText(
            "<br>".join(
                [
                    f"飞书工作目录：{instance_binding_dir('feishu', feishu['bindings'])}",
                    f"QQ 工作目录：{instance_binding_dir('qq', qq['bindings'])}",
                    f"当前 Codex：{account.get('email', '-')}",
                    f"Happy 端口：{happy.get('httpPort', '-')}",
                ]
            )
        )
        self.feishu_service_text.setText(
            "<br>".join(
                [
                    f"状态：{'运行中' if feishu['status'].get('running') else '未运行'}",
                    f"PID：{feishu['status'].get('pid', '-')}",
                    f"工作目录：{instance_binding_dir('feishu', feishu['bindings'])}",
                    f"实例目录：{feishu['paths']['home']}",
                ]
            )
        )
        self.qq_service_text.setText(
            "<br>".join(
                [
                    f"状态：{'运行中' if qq['status'].get('running') else '未运行'}",
                    f"PID：{qq['status'].get('pid', '-')}",
                    f"工作目录：{instance_binding_dir('qq', qq['bindings'])}",
                    f"实例目录：{qq['paths']['home']}",
                ]
            )
        )

        self.dashboard_happy_combo.clear()
        seen = set()
        for item in self.manager["projectPresets"]:
            target = item.get("path", "")
            if target and target not in seen:
                self.dashboard_happy_combo.addItem(f"预设 · {item.get('name', '')}", target)
                seen.add(target)
        for item in self.manager["happyRecent"]:
            target = item.get("path", "")
            if target and target not in seen:
                self.dashboard_happy_combo.addItem(f"最近 · {target}", target)
                seen.add(target)
        self.happy_dashboard_text.setText(
            "Happy 多开说明：<br>"
            "- Happy Codex 不是单实例，可以从多个工作空间分别启动。<br>"
            "- 这里不会替你关闭已有对话，只负责再开新的终端会话。<br>"
            f"- 最近记录数量：{len(self.manager['happyRecent'])}"
        )

        self.feishu_dir_label.setText(f"飞书当前工作目录：{instance_binding_dir('feishu', feishu['bindings'])}")
        self.qq_dir_label.setText(f"QQ 当前工作目录：{instance_binding_dir('qq', qq['bindings'])}")

        self.fill_table(self.preset_table, [(x.get("name", ""), x.get("path", "")) for x in self.manager["projectPresets"]])
        self.fill_table(
            self.feishu_binding_table,
            [(v.get("chatId", ""), v.get("workingDirectory", ""), v.get("mode", "")) for v in feishu["bindings"].values() if v.get("channelType") == "feishu"],
        )
        self.fill_table(
            self.qq_binding_table,
            [(v.get("chatId", ""), v.get("workingDirectory", ""), v.get("mode", "")) for v in qq["bindings"].values() if v.get("channelType") == "qq"],
        )
        self.fill_table(self.recent_table, [(x.get("path", ""), x.get("startedAt", "")) for x in self.manager["happyRecent"]])
        self.happy_hint.setText(
            "建议用法：<br>"
            "- 飞书桥接绑定长期项目 A<br>"
            "- QQ 桥接绑定项目 B<br>"
            "- Happy 用来临时多开更多工作空间"
        )

        self.account_text.setText(
            "<br>".join(
                [
                    f"邮箱：{account.get('email', '-')}",
                    f"登录方式：{account.get('mode', '-')}",
                    f"认证来源：{account.get('provider', '-')}",
                    f"订阅方案：{account.get('plan', '-')}",
                    f"组织：{account.get('org', '-')}",
                    f"最近刷新：{account.get('last_refresh', '-')}",
                ]
            )
        )
        self.fill_table(
            self.profile_table,
            [(x.get("name", ""), x.get("email", ""), x.get("plan", ""), x.get("provider", ""), x.get("savedAt", "")) for x in self.manager["codexProfiles"]],
        )

        audits = []
        for instance, payload in [("feishu", feishu), ("qq", qq)]:
            for item in payload["audit"][-30:][::-1]:
                audits.append((INSTANCE_LABELS[instance], item.get("channelType", ""), item.get("direction", ""), item.get("summary", ""), item.get("createdAt", "")))
        self.fill_table(self.audit_table, audits)
        self.feishu_log.setPlainText(tail_lines(feishu["paths"]["log"], 120))
        self.qq_log.setPlainText(tail_lines(qq["paths"]["log"], 120))
        daemon_log = Path(happy.get("daemonLogPath", "")) if happy.get("daemonLogPath") else None
        self.happy_log.setPlainText(tail_lines(daemon_log, 120) if daemon_log else "")

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择工作空间文件夹")
        if folder:
            self.path_input.setText(folder)
            if not self.name_input.text().strip():
                self.name_input.setText(Path(folder).name or folder)

    def on_preset_select(self):
        item = self.selected_preset()
        if item:
            self.name_input.setText(item.get("name", ""))
            self.path_input.setText(item.get("path", ""))

    def add_preset(self):
        name = self.name_input.text().strip()
        target = self.path_input.text().strip()
        if not name or not target:
            return QMessageBox.warning(self, "提示", "预设名称和工作空间路径不能为空。")
        if not path_ok(target):
            return QMessageBox.warning(self, "提示", "所选工作空间不存在或不是文件夹。")
        item = self.selected_preset()
        if item:
            item["name"] = name
            item["path"] = target
            text = f"已更新项目预设：{name}"
        else:
            self.manager["projectPresets"].append({"id": str(uuid.uuid4()), "name": name, "path": target})
            text = f"已新增项目预设：{name}"
        write_json(MANAGER_SETTINGS, self.manager)
        self.refresh_all()
        self.set_notice(text)

    def delete_preset(self):
        item = self.selected_preset()
        if not item:
            return
        self.manager["projectPresets"] = [x for x in self.manager["projectPresets"] if x.get("id") != item.get("id")]
        write_json(MANAGER_SETTINGS, self.manager)
        self.refresh_all()
        self.set_notice("已删除项目预设。")

    def apply_target_to_instance(self, instance: str):
        item = self.selected_preset()
        if not item:
            return QMessageBox.warning(self, "提示", "请先选中一个项目预设。")
        target = item.get("path", "")
        if not self.validate_target_or_cleanup(target, f"设置{INSTANCE_LABELS[instance]}工作目录"):
            return
        was_running = bool(read_instance_state(instance)["status"].get("running"))
        updated = update_instance_binding_workdir(instance, target)
        update_instance_default_workdir(instance, target)
        restart_message = ""
        if was_running:
            restart_message = restart_bridge(instance)
        self.refresh_all()
        message = f"{INSTANCE_LABELS[instance]}工作目录已设为 {target}。已重置当前聊天会话并更新 {updated} 个聊天绑定。"
        if restart_message:
            message = f"{message} {restart_message}"
        self.set_notice(message)

    def set_feishu_workspace(self):
        self.apply_target_to_instance("feishu")

    def set_qq_workspace(self):
        self.apply_target_to_instance("qq")

    def set_both_workspace(self):
        item = self.selected_preset()
        if not item:
            return QMessageBox.warning(self, "提示", "请先选中一个项目预设。")
        target = item.get("path", "")
        if not self.validate_target_or_cleanup(target, "设置飞书和 QQ 工作目录"):
            return
        feishu_running = bool(read_instance_state("feishu")["status"].get("running"))
        qq_running = bool(read_instance_state("qq")["status"].get("running"))
        update_instance_default_workdir("feishu", target)
        update_instance_default_workdir("qq", target)
        feishu_updated = update_instance_binding_workdir("feishu", target)
        qq_updated = update_instance_binding_workdir("qq", target)
        restart_notes = []
        if feishu_running:
            restart_notes.append(restart_bridge("feishu"))
        if qq_running:
            restart_notes.append(restart_bridge("qq"))
        self.refresh_all()
        message = f"已把飞书和 QQ 的工作目录都设为 {target}。飞书更新 {feishu_updated} 个聊天，QQ 更新 {qq_updated} 个聊天，并已重置对应会话。"
        if restart_notes:
            message = f"{message} {' '.join(restart_notes)}"
        self.set_notice(message)

    def launch_happy_selected(self):
        item = self.selected_preset()
        if not item:
            return QMessageBox.warning(self, "提示", "请先选中一个项目预设。")
        target = item.get("path", "")
        if not path_ok(target):
            return QMessageBox.warning(self, "提示", "目标工作空间无效。")
        self.set_notice(launch_happy(target))
        self.refresh_all()

    def launch_happy_recent(self):
        row = self.recent_table.currentRow()
        if row < 0 or row >= len(self.manager["happyRecent"]):
            return QMessageBox.warning(self, "提示", "请先选中一个最近目录。")
        target = self.manager["happyRecent"][row].get("path", "")
        if not path_ok(target):
            return QMessageBox.warning(self, "提示", "目标工作空间无效。")
        self.set_notice(launch_happy(target))
        self.refresh_all()

    def launch_happy_from_dashboard(self):
        target = self.dashboard_happy_combo.currentData()
        if not target or not path_ok(target):
            return QMessageBox.warning(self, "提示", "请先选一个有效工作空间。")
        self.set_notice(launch_happy(target))
        self.refresh_all()

    def add_recent_to_presets(self):
        row = self.recent_table.currentRow()
        if row < 0 or row >= len(self.manager["happyRecent"]):
            return QMessageBox.warning(self, "提示", "请先选中一个最近目录。")
        target = self.manager["happyRecent"][row].get("path", "")
        if not path_ok(target):
            return QMessageBox.warning(self, "提示", "目标工作空间无效。")
        name = Path(target).name or target
        if not any(x.get("path") == target for x in self.manager["projectPresets"]):
            self.manager["projectPresets"].append({"id": str(uuid.uuid4()), "name": name, "path": target})
            write_json(MANAGER_SETTINGS, self.manager)
        self.refresh_all()
        self.set_notice(f"已把 {target} 加入项目预设。")

    def save_codex_archive(self):
        name = self.profile_name_input.text().strip()
        if not name:
            return QMessageBox.warning(self, "提示", "请先填写账号存档名称。")
        try:
            save_current_codex_profile(name)
        except FileNotFoundError as exc:
            return QMessageBox.warning(self, "提示", str(exc))
        self.profile_name_input.clear()
        self.refresh_all()
        self.set_notice(f"已保存账号存档：{name}")

    def switch_codex_archive(self):
        item = self.selected_profile()
        if not item:
            return QMessageBox.warning(self, "提示", "请先选中一个账号存档。")
        try:
            switch_codex_profile(item.get("path", ""))
        except FileNotFoundError as exc:
            return QMessageBox.warning(self, "提示", str(exc))
        self.refresh_all()
        self.set_notice(f"已切换到账号存档：{item.get('name', '-')}")

    def delete_codex_archive(self):
        item = self.selected_profile()
        if not item:
            return QMessageBox.warning(self, "提示", "请先选中一个账号存档。")
        path = Path(item.get("path", ""))
        if path.exists():
            path.unlink()
        self.manager["codexProfiles"] = [x for x in self.manager["codexProfiles"] if x.get("id") != item.get("id")]
        write_json(MANAGER_SETTINGS, self.manager)
        self.refresh_all()
        self.set_notice("已删除账号存档。")

    def codex_login(self):
        subprocess.Popen(
            ["powershell.exe", "-NoExit", "-Command", "codex login"],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        self.set_notice("已打开新的终端窗口，你可以在那里登录一个 Codex 账号。")

    def open_codex_folder(self):
        CODEX_HOME.mkdir(parents=True, exist_ok=True)
        os.startfile(str(CODEX_HOME))

    def instance_start_action(self, instance: str):
        if not self.validate_instance_workspace_before_start(instance):
            return
        self.run_async(lambda: start_bridge(instance), f"正在启动{INSTANCE_LABELS[instance]}...")

    def instance_stop_action(self, instance: str):
        self.run_async(lambda: stop_bridge(instance), f"正在停止{INSTANCE_LABELS[instance]}...")

    def instance_restart_action(self, instance: str):
        self.run_async(lambda: restart_bridge(instance), f"正在重启{INSTANCE_LABELS[instance]}...")

    def change_refresh(self, value: str):
        try:
            self.timer.start(int(value) * 1000)
            self.set_notice(f"已把自动刷新改成每 {value} 秒一次。")
        except ValueError:
            pass


def main():
    ensure_manager()
    ensure_instance_config("feishu")
    ensure_instance_config("qq")
    apply_windows_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("Bridge Console")
    app.setApplicationDisplayName("Bridge Console")
    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))
    win = BridgeConsoleWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
