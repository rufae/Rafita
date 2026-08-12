"""Tests for hardware detection and model profile selection."""
import pytest
from unittest.mock import patch, MagicMock
from src.utils.hardware_detect import (
    detect_hardware,
    recommend_model_profile,
    HardwareProfile,
    ModelProfile,
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
        
        with patch("subprocess.run", return_value=mock_result), \
             patch("src.utils.hardware_detect._get_total_ram_gb", return_value=32.0), \
             patch("src.utils.hardware_detect._get_cpu_cores", return_value=8):
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
        
        with patch("subprocess.run", return_value=mock_result), \
             patch("src.utils.hardware_detect._get_total_ram_gb", return_value=16.0), \
             patch("src.utils.hardware_detect._get_cpu_cores", return_value=4):
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
        
        with patch("subprocess.run", return_value=mock_result), \
             patch("src.utils.hardware_detect._get_total_ram_gb", return_value=8.0), \
             patch("src.utils.hardware_detect._get_cpu_cores", return_value=4):
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
