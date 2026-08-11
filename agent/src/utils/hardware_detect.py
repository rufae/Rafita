"""Auto-detection of hardware capabilities and recommended model profiles.

Detects GPU availability, total RAM, and returns the optimal model
configuration for the current hardware without manual .env editing.
"""

import subprocess
import sys
from dataclasses import dataclass


@dataclass
class HardwareProfile:
    has_gpu: bool
    gpu_name: str
    gpu_vram_gb: float
    total_ram_gb: float
    cpu_cores: int


@dataclass
class ModelProfile:
    chat_model: str
    embedding_model: str
    embedding_dim: int
    vision_model: str
    label: str


def detect_hardware() -> HardwareProfile:
    """Detect available hardware resources."""
    has_gpu = False
    gpu_name = "none"
    gpu_vram_gb = 0.0

    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                gpu_name = parts[0].strip()
                gpu_vram_gb = float(parts[1].strip()) / 1024.0
                has_gpu = True
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
            pass
    else:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                gpu_name = parts[0].strip()
                gpu_vram_gb = float(parts[1].strip()) / 1024.0
                has_gpu = True
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
            pass

    # Fallback: check for Vulkan/OpenCL devices
    if not has_gpu:
        try:
            result = subprocess.run(
                ["lspci"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and any(
                tag in result.stdout.lower() for tag in ["vga", "3d", "display"]
            ):
                has_gpu = True
                gpu_name = "unknown (PCI device detected)"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    total_ram_gb = 0.0
    try:
        total_ram_gb = _get_total_ram_gb()
    except Exception:
        total_ram_gb = 8.0  # conservative fallback

    cpu_cores = _get_cpu_cores()

    return HardwareProfile(
        has_gpu=has_gpu,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram_gb,
        total_ram_gb=total_ram_gb,
        cpu_cores=cpu_cores,
    )


def _get_total_ram_gb() -> float:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["wmic", "computersystem", "get", "totalphysicalmemory"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    return float(line) / (1024**3)
        except Exception:
            pass
        return 8.0

    try:
        import os as _os
        mem_bytes = _os.sysconf("SC_PAGE_SIZE") * _os.sysconf("SC_PHYS_PAGES")
        return mem_bytes / (1024**3)
    except Exception:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return float(line.split()[1]) / (1024 * 1024)
        except Exception:
            pass
    return 8.0


def _get_cpu_cores() -> int:
    try:
        return (importlib_available() and __import__("os").cpu_count()) or 4
    except Exception:
        return 4


def importlib_available() -> bool:
    return True


def recommend_model_profile(hw: HardwareProfile | None = None) -> ModelProfile:
    """Return the recommended model profile for the detected hardware."""
    if hw is None:
        hw = detect_hardware()

    if hw.has_gpu and hw.gpu_vram_gb >= 10:
        return ModelProfile(
            chat_model="gemma4:12b",
            embedding_model="bge-m3",
            embedding_dim=1024,
            vision_model="llava:7b",
            label="gpu-high",
        )

    if hw.has_gpu and hw.gpu_vram_gb >= 4:
        return ModelProfile(
            chat_model="qwen2.5:7b",
            embedding_model="bge-m3",
            embedding_dim=1024,
            vision_model="llava:7b",
            label="gpu-mid",
        )

    if hw.total_ram_gb >= 32:
        return ModelProfile(
            chat_model="qwen2.5:14b",
            embedding_model="bge-m3",
            embedding_dim=1024,
            vision_model="llava:7b",
            label="cpu-high",
        )

    if hw.total_ram_gb >= 16:
        return ModelProfile(
            chat_model="qwen2.5:7b",
            embedding_model="bge-m3",
            embedding_dim=1024,
            vision_model="llava:7b",
            label="cpu-mid",
        )

    return ModelProfile(
        chat_model="qwen2.5:3b",
        embedding_model="nomic-embed-text",
        embedding_dim=768,
        vision_model="moondream:latest",
        label="cpu-low",
    )


def detect_and_log() -> HardwareProfile:
    """Detect hardware and log the profile for startup diagnostics."""
    from src.logger import logger

    hw = detect_hardware()
    profile = recommend_model_profile(hw)

    logger.info(
        "Hardware detected: GPU=%s (%.1fGB VRAM), RAM=%.1fGB, CPU=%d cores",
        hw.gpu_name, hw.gpu_vram_gb, hw.total_ram_gb, hw.cpu_cores,
    )
    logger.info(
        "Recommended profile: %s (chat=%s, embed=%s/%dd, vision=%s)",
        profile.label, profile.chat_model,
        profile.embedding_model, profile.embedding_dim,
        profile.vision_model,
    )
    return hw
