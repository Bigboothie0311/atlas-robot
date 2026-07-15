from pathlib import Path

import requests

ROBOT_ENV_PATH = Path("/home/atlas/atlas-robot/config/robot.env")

LHM_PORT = 8085
LHM_TIMEOUT_SECONDS = 2

# Confirmed against a real data.json sample from this exact machine — see
# docs/superpowers/specs/2026-07-14-jarvis-hud-v2-design.md section 3.
CPU_HARDWARE_NAME = "AMD Ryzen 7 5700G with Radeon Graphics"
GPU_HARDWARE_NAME = "AMD Radeon RX 9060 XT"
MEMORY_HARDWARE_NAME = "Total Memory"


def load_gaming_pc_ip():
    if not ROBOT_ENV_PATH.exists():
        return None

    for line in ROBOT_ENV_PATH.read_text().splitlines():
        line = line.strip()

        if line.startswith("GAMING_PC_IP="):
            ip = line.split("=", 1)[1].strip().strip('"').strip("'")

            if ip:
                return ip

    return None


def _parse_value(raw):
    # LibreHardwareMonitor values look like "11.9 %" or "47.3 °C".
    try:
        return float(raw.split()[0])
    except (AttributeError, IndexError, ValueError):
        return None


def _find_child(node, text):
    for child in node.get("Children", []):
        if child.get("Text") == text:
            return child
    return None


def _find_sensor_value(section_node, text, sensor_type):
    if section_node is None:
        return None

    for child in section_node.get("Children", []):
        if child.get("Text") == text and child.get("Type") == sensor_type:
            return child.get("Value")

    return None


def _find_hardware_node(node, name):
    if node.get("Text") == name:
        return node

    for child in node.get("Children", []):
        found = _find_hardware_node(child, name)

        if found is not None:
            return found

    return None


def _extract_stats(root):
    cpu_node = _find_hardware_node(root, CPU_HARDWARE_NAME)
    gpu_node = _find_hardware_node(root, GPU_HARDWARE_NAME)
    memory_node = _find_hardware_node(root, MEMORY_HARDWARE_NAME)

    if cpu_node is None or gpu_node is None or memory_node is None:
        return None

    cpu_percent = _parse_value(
        _find_sensor_value(_find_child(cpu_node, "Load"), "CPU Total", "Load")
    )
    cpu_temp_c = _parse_value(
        _find_sensor_value(_find_child(cpu_node, "Temperatures"), "Core (Tctl/Tdie)", "Temperature")
    )
    gpu_percent = _parse_value(
        _find_sensor_value(_find_child(gpu_node, "Load"), "GPU Core", "Load")
    )
    gpu_temp_c = _parse_value(
        _find_sensor_value(_find_child(gpu_node, "Temperatures"), "GPU Core", "Temperature")
    )
    ram_percent = _parse_value(
        _find_sensor_value(_find_child(memory_node, "Load"), "Memory", "Load")
    )

    if None in (cpu_percent, cpu_temp_c, gpu_percent, gpu_temp_c, ram_percent):
        return None

    return {
        "online": True,
        "cpu_percent": cpu_percent,
        "cpu_temp_c": cpu_temp_c,
        "gpu_percent": gpu_percent,
        "gpu_temp_c": gpu_temp_c,
        "ram_percent": ram_percent,
    }


def get_gaming_pc_stats():
    ip = load_gaming_pc_ip()

    if not ip:
        return {"online": False}

    try:
        response = requests.get(
            f"http://{ip}:{LHM_PORT}/data.json",
            timeout=LHM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        root = response.json()
    except (requests.RequestException, ValueError):
        return {"online": False}

    try:
        stats = _extract_stats(root)
    except AttributeError:
        return {"online": False}

    if stats is None:
        return {"online": False}

    return stats
