#!/usr/bin/env python3
"""Docker Pilot — GNOME top bar indicator for Docker Compose project management."""

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")

from gi.repository import AyatanaAppIndicator3, GLib, Gtk, Notify

# --- Constants ---

APP_ID = "docker-pilot"
REFRESH_INTERVAL_SECONDS = 10
DOCKER_TIMEOUT_SECONDS = 15
OPERATION_TIMEOUT_SECONDS = 60
COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)
DEFAULT_SCAN_DIRS = [os.path.expanduser("~/projects"), os.path.expanduser("~/perso")]
CONFIG_DIR = os.path.expanduser("~/.config/docker-pilot")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
MENU_MAX_PROJECTS = 5
ICON_ACTIVE = "network-server-symbolic"
ICON_ATTENTION = "emblem-system-symbolic"
PORT_REGEX = re.compile(r"(?:[\d.]+|::):(\d+)->")
STATUS_ICONS = {"green": "🟢", "orange": "🟠", "red": "🔴", "stopped": "⚫"}
CONTAINER_STATE_ICONS = {
    "running": "🟢",
    "restarting": "🟠",
    "paused": "🟠",
    "exited": "⚫",
    "dead": "🔴",
    "created": "⚫",
}


# --- Config ---


@dataclass
class Config:
    scan_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_SCAN_DIRS))
    recent_projects: dict[str, float] = field(default_factory=dict)

    @staticmethod
    def load() -> "Config":
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            config = Config()
            dirs = data.get("scan_dirs")
            if isinstance(dirs, list) and all(isinstance(d, str) for d in dirs):
                config.scan_dirs = [os.path.expanduser(d) for d in dirs]
            recent = data.get("recent_projects")
            if isinstance(recent, dict):
                config.recent_projects = {
                    k: float(v) for k, v in recent.items()
                    if isinstance(k, str) and isinstance(v, (int, float))
                }
            return config
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return Config()

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "scan_dirs": self.scan_dirs,
                "recent_projects": self.recent_projects,
            }, f, indent=2)

    def touch(self, project_name: str):
        self.recent_projects[project_name] = time.time()
        self.save()


# --- Data classes ---


@dataclass
class Container:
    name: str
    service: str
    state: str
    host_ports: list[str] = field(default_factory=list)


@dataclass
class Project:
    name: str
    status: str
    config_file: str
    is_running: bool
    containers: list[Container] = field(default_factory=list)

    @property
    def project_dir(self) -> str:
        return os.path.dirname(self.config_file)

    @property
    def health_status(self) -> str:
        if not self.is_running:
            return "stopped"
        if not self.containers:
            return "red"
        states = {c.state for c in self.containers}
        if states == {"running"}:
            return "green"
        if "running" in states:
            return "orange"
        return "red"


@dataclass
class DockerState:
    projects: list[Project] = field(default_factory=list)
    port_conflicts: dict[str, list[str]] = field(default_factory=dict)
    docker_available: bool = True
    error_message: str = ""


# --- Exceptions ---


class DockerError(Exception):
    pass


# --- Docker client ---


class DockerClient:
    """All Docker CLI interaction. No GTK dependency."""

    def __init__(self, config: Config):
        self.config = config

    @staticmethod
    def _run(
        cmd: list[str], timeout: int = DOCKER_TIMEOUT_SECONDS
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise DockerError("Docker is not installed")
        except subprocess.TimeoutExpired:
            raise DockerError(f"Command timed out after {timeout}s")

    @staticmethod
    def _get_projects() -> list[dict]:
        result = DockerClient._run(
            ["docker", "compose", "ls", "--all", "--format", "json"]
        )
        if result.returncode != 0:
            raise DockerError(result.stderr.strip() or "Failed to list projects")
        output = result.stdout.strip()
        if not output:
            return []
        return json.loads(output)

    @staticmethod
    def _get_containers() -> list[dict]:
        result = DockerClient._run(["docker", "ps", "-a", "--format", "json"])
        if result.returncode != 0:
            raise DockerError(result.stderr.strip() or "Failed to list containers")
        output = result.stdout.strip()
        if not output:
            return []
        # NDJSON: one JSON object per line
        containers = []
        for line in output.splitlines():
            line = line.strip()
            if line:
                containers.append(json.loads(line))
        return containers

    @staticmethod
    def _parse_ports(ports_str: str) -> list[str]:
        """Extract host ports from Docker's Ports field."""
        if not ports_str:
            return []
        return sorted(set(PORT_REGEX.findall(ports_str)))

    @staticmethod
    def _parse_labels(labels_str: str) -> dict[str, str]:
        """Parse comma-separated key=value labels."""
        result = {}
        if not labels_str:
            return result
        for part in labels_str.split(","):
            if "=" in part:
                key, _, value = part.partition("=")
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def _detect_port_conflicts(
        projects: list[Project],
    ) -> dict[str, list[str]]:
        """Find host ports used by multiple running projects."""
        port_to_projects: dict[str, list[str]] = {}
        for project in projects:
            if not project.is_running:
                continue
            project_ports: set[str] = set()
            for container in project.containers:
                for port in container.host_ports:
                    project_ports.add(port)
            for port in project_ports:
                port_to_projects.setdefault(port, []).append(project.name)
        return {
            port: projs
            for port, projs in sorted(port_to_projects.items())
            if len(projs) > 1
        }

    def _scan_filesystem_projects(self) -> list[dict]:
        """Discover compose projects by scanning known directories."""
        found = []
        for scan_dir in self.config.scan_dirs:
            if not os.path.isdir(scan_dir):
                continue
            try:
                entries = os.listdir(scan_dir)
            except OSError:
                continue
            for entry in entries:
                project_path = os.path.join(scan_dir, entry)
                if not os.path.isdir(project_path):
                    continue
                for filename in COMPOSE_FILENAMES:
                    config_file = os.path.join(project_path, filename)
                    if os.path.isfile(config_file):
                        found.append(
                            {"Name": entry, "ConfigFiles": config_file, "Status": ""}
                        )
                        break  # Use the first compose file found
        return found

    def get_state(self) -> DockerState:
        """Build a complete atomic state snapshot."""
        try:
            raw_projects = self._get_projects()
            raw_containers = self._get_containers()
        except DockerError as e:
            return DockerState(docker_available=False, error_message=str(e))

        # Group containers by compose project name
        containers_by_project: dict[str, list[Container]] = {}
        for raw in raw_containers:
            labels = self._parse_labels(raw.get("Labels", ""))
            project_name = labels.get("com.docker.compose.project", "")
            if not project_name:
                continue
            service = labels.get("com.docker.compose.service", "")
            container = Container(
                name=raw.get("Names", ""),
                service=service,
                state=raw.get("State", ""),
                host_ports=self._parse_ports(raw.get("Ports", "")),
            )
            containers_by_project.setdefault(project_name, []).append(container)

        # Merge Docker-tracked projects with filesystem-discovered ones
        known_names = {p.get("Name", "") for p in raw_projects}
        for fs_proj in self._scan_filesystem_projects():
            if fs_proj["Name"] not in known_names:
                raw_projects.append(fs_proj)

        projects = []
        for raw_proj in raw_projects:
            name = raw_proj.get("Name", "")
            status = raw_proj.get("Status", "")
            config_files = raw_proj.get("ConfigFiles", "")
            # Use first config file if multiple are comma-separated
            config_file = config_files.split(",")[0].strip()
            is_running = "running" in status.lower()
            proj = Project(
                name=name,
                status=status,
                config_file=config_file,
                is_running=is_running,
                containers=sorted(
                    containers_by_project.get(name, []),
                    key=lambda c: c.service,
                ),
            )
            projects.append(proj)

        projects.sort(key=lambda p: p.name.lower())
        conflicts = self._detect_port_conflicts(projects)
        return DockerState(projects=projects, port_conflicts=conflicts)

    def start_project(self, config_file: str) -> tuple[bool, str]:
        try:
            result = self._run(
                ["docker", "compose", "-f", config_file, "up", "-d"],
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return False, result.stderr.strip() or "Failed to start"
            return True, "Started successfully"
        except DockerError as e:
            return False, str(e)

    def stop_project(self, config_file: str) -> tuple[bool, str]:
        try:
            result = self._run(
                ["docker", "compose", "-f", config_file, "stop"],
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return False, result.stderr.strip() or "Failed to stop"
            return True, "Stopped successfully"
        except DockerError as e:
            return False, str(e)

    def restart_project(self, config_file: str) -> tuple[bool, str]:
        try:
            result = self._run(
                ["docker", "compose", "-f", config_file, "restart"],
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return False, result.stderr.strip() or "Failed to restart"
            return True, "Restarted successfully"
        except DockerError as e:
            return False, str(e)

    def stop_all(self, config_files: list[str]) -> tuple[bool, str]:
        errors = []
        for config_file in config_files:
            ok, msg = self.stop_project(config_file)
            if not ok:
                errors.append(msg)
        if errors:
            return False, "; ".join(errors)
        return True, "All projects stopped"


# --- GTK Indicator ---


class DockerPilotIndicator:
    def __init__(self):
        self.config = Config.load()
        self.client = DockerClient(self.config)
        self.state = DockerState()
        self.busy = False
        self._last_menu_snapshot = None
        self._lock = threading.Lock()

        Notify.init(APP_ID)

        self.indicator = AyatanaAppIndicator3.Indicator.new(
            APP_ID,
            ICON_ACTIVE,
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        self._build_loading_menu()
        self._schedule_refresh()
        GLib.timeout_add_seconds(REFRESH_INTERVAL_SECONDS, self._on_refresh_timer)

    # --- Menu building ---

    def _build_loading_menu(self):
        menu = Gtk.Menu()
        self._add_disabled_item(menu, "Loading...")
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit_clicked)
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)

    def _build_menu(self):
        menu = Gtk.Menu()
        state = self.state

        if not state.docker_available:
            self._add_disabled_item(
                menu, state.error_message or "Docker is not running"
            )
            menu.append(Gtk.SeparatorMenuItem())
            refresh_item = Gtk.MenuItem(label="Refresh Now")
            refresh_item.connect("activate", self._on_refresh_clicked)
            menu.append(refresh_item)
            quit_item = Gtk.MenuItem(label="Quit")
            quit_item.connect("activate", self._on_quit_clicked)
            menu.append(quit_item)
            menu.show_all()
            self.indicator.set_menu(menu)
            return

        running = [p for p in state.projects if p.is_running]

        # Sort: running first, then by most recently used, then alphabetical
        recent = self.config.recent_projects
        sorted_projects = sorted(
            state.projects,
            key=lambda p: (not p.is_running, -recent.get(p.name, 0), p.name.lower()),
        )

        visible = sorted_projects[:MENU_MAX_PROJECTS]
        for project in visible:
            status = STATUS_ICONS[project.health_status]
            item = Gtk.MenuItem(label=f"{status}  {project.name}")
            item.set_submenu(self._build_project_submenu(project))
            menu.append(item)

        if len(sorted_projects) > MENU_MAX_PROJECTS:
            see_all_item = Gtk.MenuItem(
                label=f"See All ({len(sorted_projects)} projects)..."
            )
            see_all_item.connect(
                "activate", self._on_see_all_clicked, sorted_projects
            )
            menu.append(see_all_item)

        # Port conflicts
        if state.port_conflicts:
            self._add_separator(menu)
            conflicts_menu = Gtk.Menu()
            for port, projs in state.port_conflicts.items():
                self._add_disabled_item(
                    conflicts_menu, f":{port} used by: {', '.join(projs)}"
                )
            conflicts_item = Gtk.MenuItem(
                label=f"Port Conflicts ({len(state.port_conflicts)})"
            )
            conflicts_item.set_submenu(conflicts_menu)
            menu.append(conflicts_item)

        self._add_separator(menu)

        # Stop All
        if running:
            stop_all_item = Gtk.MenuItem(label="Stop All")
            if self.busy:
                stop_all_item.set_sensitive(False)
            else:
                stop_all_item.connect("activate", self._on_stop_all_clicked)
            menu.append(stop_all_item)

        # Refresh Now
        refresh_item = Gtk.MenuItem(label="Refresh Now")
        refresh_item.connect("activate", self._on_refresh_clicked)
        menu.append(refresh_item)

        # Settings
        settings_item = Gtk.MenuItem(label="Settings")
        settings_item.connect("activate", self._on_settings_clicked)
        menu.append(settings_item)

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit_clicked)
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

    def _build_project_submenu(self, project: Project) -> Gtk.Menu:
        submenu = Gtk.Menu()

        if project.containers:
            for container in project.containers:
                icon = CONTAINER_STATE_ICONS.get(container.state, "⚫")
                port_info = ""
                if container.host_ports:
                    port_info = "  :" + ", :".join(container.host_ports)
                self._add_disabled_item(
                    submenu, f"{icon}  {container.service}{port_info}"
                )
            self._add_separator(submenu)

        if project.is_running:
            stop_item = Gtk.MenuItem(label="Stop")
            restart_item = Gtk.MenuItem(label="Restart")
            if self.busy:
                stop_item.set_sensitive(False)
                restart_item.set_sensitive(False)
            else:
                stop_item.connect(
                    "activate", self._on_stop_clicked, project
                )
                restart_item.connect(
                    "activate", self._on_restart_clicked, project
                )
            submenu.append(stop_item)
            submenu.append(restart_item)
        else:
            has_running = any(p.is_running for p in self.state.projects)
            if has_running:
                switch_item = Gtk.MenuItem(label="Switch to")
                if self.busy:
                    switch_item.set_sensitive(False)
                else:
                    switch_item.connect(
                        "activate", self._on_switch_clicked, project
                    )
                submenu.append(switch_item)
            start_item = Gtk.MenuItem(label="Start")
            if self.busy:
                start_item.set_sensitive(False)
            else:
                start_item.connect(
                    "activate", self._on_start_clicked, project
                )
            submenu.append(start_item)

        open_item = Gtk.MenuItem(label="Open Folder")
        open_item.connect("activate", self._on_open_folder_clicked, project)
        submenu.append(open_item)

        submenu.show_all()
        return submenu

    # --- Menu helpers ---

    @staticmethod
    def _add_disabled_item(menu: Gtk.Menu, label: str):
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        menu.append(item)

    @staticmethod
    def _add_separator(menu: Gtk.Menu):
        menu.append(Gtk.SeparatorMenuItem())

    # --- Refresh mechanism ---

    def _menu_snapshot(self, state: DockerState) -> tuple:
        """Return a comparable representation of everything that affects the menu."""
        projects = tuple(
            (p.name, p.health_status, p.is_running,
             tuple((c.service, c.state, tuple(c.host_ports)) for c in p.containers))
            for p in state.projects
        )
        conflicts = tuple(sorted(state.port_conflicts.items()))
        return (
            state.docker_available,
            state.error_message,
            projects,
            conflicts,
            self.busy,
            tuple(sorted(self.config.recent_projects.items())),
        )

    def _schedule_refresh(self):
        thread = threading.Thread(target=self._refresh_worker, daemon=True)
        thread.start()

    def _refresh_worker(self):
        new_state = self.client.get_state()
        GLib.idle_add(self._apply_state, new_state)

    def _apply_state(self, new_state: DockerState):
        self.state = new_state
        snapshot = self._menu_snapshot(new_state)
        if snapshot != self._last_menu_snapshot:
            self._last_menu_snapshot = snapshot
            self._build_menu()
        return False  # Remove from idle queue

    def _on_refresh_timer(self):
        self._schedule_refresh()
        return True  # Keep timer alive

    # --- Operation handlers ---

    def _run_operation(self, description: str, operation, *args):
        with self._lock:
            if self.busy:
                return
            self.busy = True
        self._last_menu_snapshot = None
        self._build_menu()

        def worker():
            ok, msg = operation(*args)
            GLib.idle_add(self._on_operation_complete, description, ok, msg)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_operation_complete(self, description: str, ok: bool, msg: str):
        with self._lock:
            self.busy = False
        if ok:
            self._notify(description, msg)
        else:
            self._notify(f"{description} — Failed", msg)
        self._schedule_refresh()
        return False

    def _on_start_clicked(self, _item, project: Project):
        self.config.touch(project.name)
        self._run_operation(
            f"Start {project.name}",
            self.client.start_project,
            project.config_file,
        )

    def _on_switch_clicked(self, _item, project: Project):
        self.config.touch(project.name)
        running = [p for p in self.state.projects if p.is_running]
        config_files_to_stop = [p.config_file for p in running]
        self._run_operation(
            f"Switch to {project.name}",
            self._switch_operation,
            config_files_to_stop,
            project.config_file,
        )

    def _switch_operation(
        self, stop_configs: list[str], start_config: str
    ) -> tuple[bool, str]:
        errors = []
        for config_file in stop_configs:
            ok, msg = self.client.stop_project(config_file)
            if not ok:
                errors.append(msg)
        ok, msg = self.client.start_project(start_config)
        if not ok:
            errors.append(msg)
        if errors:
            return False, "; ".join(errors)
        return True, "Switched successfully"

    def _on_stop_clicked(self, _item, project: Project):
        self.config.touch(project.name)
        self._run_operation(
            f"Stop {project.name}",
            self.client.stop_project,
            project.config_file,
        )

    def _on_restart_clicked(self, _item, project: Project):
        self.config.touch(project.name)
        self._run_operation(
            f"Restart {project.name}",
            self.client.restart_project,
            project.config_file,
        )

    def _on_stop_all_clicked(self, _item):
        running = [p for p in self.state.projects if p.is_running]
        config_files = [p.config_file for p in running]
        self._run_operation("Stop All", self.client.stop_all, config_files)

    def _on_open_folder_clicked(self, _item, project: Project):
        try:
            subprocess.Popen(["xdg-open", project.project_dir])
        except Exception:
            pass

    def _on_refresh_clicked(self, _item):
        self._schedule_refresh()

    def _on_see_all_clicked(self, _item, sorted_projects: list):
        dialog = Gtk.Dialog(
            title="All Projects",
            transient_for=None,
            flags=0,
        )
        dialog.add_buttons(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(500, 400)

        content = dialog.get_content_area()
        content.set_spacing(4)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        content.pack_start(scrolled, True, True, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        scrolled.add(box)

        expanders: list[Gtk.Expander] = []

        def on_expander_activated(activated_exp):
            if activated_exp.get_expanded():
                return
            for exp in expanders:
                if exp is not activated_exp:
                    exp.set_expanded(False)

        for project in sorted_projects:
            icon = STATUS_ICONS[project.health_status]
            expander = Gtk.Expander(label=f"{icon}  {project.name}")
            expander.set_margin_top(2)
            expander.set_margin_bottom(2)
            expander.connect("activate", on_expander_activated)
            expanders.append(expander)

            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            inner.set_margin_start(16)
            inner.set_margin_top(4)
            inner.set_margin_bottom(4)

            # Container list
            if project.containers:
                for container in project.containers:
                    c_icon = CONTAINER_STATE_ICONS.get(container.state, "⚫")
                    port_info = ""
                    if container.host_ports:
                        port_info = "  :" + ", :".join(container.host_ports)
                    c_label = Gtk.Label(label=f"{c_icon}  {container.service}{port_info}")
                    c_label.set_halign(Gtk.Align.START)
                    inner.pack_start(c_label, False, False, 0)

            # Action buttons
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_box.set_margin_top(4)

            if project.is_running:
                stop_btn = Gtk.Button(label="Stop")
                stop_btn.connect(
                    "clicked",
                    lambda _b, p=project, d=dialog: (
                        d.destroy(),
                        self._on_stop_clicked(None, p),
                    ),
                )
                btn_box.pack_start(stop_btn, False, False, 0)
                restart_btn = Gtk.Button(label="Restart")
                restart_btn.connect(
                    "clicked",
                    lambda _b, p=project, d=dialog: (
                        d.destroy(),
                        self._on_restart_clicked(None, p),
                    ),
                )
                btn_box.pack_start(restart_btn, False, False, 0)
            else:
                start_btn = Gtk.Button(label="Start")
                start_btn.connect(
                    "clicked",
                    lambda _b, p=project, d=dialog: (
                        d.destroy(),
                        self._on_start_clicked(None, p),
                    ),
                )
                btn_box.pack_start(start_btn, False, False, 0)

            open_btn = Gtk.Button(label="Open Folder")
            open_btn.connect(
                "clicked",
                lambda _b, p=project: self._on_open_folder_clicked(None, p),
            )
            btn_box.pack_start(open_btn, False, False, 0)

            inner.pack_start(btn_box, False, False, 0)
            expander.add(inner)
            box.pack_start(expander, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _on_settings_clicked(self, _item):
        dialog = Gtk.Dialog(
            title="Docker Pilot Settings",
            transient_for=None,
            flags=0,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_size(450, 300)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(12)

        label = Gtk.Label(label="Project scan directories:")
        label.set_halign(Gtk.Align.START)
        content.pack_start(label, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        content.pack_start(scrolled, True, True, 0)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(listbox)

        dirs = list(self.config.scan_dirs)

        def rebuild_list():
            for child in listbox.get_children():
                listbox.remove(child)
            for i, d in enumerate(dirs):
                row = Gtk.ListBoxRow()
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                hbox.set_margin_start(4)
                hbox.set_margin_end(4)
                hbox.set_margin_top(2)
                hbox.set_margin_bottom(2)
                path_label = Gtk.Label(label=d)
                path_label.set_halign(Gtk.Align.START)
                path_label.set_hexpand(True)
                path_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
                hbox.pack_start(path_label, True, True, 0)
                remove_btn = Gtk.Button(label="Remove")
                remove_btn.connect("clicked", on_remove_clicked, i)
                hbox.pack_end(remove_btn, False, False, 0)
                row.add(hbox)
                listbox.add(row)
            listbox.show_all()

        def on_remove_clicked(_btn, index):
            del dirs[index]
            rebuild_list()

        def on_add_clicked(_btn):
            chooser = Gtk.FileChooserDialog(
                title="Select Directory",
                parent=dialog,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            chooser.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
            )
            if chooser.run() == Gtk.ResponseType.OK:
                path = chooser.get_filename()
                if path and path not in dirs:
                    dirs.append(path)
                    rebuild_list()
            chooser.destroy()

        rebuild_list()

        add_btn = Gtk.Button(label="Add Directory...")
        add_btn.set_halign(Gtk.Align.START)
        content.pack_start(add_btn, False, False, 0)
        add_btn.connect("clicked", on_add_clicked)

        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.config.scan_dirs = dirs
            self.config.save()
            self.client.config = self.config
            self._schedule_refresh()
        dialog.destroy()

    def _on_quit_clicked(self, _item):
        self._quit()

    # --- Notifications ---

    def _notify(self, title: str, body: str):
        try:
            notification = Notify.Notification.new(title, body)
            notification.show()
        except Exception:
            pass

    # --- Lifecycle ---

    def _quit(self):
        Notify.uninit()
        Gtk.main_quit()

    def run(self):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._quit)
        Gtk.main()


def main():
    indicator = DockerPilotIndicator()
    indicator.run()


if __name__ == "__main__":
    main()
