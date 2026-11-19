import os
import sqlite3

from eli.kernel.pipeline import get_pipeline_description
from eli.core.paths import get_paths

from eli.core.paths import path_get as _eli_path_get


class IntrospectionAgent:
    def get_pipeline(self) -> str:
        return "\n".join(get_pipeline_description())

    def get_memory_stats(self) -> str:
        paths = get_paths()
        user_db = paths.user_db
        memory_db = _eli_path_get(paths, "memory_db")

        turn_count = 0
        mem_count = 0

        if user_db.exists():
            conn = sqlite3.connect(user_db)
            try:
                cur = conn.execute("SELECT COUNT(*) FROM conversation_turns")
                row = cur.fetchone()
                turn_count = row[0] if row else 0
            finally:
                conn.close()

        if memory_db.exists():
            conn = sqlite3.connect(memory_db)
            try:
                cur = conn.execute("SELECT COUNT(*) FROM memories")
                row = cur.fetchone()
                mem_count = row[0] if row else 0
            finally:
                conn.close()

        return f"Total conversation turns: {turn_count}\nTotal stored memories: {mem_count}"

    def get_runtime(self) -> str:
        # what is loaded now when this process holds it, else what the loader last wrote to disk
        from eli.cognition.context_synthesiser import _runtime_snapshot
        snapshot = _runtime_snapshot()
        settings = {}

        try:
            from eli.core import runtime_settings as _rs
            settings = _rs.load_settings() or {}
        except Exception:
            settings = {}

        def _pick(*values):
            for v in values:
                if v not in (None, "", [], {}, ()):
                    return v
            return "unknown"

        provider = _pick(
            snapshot.get("provider"),
            settings.get("provider"),
            os.environ.get("ELI_PROVIDER"),
        )
        model = _pick(
            snapshot.get("model_name"),
            settings.get("selected_model"),
            os.environ.get("ELI_GGUF_MODEL"),
            os.environ.get("ELI_MODEL"),
        )
        model_path = _pick(
            snapshot.get("model_path"),
            settings.get("gguf_model_path"),
            settings.get("model_path"),
            settings.get("custom_model_path"),
            settings.get("bundled_model_path"),
        )
        ctx = _pick(
            snapshot.get("n_ctx"),
            settings.get("n_ctx"),
            os.environ.get("ELI_GGUF_N_CTX"),
            os.environ.get("ELI_CONTEXT"),
        )
        gpu = _pick(
            snapshot.get("n_gpu_layers"),
            settings.get("n_gpu_layers"),
            settings.get("gpu_layers"),
            os.environ.get("ELI_GGUF_N_GPU_LAYERS"),
            os.environ.get("ELI_GPU_LAYERS"),
        )
        batch = _pick(
            snapshot.get("n_batch"),
            settings.get("batch_size"),
            os.environ.get("ELI_GGUF_N_BATCH"),
            os.environ.get("ELI_BATCH"),
        )
        threads = _pick(
            snapshot.get("n_threads"),
            settings.get("n_threads"),
            settings.get("cpu_threads"),
            os.environ.get("ELI_GGUF_THREADS"),
            os.environ.get("ELI_THREADS"),
        )
        loaded = snapshot.get("loaded")

        lines = [
            f"Provider: {provider}",
            f"Model: {model}",
            f"Context size: {ctx}",
            f"GPU-layer parameter: {gpu}",
            f"Batch size: {batch}",
            f"Threads: {threads}",
        ]
        if model_path != "unknown":
            lines.append(f"Model path: {model_path}")
        if loaded is not None:
            lines.append(f"Loaded in process: {loaded}")

        return "\n".join(lines)

