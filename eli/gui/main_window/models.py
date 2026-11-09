"""models — extracted from gui_eli_original.py (v3 modularisation)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403
from eli.gui.main_window._common import _eli_prompt_floor_tokens


class LocalModelManager:
    provider_name = "gguf"
    def __init__(self):
        self.model = None
        self.model_path = None
        self.is_loaded = False
        self.load_error = None
        self.fitted_gpu_layers = 0
        self.gpu_integrated = False
        self.gpu_name = ""
        self.gpu_vendor = ""
        self.n_ctx = self.n_threads = self.n_gpu_layers = self.n_batch = 0
        self.requested_n_gpu_layers = 0
        self.gpu_offload_supported = None
        self.experts_in_ram = None
        self.plan_text = ""
    def adopt(self, llm) -> None:
        """Take on the model gguf_inference loaded, with what it was asked for and loaded with."""
        from eli.cognition import gguf_inference as _gg
        report = dict(_gg.get_effective_runtime_report() or {})
        effective = dict(report.get("effective") or {})
        requested = dict(report.get("requested") or {})
        plan = dict(report.get("load_plan") or {})
        self.model = llm
        self.model_path = str(report.get("model_path") or self.model_path or "")
        self.n_ctx = int(effective.get("n_ctx") or 0)
        self.n_threads = int(effective.get("n_threads") or 0)
        self.n_gpu_layers = int(effective.get("n_gpu_layers") or 0)
        self.n_batch = int(effective.get("n_batch") or 0)
        self.fitted_gpu_layers = self.n_gpu_layers
        self.requested_n_gpu_layers = int(requested.get("n_gpu_layers") or 0)
        self.gpu_offload_supported = bool(report.get("gpu_offload_supported"))
        from eli.core.hardware_profile import _card_names
        used = [{"name": d.get("description") or d.get("name")}
                for d in (plan.get("devices") or []) if d.get("planned_mb")]
        self.gpu_name = _card_names(used) if used else ""
        self.experts_in_ram = report.get("moe_experts_in_ram_layers")
        self.plan_text = str(plan.get("text") or "")
        self.is_loaded = True
        self.load_error = None

    def status_line(self) -> str:
        """The status bar's ready line: the model and what it loaded with, where."""
        from eli.core.hardware_profile import describe_gpu_layers
        return (f"🟢 Model ready: {Path(self.model_path).name} (ctx={self.n_ctx} "
                f"gpu={describe_gpu_layers(self.n_gpu_layers)}"
                + (f" on {self.gpu_name}" if self.gpu_name and self.n_gpu_layers != 0 else "")
                + (f", experts of {self.experts_in_ram} layers in RAM" if self.experts_in_ram else "")
                + f" batch={self.n_batch})")

    def load_model(
        self,
        model_path: str,
        n_ctx: int = 0,
        n_threads: int = 0,
        n_gpu_layers: Optional[int] = None,
        n_batch: int = 512,
        cache_type_k: str = "",
        cache_type_v: str = "",
        use_mmap: bool = True,
        use_mlock: bool = False,
    ) -> bool:
        """Load through gguf_inference.load_model: the plan for this machine now (eli/core/load_plan.py),
        the window's boxes as the request. 0 context is auto; the layer count is a ceiling."""
        try:
            try:
                from eli.core.gpu_pack_runtime import try_activate_gpu_pack
                try_activate_gpu_pack(verify=True)
            except Exception:
                log.debug("GPU pack activation before model load skipped", exc_info=True)
            # The memory embedder loads first: the plan then sees the memory it takes.
            try:
                from eli.memory.vector_store import get_vector_store as _pre_vs
                _pre_vs()
            except Exception as _vs_err:
                print(f"   [PRELOAD] nomic embedder preload skipped: {_vs_err}")
            path_obj = resolve_model_path(model_path)
            self.model_path = str(path_obj)
            if not path_obj.exists():
                self.load_error = f"Model not found: {path_obj}"
                return False
            from eli.core.mem_units import file_size_gib
            print(f"🔄 Loading model: {path_obj.name}")
            print(f"   Size: {file_size_gib(path_obj):.2f} GB")
            _train = 0
            try:
                from eli.core.startup_hardware_optimizer import train_ctx_for_model
                _train = int(train_ctx_for_model(str(path_obj)) or 0)
                _floor = _eli_prompt_floor_tokens()
                if _train and _floor and _train < _floor:
                    log.warning(f"[LOAD_PLAN] model trained context {_train} is smaller than ELI's own "
                                f"prompt (~{_floor} tokens of persona and answer room): persona and "
                                f"memory will be cut. A model trained on a longer context avoids it.")
            except Exception:
                log.debug("[GUI] trained context check skipped", exc_info=True)
            from eli.cognition import gguf_inference as _gg
            request = _gg.load_request(
                path_obj,
                n_ctx=max(0, int(n_ctx or 0)),
                n_gpu_layers=None if n_gpu_layers is None else max(0, int(n_gpu_layers)),
                n_batch=max(1, int(n_batch or 512)),
                n_threads=max(0, int(n_threads or 0)),
                cache_type_k=str(cache_type_k or ""),
                cache_type_v=str(cache_type_v or ""),
                use_mmap=bool(use_mmap),
                use_mlock=bool(use_mlock),
            )
            self.model = None
            gc.collect()
            llm = _gg.load_model(force_reload=True, request=request)
            self.adopt(llm)
            # The reasoning-mode budgets were sized for the tuner's recommendation; size them for
            # the window the model can use: what loaded, up to what it was trained on (the
            # generation path sizes every prompt the same way). A load past the trained length
            # sized them for tokens the model is never given.
            _usable = int(_gg.current_context_limit() or 0) or int(self.n_ctx)
            if _train and int(self.n_ctx) > _train:
                log.warning(f"[LOAD_PLAN] {path_obj.name} loaded at ctx={self.n_ctx}, but it was trained "
                            f"on {_train} tokens: it reads at most {_train}, and the cache for the "
                            f"other {int(self.n_ctx) - _train} is memory it does not use. A context of "
                            f"{_train} or less loads only what it can use.")
            try:
                from eli.core.startup_hardware_optimizer import resize_budgets_to_effective_ctx
                _delta = resize_budgets_to_effective_ctx(_usable)
                if _delta:
                    log.debug(f"[LOAD_PLAN] mode budgets re-derived from usable ctx={_usable}: "
                              f"{sorted(_delta)}")
            except Exception as _budget_err:
                log.debug(f"[GUI][LOAD] mode budget re-derivation skipped: {_budget_err}")
            print(f"✅ Model loaded successfully: {self.plan_text}")
            return True
        except Exception as e:
            self.load_error = f"Failed to load model: {str(e)}"
            print(f"❌ {self.load_error}")
            self.model = None
            self.is_loaded = False
            gc.collect()
            return False

    # The window's own generations (proactive suggestions, screen text, the self-improvement plan)
    # run on the model gguf_inference loaded, so they go through gguf_inference: llama.cpp's one
    # lock (eli/runtime/native_locks.py), Stop, and the user's priority over background work hold
    # for them as for every other call. They called the Llama object directly under a lock of the
    # window's own, alongside the engine's and the embedder's calls on the same native model.
    @staticmethod
    def _call_args(messages) -> Dict[str, Any]:
        return {"prompt": messages} if isinstance(messages, str) else {"messages": list(messages or [])}

    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 2048,
             temperature: float = 0.7) -> str:
        if not self.is_loaded:
            return "❌ Model not loaded"
        try:
            from eli.cognition import gguf_inference as _gg
            return str(_gg.chat_completion(**self._call_args(messages), max_tokens=max_tokens,
                                           temperature=temperature) or "").strip()
        except Exception as e:
            return f"❌ Chat error: {str(e)}"
    def generate(self, prompt: str, max_tokens: int = 2048,
                temperature: float = 0.7) -> str:
        messages = [
            {'role': 'system', 'content': ELI_SYSTEM_PROMPT},
            {'role': 'user',   'content': prompt},
        ]
        return self.chat(messages, max_tokens=max_tokens, temperature=temperature)
    def chat_stream(self, messages, max_tokens=4096, temperature=0.7):
        if not self.is_loaded:
            yield "❌ Model not loaded"
            return
        try:
            from eli.cognition import gguf_inference as _gg
            for chunk in _gg.generate(**self._call_args(messages), max_tokens=max_tokens,
                                      temperature=temperature, stream=True):
                token = chunk.get("response", "") if isinstance(chunk, dict) else str(chunk or "")
                if token:
                    yield token
        except Exception as e:
            yield f"\n❌ Stream error: {e}"
    def unload(self):
        self.model = None
        self.is_loaded = False
        gc.collect()
        print("🔄 Model unloaded")


class OllamaModelManager:
    provider_name = "ollama"
    def __init__(self):
        self.host = "http://localhost:11434"
        self.model_name = ""
        self.is_loaded = False
        self.load_error = None
    def _normalize_host(self, host: str) -> str:
        """Delegate to the canonical normaliser so Settings behaves exactly like the
        startup picker, the wizard and the client. The local version only added a
        scheme — it left 'localhost' pointing at port 80, and had no IPv4 fallback."""
        try:
            from eli.integrations.ollama.client import normalise_host
            return normalise_host(host or self.host)
        except Exception:
            host = (host or "http://127.0.0.1:11434").strip().rstrip('/')
            if not host.startswith('http://') and not host.startswith('https://'):
                host = 'http://' + host
            return host
    def _candidates(self, host: Optional[str] = None) -> List[str]:
        try:
            from eli.integrations.ollama.client import candidate_hosts
            return candidate_hosts(host or self.host)
        except Exception:
            return [self._normalize_host(host or self.host)]
    def list_models(self, host: Optional[str] = None) -> List[str]:
        last = None
        # Listing never starts Ollama (it started `ollama serve` at every launch with a GGUF model
        # in use); the client starts it when a reply is asked of an Ollama model.
        for base in self._candidates(host):
            try:
                from eli.integrations.ollama.client import _allow_via_netguard
                _allow_via_netguard(base)
            except Exception:
                log.debug("[OLLAMA] netguard registration skipped", exc_info=True)
            req = urllib.request.Request(base + '/api/tags',
                                         headers={'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode('utf-8', errors='replace'))
                return [m.get('name', '') for m in data.get('models', []) if m.get('name')]
            except Exception as exc:
                last = exc
        raise last if last else RuntimeError("no Ollama host to query")
    def load_model(self, host: str, model_name: str) -> bool:
        try:
            self.host = self._normalize_host(host)
            self.model_name = (model_name or '').strip()
            if not self.model_name:
                self.load_error = 'No Ollama model selected.'
                self.is_loaded = False
                return False
            models = self.list_models(self.host)
            if models and self.model_name not in models:
                log.debug(f"[OLLAMA] model not in /api/tags yet, continuing anyway: {self.model_name}")
            self.load_error = None
            self.is_loaded = True
            return True
        except Exception as e:
            self.load_error = f"Failed to connect to Ollama: {e}"
            self.is_loaded = False
            return False
    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 2048,
             temperature: float = 0.7) -> str:
        if not self.is_loaded:
            return "❌ Ollama model not loaded"
        url = self.host + '/api/chat'
        payload = {
            'model': self.model_name,
            'messages': messages,
            'stream': False,
            'options': {
                'temperature': temperature,
                'num_predict': max_tokens,
            }
        }
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
        msg = data.get('message') or {}
        return (msg.get('content') or '').strip()
    def generate(self, prompt: str, max_tokens: int = 2048,
                temperature: float = 0.7) -> str:
        messages = [
            {'role': 'system', 'content': ELI_SYSTEM_PROMPT},
            {'role': 'user',   'content': prompt},
        ]
        return self.chat(messages, max_tokens=max_tokens, temperature=temperature)
    def chat_stream(self, messages, max_tokens=4096, temperature=0.7):
        if not self.is_loaded:
            yield "❌ Ollama not loaded"
            return
        import json, urllib.request
        url = self.host + '/api/chat'
        payload = {
            'model': self.model_name,
            'messages': messages,
            'stream': True,
            'options': {'temperature': temperature, 'num_predict': max_tokens},
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for line in resp:
                    line = line.decode('utf-8', errors='replace').strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line)
                        token = (j.get('message') or {}).get('content', '')
                        if token:
                            yield token
                        if j.get('done'):
                            break
                    except Exception:
                        continue
        except Exception as e:
            yield f"\n❌ Ollama stream error: {e}"
    def unload(self):
        self.is_loaded = False
