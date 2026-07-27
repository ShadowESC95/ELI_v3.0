"""main_window — the decomposed gui_eli_original.py monolith."""
from __future__ import annotations
from eli.gui.main_window.adapters import CentralMemoryAdapter, ExecutorBridge, _GUIEngineAdapter
from eli.gui.main_window.models import LocalModelManager, OllamaModelManager
from eli.gui.main_window.widgets import (_FlowLayout, _CapabilityList, _QABoard, _ZoomableSettingsView, _MiniTelemetryGraph, _ZoomableImagePreview, _GlobalScrollZoom)
from eli.gui.main_window.window import EliMainWindow, main
from eli.gui.main_window._common import (APP_VERSION, _eli_path_get, _eli_gui_visible_text, _eli_app_version, ensure_dirs, now_timestamp, now_hms, format_gb, classify_gguf_source, recommend_model_setup, discover_gguf_models, detect_system_capabilities, recommend_optimal_settings, resolve_model_path, _sanitize_identity_drift, _policy_identity_memory_response)
__all__ = ['APP_VERSION', 'CentralMemoryAdapter', 'EliMainWindow', 'ExecutorBridge', 'LocalModelManager', 'OllamaModelManager', '_CapabilityList', '_FlowLayout', '_GUIEngineAdapter', '_GlobalScrollZoom', '_MiniTelemetryGraph', '_QABoard', '_ZoomableImagePreview', '_ZoomableSettingsView', '_eli_app_version', '_eli_gui_visible_text', '_eli_path_get', '_policy_identity_memory_response', '_sanitize_identity_drift', 'classify_gguf_source', 'detect_system_capabilities', 'discover_gguf_models', 'ensure_dirs', 'format_gb', 'main', 'now_hms', 'now_timestamp', 'recommend_model_setup', 'recommend_optimal_settings', 'resolve_model_path']
