# Docker Pilot

A GNOME top-bar indicator for managing Docker Compose projects.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

## Features

- **System tray indicator** — lives in the GNOME top bar, always one click away
- **Start / Stop / Restart** any Docker Compose project from the menu
- **Switch** between projects (stops the running one, starts the selected one)
- **Port conflict detection** — warns when multiple projects bind the same host port
- **Configurable scan directories** — auto-discovers `docker-compose.yml` / `compose.yml` files
- **Recent projects** — quick access to the projects you use most
- **See All dialog** — browse every discovered project with container details and health status
- **Settings dialog** — add or remove scan directories from the GUI
- **Desktop notifications** for operation results
- **Autostart** on login (with configurable delay)

## Requirements

- Ubuntu / GNOME desktop (GTK 3)
- Docker (with `docker compose` v2)
- Python 3 system packages:

```
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7
```

## Install

```bash
git clone https://github.com/YOUR_USER/docker-pilot.git
cd docker-pilot
./install.sh
```

This creates a `docker-pilot` launcher in `~/.local/bin` and an autostart desktop entry so it runs on login.

### Run manually

```bash
docker-pilot
```

## Uninstall

```bash
./uninstall.sh
```

Removes the launcher, autostart entry, and stops any running instance. Your configuration is preserved.

## Configuration

Config file: `~/.config/docker-pilot/config.json`

```json
{
  "scan_dirs": ["~/projects", "~/perso"],
  "recent_projects": {}
}
```

- **scan_dirs** — directories to scan for Docker Compose files (one level deep)
- **recent_projects** — automatically maintained; tracks last-used timestamps

You can also edit scan directories from the Settings menu inside the indicator.

## License

[GPL-3.0](LICENSE)
