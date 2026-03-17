import base64
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk


HOME = Path.home()
SOURCE_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = SOURCE_ROOT.parent.parent
LOCAL_CONFIG_PATH = SOURCE_ROOT / "bridge-console.local.json"
CTI_HOME = HOME / ".claude-to-im"
CTI_CONFIG = CTI_HOME / "config.env"
CTI_STATUS = CTI_HOME / "runtime" / "status.json"
CTI_LOG = CTI_HOME / "logs" / "bridge.log"
CTI_AUDIT = CTI_HOME / "data" / "audit.json"
CTI_BINDINGS = CTI_HOME / "data" / "bindings.json"
HAPPY_HOME = HOME / ".happy"
HAPPY_DAEMON = HAPPY_HOME / "daemon.state.json"
CODEX_HOME = HOME / ".codex"
CODEX_AUTH = CODEX_HOME / "auth.json"
MANAGER_HOME = HOME / ".bridge-console"
MANAGER_SETTINGS = MANAGER_HOME / "settings.json"
PROFILE_HOME = MANAGER_HOME / "codex-profiles"
DEFAULT_CTI_SOURCE = WORKSPACE_ROOT / "Claude-to-IM-skill" / "Claude-to-IM-skill-source"


def load_local_config():
    try:
        return json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_config_path(value, fallback: Path):
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


BG = "#f4f7fc"
SURFACE = "#ffffff"
SURFACE_ALT = "#f8faff"
BORDER = "#e6ebf5"
TEXT = "#14213d"
TEXT_SOFT = "#6b7a99"
ACCENT = "#4f7cff"
ACCENT_SOFT = "#eef3ff"
SUCCESS = "#18b368"
SUCCESS_BG = "#eafaf2"
WARNING = "#ff9f43"
WARNING_BG = "#fff4e8"
DANGER = "#ef5f7a"
DANGER_BG = "#fff0f4"
INFO_BG = "#f3f7ff"


def first_available_font(*names):
    families = set(tkfont.families())
    for name in names:
        if name in families:
            return name
    return "TkDefaultFont"


def read_text(path: Path, fallback=""):
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return fallback


def read_json(path: Path, fallback):
    try:
        return json.loads(read_text(path))
    except Exception:
        return fallback


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_env(path: Path):
    data = {}
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def write_env_key(path: Path, key: str, value: str):
    lines = read_text(path).splitlines()
    output = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{key}={value}")
    path.write_text("\r\n".join([line for line in output if line]) + "\r\n", encoding="utf-8")


def update_channel_binding_workdir(channel_type: str, target: str):
    bindings = read_json(CTI_BINDINGS, {})
    changed = 0
    for key, value in bindings.items():
        if value.get("channelType") == channel_type:
            value["workingDirectory"] = target
            bindings[key] = value
            changed += 1
    write_json(CTI_BINDINGS, bindings)
    return changed


def tail_lines(path: Path | None, count=120):
    if not path:
        return ""
    return "\n".join(read_text(path).splitlines()[-count:])


def path_ok(target: str):
    if not target:
        return False
    try:
        return Path(target).exists() and Path(target).is_dir()
    except Exception:
        return False


def decode_jwt_payload(token: str):
    if not token or "." not in token:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


def codex_account_info():
    auth = read_json(CODEX_AUTH, {})
    tokens = auth.get("tokens", {})
    id_payload = decode_jwt_payload(tokens.get("id_token", ""))
    access_payload = decode_jwt_payload(tokens.get("access_token", ""))
    auth_payload = id_payload.get("https://api.openai.com/auth", {}) or access_payload.get("https://api.openai.com/auth", {})
    profile_payload = access_payload.get("https://api.openai.com/profile", {})
    orgs = auth_payload.get("organizations", [])
    default_org = next((org for org in orgs if org.get("is_default")), orgs[0] if orgs else {})
    email = profile_payload.get("email") or id_payload.get("email") or "-"
    provider = id_payload.get("auth_provider", "-")
    plan = auth_payload.get("chatgpt_plan_type", "-")
    mode = auth.get("auth_mode", "-")
    return {
        "email": email,
        "provider": provider,
        "plan": plan,
        "mode": mode,
        "account_id": tokens.get("account_id", "-"),
        "org": default_org.get("title", "-"),
        "last_refresh": auth.get("last_refresh", "-"),
    }


def ensure_manager():
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


def stop_bridge():
    status = read_json(CTI_STATUS, {})
    pid = status.get("pid")
    if not pid:
        return "Bridge 当前没有在运行。"
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return f"已停止 Bridge，PID {pid}。"


def start_bridge():
    if not PROJECT_ROOT.exists():
        return f"Bridge 源码目录不存在：{PROJECT_ROOT}"
    daemon_entry = PROJECT_ROOT / "dist" / "daemon.mjs"
    if not daemon_entry.exists():
        return f"未找到 Bridge 入口文件：{daemon_entry}"
    (CTI_HOME / "logs").mkdir(parents=True, exist_ok=True)
    (CTI_HOME / "runtime").mkdir(parents=True, exist_ok=True)
    out = open(CTI_HOME / "logs" / "bridge.log", "a", encoding="utf-8")
    err = open(CTI_HOME / "logs" / "bridge.err.log", "a", encoding="utf-8")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen(["node", str(daemon_entry)], cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True, creationflags=flags)
    return "已请求启动 Bridge。"


def restart_bridge():
    stop_bridge()
    return start_bridge()


def launch_happy(workdir: str):
    subprocess.Popen(["powershell.exe", "-NoExit", "-Command", "happy codex"], cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    data = ensure_manager()
    recent = [item for item in data["happyRecent"] if item.get("path") != workdir]
    recent.insert(0, {"path": workdir})
    data["happyRecent"] = recent[:10]
    write_json(MANAGER_SETTINGS, data)
    return f"已在 {workdir} 启动 Happy Codex。"


def save_current_codex_profile(name: str):
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


def switch_codex_profile(profile_path: str):
    source = Path(profile_path)
    if not source.exists():
        raise FileNotFoundError("所选账号快照文件不存在。")
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, CODEX_AUTH)


class SoftButton(tk.Button):
    def __init__(self, parent, text, command, kind="soft", width=0):
        palette = {
            "primary": (ACCENT, "#ffffff", "#3f69e5"),
            "soft": ("#eef3ff", TEXT, "#e4ebfb"),
            "danger": ("#fff0f4", DANGER, "#ffe3ea"),
            "ghost": (SURFACE, TEXT_SOFT, "#f5f7fb"),
        }
        bg, fg, active = palette[kind]
        font_family = first_available_font("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Variable", "Segoe UI")
        super().__init__(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active, activeforeground=fg, relief="flat", bd=0, font=(font_family, 10, "bold"), padx=16, pady=10, cursor="hand2", width=width)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Bridge Console")
        self.geometry("1560x980")
        self.minsize(1360, 860)
        self.configure(bg=BG)
        self.tk.call("tk", "scaling", 1.25)

        self.font_ui = first_available_font("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Variable", "Segoe UI")
        self.font_title = self.font_ui
        self.font_code = first_available_font("Cascadia Mono", "Consolas")

        self.f_title = (self.font_title, 17, "bold")
        self.f_h1 = (self.font_title, 15, "bold")
        self.f_h2 = (self.font_ui, 13, "bold")
        self.f_body = (self.font_ui, 10)
        self.f_body_soft = (self.font_ui, 10)
        self.f_small = (self.font_ui, 9)
        self.f_button = (self.font_ui, 10, "bold")
        self.f_chip = (self.font_ui, 9, "bold")
        self.f_metric = (self.font_title, 17, "bold")
        self.f_code = (self.font_code, 10)
        self.f_code_small = (self.font_code, 9)

        self.manager = ensure_manager()
        self.notice = tk.StringVar(value="准备就绪。")
        self.selected_path = tk.StringVar()
        self.selected_name = tk.StringVar()
        self.default_dir = tk.StringVar()
        self.feishu_dir = tk.StringVar()
        self.qq_dir = tk.StringVar()
        self.refresh_seconds = tk.IntVar(value=15)
        self.card_bridge_title = tk.StringVar(value="运行中")
        self.card_bridge_sub = tk.StringVar(value="Bridge 状态")
        self.card_channel_title = tk.StringVar(value="feishu,qq")
        self.card_channel_sub = tk.StringVar(value="已启用通道")
        self.card_happy_title = tk.StringVar(value="-")
        self.card_happy_sub = tk.StringVar(value="Happy 端口")
        self.card_account_title = tk.StringVar(value="-")
        self.card_account_sub = tk.StringVar(value="当前 Codex 账号")
        self.profile_name_var = tk.StringVar()
        self.auto_job = None

        self._style()
        self._build_ui()
        self.refresh_all()
        self.schedule_refresh()

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=36, bordercolor=BORDER, relief="flat", font=self.f_body)
        style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=TEXT, bordercolor=BORDER, font=self.f_button, padding=(8, 10))
        style.map("Treeview", background=[("selected", "#ecf2ff")], foreground=[("selected", TEXT)])
        style.configure("TNotebook", background=SURFACE, borderwidth=0)
        style.configure("TNotebook.Tab", background=ACCENT_SOFT, foreground=TEXT_SOFT, padding=(16, 11), font=self.f_button)
        style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", TEXT)])
        style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=7, font=self.f_body)

    def _build_ui(self):
        wrapper = tk.Frame(self, bg=BG)
        wrapper.pack(fill="both", expand=True, padx=24, pady=20)
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_rowconfigure(3, weight=1)

        self._build_header(wrapper)
        self._build_summary_cards(wrapper)
        self._build_content(wrapper)

    def _build_header(self, parent):
        header = tk.Frame(parent, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.grid_columnconfigure(0, weight=1)

        left = tk.Frame(header, bg=BG)
        left.grid(row=0, column=0, sticky="w")
        tk.Label(left, text="Bridge Console", bg=BG, fg=TEXT, font=self.f_title).pack(anchor="w")
        tk.Label(left, text="统一管理飞书、QQ、Happy Codex 与 Codex 账号切换。", bg=BG, fg=TEXT_SOFT, font=self.f_body).pack(anchor="w", pady=(6, 0))

        nav = tk.Frame(header, bg=BG)
        nav.grid(row=0, column=1, sticky="e")
        for label, active in [("仪表盘", True), ("工作空间", False), ("账号管理", False), ("日志", False)]:
            pill = tk.Label(nav, text=label, bg=TEXT if active else "#eef2f8", fg="#ffffff" if active else TEXT, font=self.f_button, padx=18, pady=10, cursor="hand2")
            pill.pack(side="left", padx=6)

        notice_card = tk.Frame(parent, bg=INFO_BG, highlightbackground=BORDER, highlightthickness=1)
        notice_card.grid(row=1, column=0, sticky="ew", pady=(0, 18))
        tk.Label(notice_card, textvariable=self.notice, bg=INFO_BG, fg=ACCENT, font=self.f_button, anchor="w", padx=16, pady=12).pack(fill="x")

    def _mini_card(self, parent, title_var, subtitle_var, icon, icon_bg):
        frame = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        tk.Label(frame, text=icon, bg=icon_bg, fg=TEXT, font=(self.font_ui, 13), width=3, pady=8).pack(anchor="w", padx=16, pady=(16, 12))
        tk.Label(frame, textvariable=title_var, bg=SURFACE, fg=TEXT, font=self.f_metric).pack(anchor="w", padx=16)
        tk.Label(frame, textvariable=subtitle_var, bg=SURFACE, fg=TEXT_SOFT, font=self.f_body).pack(anchor="w", padx=16, pady=(8, 16))
        return frame

    def _build_summary_cards(self, parent):
        cards = tk.Frame(parent, bg=BG)
        cards.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1)

        self._mini_card(cards, self.card_bridge_title, self.card_bridge_sub, "◎", SUCCESS_BG).grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._mini_card(cards, self.card_channel_title, self.card_channel_sub, "≋", ACCENT_SOFT).grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        self._mini_card(cards, self.card_happy_title, self.card_happy_sub, "⌘", WARNING_BG).grid(row=0, column=2, sticky="nsew", padx=(0, 12))
        self._mini_card(cards, self.card_account_title, self.card_account_sub, "◉", DANGER_BG).grid(row=0, column=3, sticky="nsew")

    def _section_card(self, parent, title, subtitle=""):
        frame = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        head = tk.Frame(frame, bg=SURFACE)
        head.pack(fill="x", padx=18, pady=(18, 8))
        tk.Label(head, text=title, bg=SURFACE, fg=TEXT, font=self.f_h1).pack(anchor="w")
        if subtitle:
            tk.Label(head, text=subtitle, bg=SURFACE, fg=TEXT_SOFT, font=self.f_body).pack(anchor="w", pady=(6, 0))
        return frame

    def _build_content(self, parent):
        content = tk.Frame(parent, bg=BG)
        content.grid(row=3, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=5)
        content.grid_columnconfigure(1, weight=4)
        content.grid_rowconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)

        left = tk.Frame(content, bg=BG)
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 16))
        left.grid_columnconfigure(0, weight=1)
        left.grid_columnconfigure(1, weight=1)
        left.grid_rowconfigure(1, weight=1)
        left.grid_rowconfigure(2, weight=1)

        right = tk.Frame(content, bg=BG)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        self._build_workspace_card(left)
        self._build_channel_card(left)
        self._build_recent_card(left)
        self._build_account_card(right)
        self._build_log_card(right)

    def _build_workspace_card(self, parent):
        card = self._section_card(parent, "工作空间选择器", "不用再手输整条路径，直接选择文件夹，然后分发给默认目录、飞书或 QQ。")
        card.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 16))

        info = tk.Frame(card, bg=SURFACE)
        info.pack(fill="x", padx=18, pady=(4, 12))
        self._info_line(info, "新会话默认目录", self.default_dir)
        self._info_line(info, "飞书当前绑定", self.feishu_dir)
        self._info_line(info, "QQ 当前绑定", self.qq_dir)

        form = tk.Frame(card, bg=SURFACE)
        form.pack(fill="x", padx=18, pady=(0, 14))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=2)
        self._entry_with_label(form, "预设名称", self.selected_name, 0, 0)
        self._entry_with_label(form, "工作空间路径", self.selected_path, 0, 1)

        actions = tk.Frame(card, bg=SURFACE)
        actions.pack(fill="x", padx=18, pady=(0, 18))
        SoftButton(actions, "选择文件夹", self.pick_folder, "soft").pack(side="left")
        SoftButton(actions, "新增预设", self.add_preset, "primary").pack(side="left", padx=10)
        SoftButton(actions, "删除预设", self.delete_preset, "danger").pack(side="left")
        SoftButton(actions, "刷新", self.refresh_all, "ghost").pack(side="right")

        lower = tk.Frame(card, bg=SURFACE)
        lower.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        lower.grid_columnconfigure(0, weight=1)
        lower.grid_columnconfigure(1, weight=1)
        lower.grid_rowconfigure(0, weight=1)

        preset_wrap = tk.Frame(lower, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        preset_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(preset_wrap, text="项目预设", bg=SURFACE_ALT, fg=TEXT, font=self.f_h2).pack(anchor="w", padx=14, pady=(12, 10))
        self.preset_tree = ttk.Treeview(preset_wrap, columns=("name", "path"), show="headings", height=8)
        self.preset_tree.heading("name", text="名称")
        self.preset_tree.heading("path", text="路径")
        self.preset_tree.column("name", width=140)
        self.preset_tree.column("path", width=420)
        self.preset_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.preset_tree.bind("<<TreeviewSelect>>", lambda _e: self.on_preset_select())

        quick = tk.Frame(lower, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        quick.grid(row=0, column=1, sticky="nsew")
        tk.Label(quick, text="一键分发", bg=SURFACE_ALT, fg=TEXT, font=self.f_h2).pack(anchor="w", padx=14, pady=(12, 10))
        btns = tk.Frame(quick, bg=SURFACE_ALT)
        btns.pack(fill="x", padx=12)
        SoftButton(btns, "设为默认", self.set_default_to_selected, "soft").pack(fill="x", pady=(0, 8))
        SoftButton(btns, "仅设飞书", self.set_feishu_to_selected, "soft").pack(fill="x", pady=(0, 8))
        SoftButton(btns, "仅设 QQ", self.set_qq_to_selected, "soft").pack(fill="x", pady=(0, 8))
        SoftButton(btns, "默认并重启 Bridge", self.switch_bridge_to_selected, "primary").pack(fill="x", pady=(0, 12))

        tk.Label(quick, text="说明：飞书和 QQ 共用一个 Bridge 进程，但每个聊天绑定都可以有自己的工作目录。", bg=SURFACE_ALT, fg=TEXT_SOFT, font=self.f_body, justify="left", wraplength=320).pack(anchor="w", padx=14, pady=(0, 10))

    def _build_channel_card(self, parent):
        card = self._section_card(parent, "桥接状态与通道工作空间", "这里会直接显示当前 Feishu / QQ 各自实际绑定到哪个项目。")
        card.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 16))

        state_box = tk.Frame(card, bg=SURFACE)
        state_box.pack(fill="x", padx=18, pady=(4, 14))
        self.bridge_pill = tk.Label(state_box, text="Bridge", bg=SUCCESS_BG, fg=SUCCESS, font=self.f_chip, padx=14, pady=8)
        self.bridge_pill.pack(side="left")
        self.channel_pill = tk.Label(state_box, text="通道", bg=ACCENT_SOFT, fg=ACCENT, font=self.f_chip, padx=14, pady=8)
        self.channel_pill.pack(side="left", padx=8)
        self.happy_pill = tk.Label(state_box, text="Happy", bg=WARNING_BG, fg=WARNING, font=self.f_chip, padx=14, pady=8)
        self.happy_pill.pack(side="left")

        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(left, text="运行参数", bg=SURFACE_ALT, fg=TEXT, font=self.f_h2).pack(anchor="w", padx=14, pady=(12, 10))
        self.status_text = tk.Text(left, height=13, bg=SURFACE_ALT, fg=TEXT, relief="flat", wrap="word", font=self.f_code, padx=14, pady=10)
        self.status_text.pack(fill="both", expand=True)

        right = tk.Frame(body, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew")
        tabs = ttk.Notebook(right)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)

        binding_tab = tk.Frame(tabs, bg=SURFACE)
        capability_tab = tk.Frame(tabs, bg=SURFACE)
        tabs.add(binding_tab, text="Feishu / QQ")
        tabs.add(capability_tab, text="能力说明")

        self.binding_tree = ttk.Treeview(binding_tab, columns=("channel", "chat", "workdir", "mode"), show="headings", height=8)
        for key, title, width in [("channel", "通道", 90), ("chat", "聊天 ID", 210), ("workdir", "工作空间", 280), ("mode", "模式", 80)]:
            self.binding_tree.heading(key, text=title)
            self.binding_tree.column(key, width=width)
        self.binding_tree.pack(fill="both", expand=True, padx=8, pady=8)

        self.cap_text = tk.Text(capability_tab, bg=SURFACE, fg=TEXT, relief="flat", wrap="word", font=self.f_body, padx=14, pady=14)
        self.cap_text.pack(fill="both", expand=True)

    def _build_recent_card(self, parent):
        card = self._section_card(parent, "Happy 最近目录", "Happy Codex 仍然按启动目录生效，这里保留最近项目列表，方便快速继续。")
        card.grid(row=2, column=0, columnspan=2, sticky="nsew")

        actions = tk.Frame(card, bg=SURFACE)
        actions.pack(fill="x", padx=18, pady=(4, 12))
        SoftButton(actions, "启动选中目录", self.launch_happy_recent, "primary").pack(side="left")
        SoftButton(actions, "加入项目预设", self.add_recent_to_presets, "soft").pack(side="left", padx=10)
        SoftButton(actions, "从预设启动 Happy", self.launch_happy_selected, "ghost").pack(side="left")

        self.recent_tree = ttk.Treeview(card, columns=("path",), show="headings", height=8)
        self.recent_tree.heading("path", text="最近启动目录")
        self.recent_tree.column("path", width=920)
        self.recent_tree.pack(fill="both", expand=True, padx=18, pady=(0, 18))

    def _build_account_card(self, parent):
        card = self._section_card(parent, "Codex 账号管理", "当前账号信息来自本机 auth.json。你可以保存当前登录态为快照，然后一键切换。")
        card.grid(row=0, column=0, sticky="nsew", pady=(0, 16))

        summary = tk.Frame(card, bg=SURFACE)
        summary.pack(fill="x", padx=18, pady=(4, 12))
        summary.grid_columnconfigure(0, weight=1)
        summary.grid_columnconfigure(1, weight=1)

        current = tk.Frame(summary, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        current.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(current, text="当前账号", bg=SURFACE_ALT, fg=TEXT, font=self.f_h2).pack(anchor="w", padx=14, pady=(12, 8))
        self.account_text = tk.Text(current, height=8, bg=SURFACE_ALT, fg=TEXT, relief="flat", wrap="word", font=self.f_body, padx=14, pady=8)
        self.account_text.pack(fill="both", expand=True)

        quick = tk.Frame(summary, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        quick.grid(row=0, column=1, sticky="nsew")
        tk.Label(quick, text="快捷操作", bg=SURFACE_ALT, fg=TEXT, font=self.f_h2).pack(anchor="w", padx=14, pady=(12, 10))
        self._entry_with_label(quick, "快照名称", self.profile_name_var, 1, 0, parent_bg=SURFACE_ALT, full_width=True)
        quick_actions = tk.Frame(quick, bg=SURFACE_ALT)
        quick_actions.pack(fill="x", padx=14, pady=(10, 4))
        SoftButton(quick_actions, "保存当前账号快照", self.save_codex_snapshot, "primary").pack(fill="x", pady=(0, 8))
        SoftButton(quick_actions, "新开终端登录", self.codex_login, "soft").pack(fill="x", pady=(0, 8))
        SoftButton(quick_actions, "打开认证目录", self.open_codex_folder, "ghost").pack(fill="x")

        self.profile_tree = ttk.Treeview(card, columns=("name", "email", "plan", "provider", "saved"), show="headings", height=8)
        for key, title, width in [("name", "快照名称", 130), ("email", "账号", 220), ("plan", "方案", 90), ("provider", "登录源", 90), ("saved", "保存时间", 150)]:
            self.profile_tree.heading(key, text=title)
            self.profile_tree.column(key, width=width)
        self.profile_tree.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        profile_actions = tk.Frame(card, bg=SURFACE)
        profile_actions.pack(fill="x", padx=18, pady=(0, 18))
        SoftButton(profile_actions, "切换到选中快照", self.switch_codex_snapshot, "soft").pack(side="left")
        SoftButton(profile_actions, "删除快照", self.delete_codex_snapshot, "danger").pack(side="left", padx=10)
        SoftButton(profile_actions, "刷新账号信息", self.refresh_all, "ghost").pack(side="right")

    def _build_log_card(self, parent):
        card = self._section_card(parent, "Bridge 与日志", "桥接启停、自动刷新、审计记录和运行日志都在这里。")
        card.grid(row=1, column=0, sticky="nsew")

        bar = tk.Frame(card, bg=SURFACE)
        bar.pack(fill="x", padx=18, pady=(4, 12))
        SoftButton(bar, "启动 Bridge", self.start_bridge_action, "primary").pack(side="left")
        SoftButton(bar, "停止", self.stop_bridge_action, "danger").pack(side="left", padx=8)
        SoftButton(bar, "重启", self.restart_bridge_action, "soft").pack(side="left")
        tk.Label(bar, text="自动刷新", bg=SURFACE, fg=TEXT_SOFT, font=self.f_body).pack(side="right")
        combo = ttk.Combobox(bar, textvariable=self.refresh_seconds, values=[5, 10, 15, 30, 60], width=6, state="readonly")
        combo.pack(side="right", padx=(0, 8))
        combo.bind("<<ComboboxSelected>>", lambda _e: self.schedule_refresh())

        tabs = ttk.Notebook(card)
        tabs.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        audit_tab = tk.Frame(tabs, bg=SURFACE)
        bridge_tab = tk.Frame(tabs, bg=SURFACE)
        happy_tab = tk.Frame(tabs, bg=SURFACE)
        tabs.add(audit_tab, text="审计记录")
        tabs.add(bridge_tab, text="Bridge 日志")
        tabs.add(happy_tab, text="Happy 日志")

        self.audit_tree = ttk.Treeview(audit_tab, columns=("channel", "direction", "summary", "time"), show="headings", height=10)
        for key, title, width in [("channel", "通道", 80), ("direction", "方向", 80), ("summary", "摘要", 360), ("time", "时间", 180)]:
            self.audit_tree.heading(key, text=title)
            self.audit_tree.column(key, width=width)
        self.audit_tree.pack(fill="both", expand=True, padx=8, pady=8)

        self.bridge_log = tk.Text(bridge_tab, bg=SURFACE_ALT, fg=TEXT, relief="flat", wrap="word", font=self.f_code_small, padx=14, pady=14)
        self.bridge_log.pack(fill="both", expand=True, padx=8, pady=8)

        self.happy_log = tk.Text(happy_tab, bg=SURFACE_ALT, fg=TEXT, relief="flat", wrap="word", font=self.f_code_small, padx=14, pady=14)
        self.happy_log.pack(fill="both", expand=True, padx=8, pady=8)

    def set_notice(self, text, ok=True):
        self.notice.set(text)

    def _info_line(self, parent, label, var):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=f"{label}：", bg=SURFACE, fg=TEXT_SOFT, font=self.f_body).pack(side="left")
        tk.Label(row, textvariable=var, bg=SURFACE, fg=TEXT, font=self.f_body, anchor="w").pack(side="left")

    def _entry_with_label(self, parent, label, variable, row, column, parent_bg=SURFACE, full_width=False):
        wrap = tk.Frame(parent, bg=parent_bg)
        if full_width:
            wrap.pack(fill="x", padx=14, pady=(0, 0))
        else:
            wrap.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0))
            parent.grid_columnconfigure(column, weight=1)
        tk.Label(wrap, text=label, bg=parent_bg, fg=TEXT_SOFT, font=self.f_body).pack(anchor="w")
        entry = tk.Entry(wrap, textvariable=variable, bg=SURFACE, fg=TEXT, relief="flat", highlightbackground=BORDER, highlightthickness=1, insertbackground=TEXT, font=self.f_body)
        entry.pack(fill="x", pady=(6, 0), ipady=8)

    def schedule_refresh(self):
        if self.auto_job:
            self.after_cancel(self.auto_job)
        self.auto_job = self.after(max(self.refresh_seconds.get(), 5) * 1000, self.auto_refresh)

    def auto_refresh(self):
        self.refresh_all()
        self.schedule_refresh()

    def state(self):
        self.manager = ensure_manager()
        return {
            "env": parse_env(CTI_CONFIG),
            "bridge": read_json(CTI_STATUS, {}),
            "happy": read_json(HAPPY_DAEMON, {}),
            "bindings": read_json(CTI_BINDINGS, {}),
            "audit": read_json(CTI_AUDIT, [])[-30:][::-1],
            "account": codex_account_info(),
        }

    def channel_dir(self, bindings, channel):
        for value in bindings.values():
            if value.get("channelType") == channel:
                return value.get("workingDirectory", "-")
        return "-"

    def refresh_all(self):
        state = self.state()
        env = state["env"]
        bridge = state["bridge"]
        happy = state["happy"]
        bindings = state["bindings"]
        account = state["account"]

        self.default_dir.set(env.get("CTI_DEFAULT_WORKDIR", "-"))
        self.feishu_dir.set(self.channel_dir(bindings, "feishu"))
        self.qq_dir.set(self.channel_dir(bindings, "qq"))

        bridge_running = bool(bridge.get("running"))
        self.card_bridge_title.set("运行中" if bridge_running else "未运行")
        self.card_channel_title.set(env.get("CTI_ENABLED_CHANNELS", "-"))
        self.card_happy_title.set(str(happy.get("httpPort", "-")))
        self.card_account_title.set(account.get("email", "-"))
        self.card_account_sub.set(f"当前 Codex · {account.get('plan', '-')}")

        self.bridge_pill.configure(text="Bridge 运行中" if bridge_running else "Bridge 未运行", bg=SUCCESS_BG if bridge_running else DANGER_BG, fg=SUCCESS if bridge_running else DANGER)
        self.channel_pill.configure(text=f"通道：{env.get('CTI_ENABLED_CHANNELS', '-')}")
        self.happy_pill.configure(text=f"Happy 端口：{happy.get('httpPort', '-')}")

        self.status_text.delete("1.0", tk.END)
        self.status_text.insert("1.0", "\n".join([
            f"运行时              {env.get('CTI_RUNTIME', '-')}",
            f"默认目录            {env.get('CTI_DEFAULT_WORKDIR', '-')}",
            f"Bridge PID          {bridge.get('pid', '-')}",
            f"Bridge 通道         {env.get('CTI_ENABLED_CHANNELS', '-')}",
            f"Bridge 模式         {env.get('CTI_DEFAULT_MODE', '-')}",
            "",
            f"Happy PID           {happy.get('pid', '-')}",
            f"Happy HTTP 端口     {happy.get('httpPort', '-')}",
            f"Happy 心跳          {happy.get('lastHeartbeat', '-')}",
        ]))

        self.account_text.delete("1.0", tk.END)
        self.account_text.insert("1.0", "\n".join([
            f"邮箱：{account.get('email', '-')}",
            f"登录方式：{account.get('mode', '-')}",
            f"认证来源：{account.get('provider', '-')}",
            f"订阅方案：{account.get('plan', '-')}",
            f"组织：{account.get('org', '-')}",
            f"最近刷新：{account.get('last_refresh', '-')}",
        ]))

        for tree in [self.preset_tree, self.recent_tree, self.binding_tree, self.audit_tree, self.profile_tree]:
            for row in tree.get_children():
                tree.delete(row)

        for item in self.manager["projectPresets"]:
            self.preset_tree.insert("", tk.END, iid=item["id"], values=(item.get("name", ""), item.get("path", "")))

        for item in self.manager["happyRecent"]:
            self.recent_tree.insert("", tk.END, values=(item.get("path", ""),))

        for value in bindings.values():
            self.binding_tree.insert("", tk.END, values=(value.get("channelType", ""), value.get("chatId", ""), value.get("workingDirectory", ""), value.get("mode", "")))

        for item in state["audit"]:
            self.audit_tree.insert("", tk.END, values=(item.get("channelType", ""), item.get("direction", ""), item.get("summary", ""), item.get("createdAt", "")))

        for item in self.manager["codexProfiles"]:
            self.profile_tree.insert("", tk.END, iid=item["id"], values=(item.get("name", ""), item.get("email", ""), item.get("plan", ""), item.get("provider", ""), item.get("savedAt", "")))

        self.cap_text.delete("1.0", tk.END)
        self.cap_text.insert("1.0", "\n".join([
            "飞书",
            "- 更稳，适合长期远程控制",
            "- 支持图片输入",
            "- 可以独立绑定自己的工作空间",
            "",
            "QQ",
            "- 私聊入口方便",
            "- 当前不支持图片回传",
            "- 也可以独立绑定自己的工作空间",
            "",
            "Happy Codex",
            "- 过程展示更强",
            "- 权限和连接更容易卡住",
            "- 工作空间按启动目录或当前会话目录生效",
        ]))

        self.bridge_log.delete("1.0", tk.END)
        self.bridge_log.insert("1.0", tail_lines(CTI_LOG, 120))

        self.happy_log.delete("1.0", tk.END)
        daemon_log = Path(happy.get("daemonLogPath", "")) if happy.get("daemonLogPath") else None
        self.happy_log.insert("1.0", tail_lines(daemon_log, 120) if daemon_log else "")

    def selected_preset(self):
        selected = self.preset_tree.selection()
        if not selected:
            return None
        target_id = selected[0]
        for item in self.manager["projectPresets"]:
            if item["id"] == target_id:
                return item
        return None

    def on_preset_select(self):
        item = self.selected_preset()
        if not item:
            return
        self.selected_name.set(item.get("name", ""))
        self.selected_path.set(item.get("path", ""))

    def pick_folder(self):
        folder = filedialog.askdirectory(title="选择工作空间文件夹")
        if not folder:
            return
        self.selected_path.set(folder)
        if not self.selected_name.get().strip():
            self.selected_name.set(Path(folder).name or folder)

    def add_preset(self):
        name = self.selected_name.get().strip()
        target = self.selected_path.get().strip()
        if not name or not target:
            return messagebox.showerror("提示", "预设名称和工作空间路径不能为空。")
        if not path_ok(target):
            return messagebox.showerror("提示", "所选工作空间不存在或不是文件夹。")

        item = self.selected_preset()
        if item:
            item["name"] = name
            item["path"] = target
            text = f"已更新项目预设：{name}"
        else:
            self.manager["projectPresets"].append({"id": str(uuid.uuid4()), "name": name, "path": target})
            text = f"已新增项目预设：{name}"
        write_json(MANAGER_SETTINGS, self.manager)
        self.set_notice(text)
        self.refresh_all()

    def delete_preset(self):
        item = self.selected_preset()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个项目预设。")
        self.manager["projectPresets"] = [entry for entry in self.manager["projectPresets"] if entry["id"] != item["id"]]
        write_json(MANAGER_SETTINGS, self.manager)
        self.selected_name.set("")
        self.selected_path.set("")
        self.set_notice(f"已删除项目预设：{item['name']}")
        self.refresh_all()

    def set_default_to_selected(self):
        item = self.selected_preset()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个项目预设。")
        write_env_key(CTI_CONFIG, "CTI_DEFAULT_WORKDIR", item["path"])
        self.set_notice(f"已更新新会话默认目录：{item['path']}")
        self.refresh_all()

    def set_feishu_to_selected(self):
        item = self.selected_preset()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个项目预设。")
        count = update_channel_binding_workdir("feishu", item["path"])
        self.set_notice(f"已更新飞书工作空间：{item['path']}，影响 {count} 个绑定。")
        self.refresh_all()

    def set_qq_to_selected(self):
        item = self.selected_preset()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个项目预设。")
        count = update_channel_binding_workdir("qq", item["path"])
        self.set_notice(f"已更新 QQ 工作空间：{item['path']}，影响 {count} 个绑定。")
        self.refresh_all()

    def switch_bridge_to_selected(self):
        item = self.selected_preset()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个项目预设。")
        write_env_key(CTI_CONFIG, "CTI_DEFAULT_WORKDIR", item["path"])
        result = restart_bridge()
        self.set_notice(f"已更新默认目录并重启 Bridge。{result}")
        self.after(1200, self.refresh_all)

    def launch_happy_selected(self):
        item = self.selected_preset()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个项目预设。")
        self.set_notice(launch_happy(item["path"]))
        self.refresh_all()

    def launch_happy_recent(self):
        selected = self.recent_tree.selection()
        if not selected:
            return messagebox.showinfo("提示", "请先选中一个 Happy 最近目录。")
        target = self.recent_tree.item(selected[0], "values")[0]
        if not path_ok(target):
            return messagebox.showerror("提示", "这个目录已经不存在了。")
        self.set_notice(launch_happy(target))
        self.refresh_all()

    def add_recent_to_presets(self):
        selected = self.recent_tree.selection()
        if not selected:
            return messagebox.showinfo("提示", "请先选中一个 Happy 最近目录。")
        target = self.recent_tree.item(selected[0], "values")[0]
        if any(item.get("path") == target for item in self.manager["projectPresets"]):
            return self.set_notice("这个目录已经在项目预设里了。", ok=False)
        self.manager["projectPresets"].append({"id": str(uuid.uuid4()), "name": Path(target).name or target, "path": target})
        write_json(MANAGER_SETTINGS, self.manager)
        self.set_notice(f"已把最近目录加入预设：{target}")
        self.refresh_all()

    def selected_profile(self):
        selected = self.profile_tree.selection()
        if not selected:
            return None
        target_id = selected[0]
        for item in self.manager["codexProfiles"]:
            if item["id"] == target_id:
                return item
        return None

    def save_codex_snapshot(self):
        name = self.profile_name_var.get().strip() or f"账号快照 {datetime.now().strftime('%m-%d %H:%M')}"
        try:
            save_current_codex_profile(name)
        except Exception as exc:
            return messagebox.showerror("提示", f"保存账号快照失败：{exc}")
        self.profile_name_var.set("")
        self.set_notice(f"已保存 Codex 账号快照：{name}")
        self.refresh_all()

    def switch_codex_snapshot(self):
        item = self.selected_profile()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个账号快照。")
        if not messagebox.askyesno("确认切换", f"要切换到账号快照“{item['name']}”吗？"):
            return
        try:
            switch_codex_profile(item["path"])
        except Exception as exc:
            return messagebox.showerror("提示", f"切换账号失败：{exc}")
        self.set_notice(f"已切换到 Codex 账号快照：{item['name']}。如有需要，请重新启动相关会话。")
        self.refresh_all()

    def delete_codex_snapshot(self):
        item = self.selected_profile()
        if not item:
            return messagebox.showinfo("提示", "请先选中一个账号快照。")
        if not messagebox.askyesno("确认删除", f"要删除账号快照“{item['name']}”吗？"):
            return
        try:
            Path(item["path"]).unlink(missing_ok=True)
        except Exception:
            pass
        self.manager["codexProfiles"] = [entry for entry in self.manager["codexProfiles"] if entry["id"] != item["id"]]
        write_json(MANAGER_SETTINGS, self.manager)
        self.set_notice(f"已删除账号快照：{item['name']}")
        self.refresh_all()

    def open_codex_folder(self):
        subprocess.Popen(["explorer.exe", str(CODEX_HOME)])

    def codex_login(self):
        subprocess.Popen(["powershell.exe", "-NoExit", "-Command", "codex login"], cwd=str(HOME), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        self.set_notice("已打开新的终端窗口，你可以在那里登录另一个 Codex 账号。")

    def start_bridge_action(self):
        self.set_notice(start_bridge())
        self.after(1200, self.refresh_all)

    def stop_bridge_action(self):
        self.set_notice(stop_bridge())
        self.after(1200, self.refresh_all)

    def restart_bridge_action(self):
        self.set_notice(restart_bridge())
        self.after(1200, self.refresh_all)


if __name__ == "__main__":
    App().mainloop()
