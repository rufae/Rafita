"""Tests for hardware detection and model profile selection."""

from unittest.mock import MagicMock, patch

from src.utils import hardware_detect as hw_mod
from src.utils.hardware_detect import (
    detect_and_log,
    detect_hardware,
    recommend_model_profile,
)


class TestHardwareDetection:
    """Test hardware detection logic."""

    def test_detect_rtx_3060_profile(self):
        """RTX 3060 (12GB VRAM) should select gpu-high profile with gemma4:12b."""
        # Mock nvidia-smi output for RTX 3060
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA GeForce RTX 3060, 12288\n"

        with patch("subprocess.run", return_value=mock_result):
            hw = detect_hardware()
            profile = recommend_model_profile(hw)

        assert hw.has_gpu is True
        assert hw.gpu_vram_gb >= 10.0
        assert profile.label == "gpu-high"
        assert profile.chat_model == "gemma4:12b"
        assert profile.embedding_model == "bge-m3"
        assert profile.embedding_dim == 1024

    def test_detect_gtx_1660_profile(self):
        """GTX 1660 (6GB VRAM) should select gpu-mid profile with qwen2.5:7b."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA GeForce GTX 1660, 6144\n"

        with patch("subprocess.run", return_value=mock_result):
            hw = detect_hardware()
            profile = recommend_model_profile(hw)

        assert hw.has_gpu is True
        assert 4.0 <= hw.gpu_vram_gb < 10.0
        assert profile.label == "gpu-mid"
        assert profile.chat_model == "qwen2.5:7b"
        assert profile.embedding_model == "bge-m3"

    def test_detect_no_gpu_32gb_ram(self):
        """No GPU with 32GB RAM should select cpu-high profile."""
        mock_result = MagicMock()
        mock_result.returncode = 1  # nvidia-smi fails
        mock_result.stdout = ""

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("src.utils.hardware_detect._get_total_ram_gb", return_value=32.0),
            patch("src.utils.hardware_detect._get_cpu_cores", return_value=8),
        ):
            hw = detect_hardware()
            profile = recommend_model_profile(hw)

        assert hw.has_gpu is False
        assert hw.total_ram_gb >= 32.0
        assert profile.label == "cpu-high"
        assert profile.chat_model == "qwen2.5:14b"

    def test_detect_no_gpu_16gb_ram(self):
        """No GPU with 16GB RAM should select cpu-mid profile."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("src.utils.hardware_detect._get_total_ram_gb", return_value=16.0),
            patch("src.utils.hardware_detect._get_cpu_cores", return_value=4),
        ):
            hw = detect_hardware()
            profile = recommend_model_profile(hw)

        assert hw.has_gpu is False
        assert 16.0 <= hw.total_ram_gb < 32.0
        assert profile.label == "cpu-mid"
        assert profile.chat_model == "qwen2.5:7b"

    def test_detect_no_gpu_8gb_ram(self):
        """No GPU with 8GB RAM should select cpu-low profile."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with (
            patch("subprocess.run", return_value=mock_result),
            patch("src.utils.hardware_detect._get_total_ram_gb", return_value=8.0),
            patch("src.utils.hardware_detect._get_cpu_cores", return_value=4),
        ):
            hw = detect_hardware()
            profile = recommend_model_profile(hw)

        assert hw.has_gpu is False
        assert hw.total_ram_gb < 16.0
        assert profile.label == "cpu-low"
        assert profile.chat_model == "qwen2.5:3b"
        assert profile.embedding_model == "nomic-embed-text"
        assert profile.embedding_dim == 768

    def test_rtx_3060_vram_calculation(self):
        """Verify VRAM calculation from nvidia-smi output."""
        # RTX 3060 reports 12288 MiB
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA GeForce RTX 3060, 12288\n"

        with patch("subprocess.run", return_value=mock_result):
            hw = detect_hardware()

        # 12288 MiB = 12.0 GB
        assert abs(hw.gpu_vram_gb - 12.0) < 0.1
        assert hw.gpu_name == "NVIDIA GeForce RTX 3060"

    def test_rtx_4090_profile(self):
        """RTX 4090 (24GB VRAM) should select gpu-high profile."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA GeForce RTX 4090, 24576\n"

        with patch("subprocess.run", return_value=mock_result):
            hw = detect_hardware()
            profile = recommend_model_profile(hw)

        assert hw.has_gpu is True
        assert hw.gpu_vram_gb >= 10.0
        assert profile.label == "gpu-high"
        assert profile.chat_model == "gemma4:12b"


# ---------- ramas win32, fallbacks y utilidades (cobertura 2026-10-06) ----------


def _resultado(code, out=""):
    r = MagicMock()
    r.returncode = code
    r.stdout = out
    return r


def test_detect_win32_con_gpu_y_ram_por_wmic():
    """Rama win32: nvidia-smi (37-48) + wmic con cifras (103-113)."""
    nvidia = _resultado(0, "NVIDIA GeForce RTX 3060, 12288\n")
    wmic = _resultado(0, "TotalPhysicalMemory\n34359738368\n")

    def _run(cmd, **kwargs):
        return wmic if cmd[0] == "wmic" else nvidia

    with patch("sys.platform", "win32"), patch("subprocess.run", side_effect=_run):
        hw = detect_hardware()
        ram = hw_mod._get_total_ram_gb()

    assert hw.has_gpu is True
    assert abs(hw.gpu_vram_gb - 12.0) < 0.1
    assert abs(ram - 32.0) < 0.1


def test_detect_win32_sin_nvidia_y_wmic_sin_cifras():
    """nvidia-smi inexistente (49-50), lspci (79-80) y wmic sin digito (116)."""

    def _run(cmd, **kwargs):
        if cmd[0] == "nvidia-smi":
            raise FileNotFoundError("nvidia-smi")
        if cmd[0] == "lspci":
            return _resultado(0, "00:02.0 VGA compatible controller: Intel UHD")
        return _resultado(0, "TotalPhysicalMemory\nno-number\n")

    with patch("sys.platform", "win32"), patch("subprocess.run", side_effect=_run):
        hw = detect_hardware()
        ram = hw_mod._get_total_ram_gb()

    assert hw.has_gpu is True and "PCI" in hw.gpu_name
    assert ram == 8.0


def test_detect_linux_sin_nvidia_ni_lspci():
    """Excepciones de subprocess en ambas ramas (64-65 y 81-82)."""

    def _run(cmd, **kwargs):
        raise FileNotFoundError("sin binario")

    with patch("subprocess.run", side_effect=_run):
        hw = detect_hardware()

    assert hw.has_gpu is False


def test_detect_linux_fallback_lspci_detecta_vga():
    """nvidia-smi devuelve error -> lspci detecta VGA (79-80)."""
    nvidia = _resultado(1, "")
    lspci = _resultado(0, "00:02.0 VGA compatible controller: Intel Corporation")

    def _run(cmd, **kwargs):
        return lspci if cmd[0] == "lspci" else nvidia

    with patch("subprocess.run", side_effect=_run):
        hw = detect_hardware()

    assert hw.has_gpu is True
    assert hw.gpu_name == "unknown (PCI device detected)"


def test_detect_ram_fallida_usa_fallback():
    with (
        patch("src.utils.hardware_detect._get_total_ram_gb", side_effect=RuntimeError("boom")),
        patch("subprocess.run", return_value=_resultado(1, "")),
    ):
        hw = detect_hardware()

    assert hw.total_ram_gb == 8.0  # 87-88


def test_get_ram_fallback_meminfo():
    with patch("os.sysconf", side_effect=ValueError("sin sysconf")):
        ram = hw_mod._get_total_ram_gb()

    assert ram > 0  # 123-128: /proc/meminfo


def test_get_ram_fallback_total_a_8gb():
    with (
        patch("os.sysconf", side_effect=ValueError("sin sysconf")),
        patch("builtins.open", side_effect=OSError("sin /proc")),
    ):
        ram = hw_mod._get_total_ram_gb()

    assert ram == 8.0  # 129-131


def test_cpu_cores_fallback_excepcion():
    with patch("os.cpu_count", side_effect=RuntimeError("boom")):
        assert hw_mod._get_cpu_cores() == 4  # 137-138


def test_recommend_profile_detecta_si_no_recibe_hw():
    hw_falso = MagicMock(has_gpu=False, gpu_vram_gb=0.0, total_ram_gb=8.0, cpu_cores=4)
    with patch("src.utils.hardware_detect.detect_hardware", return_value=hw_falso) as fake:
        profile = recommend_model_profile()

    assert profile.label == "cpu-low"
    fake.assert_called_once()  # 148


def test_detect_and_log_devuelve_el_perfil():
    hw_falso = hw_mod.HardwareProfile(
        has_gpu=True,
        gpu_name="RTX 3060",
        gpu_vram_gb=12.0,
        total_ram_gb=32.0,
        cpu_cores=8,
    )
    with patch("src.utils.hardware_detect.detect_hardware", return_value=hw_falso):
        salida = detect_and_log()

    assert salida is hw_falso  # 197-217: loguea y devuelve
