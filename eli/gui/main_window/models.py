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
    def _write_shared_runtime_snapshot(
        self,
        model_path: str,
        n_ctx: int,
        n_threads: int,
        n_gpu_layers: int,
        *,
        requested_n_gpu_layers: Optional[int] = None,
        requested_n_ctx: Optional[int] = None,
        requested_n_batch: Optional[int] = None,
        gpu_offload_supported: Optional[bool] = None,
        vram_free_mb_at_load: Optional[int] = None,
        vram_held_by_others: str = "",
    ):
        try:
            from eli.core.paths import get_paths
            snap_path = Path(get_paths().artifacts_dir) / 'runtime_snapshot.json'
            effective_n_gpu_layers = int(n_gpu_layers or 0)
            effective_n_ctx = int(n_ctx or 0)
            effective_n_batch = int(getattr(self, 'n_batch', 0) or 0)
            requested_layers = int(
                requested_n_gpu_layers
                if requested_n_gpu_layers is not None
                else effective_n_gpu_layers
            )
            # ctx/batch requested values are the operator's true ask, not what the fallback loaded;
            # default to effective only when the caller has nothing else.
            requested_ctx = int(
                requested_n_ctx if requested_n_ctx is not None else effective_n_ctx
            )
            requested_batch = int(
                requested_n_batch if requested_n_batch is not None else effective_n_batch
            )
            payload = {
                'provider': 'gguf',
                'model_path': str(model_path),
                'model_name': Path(model_path).name if model_path else '',
                'n_ctx': effective_n_ctx,
                'n_gpu_layers': effective_n_gpu_layers,
                'n_threads': int(n_threads or 0),
                'n_batch': effective_n_batch,
                'requested_n_gpu_layers': requested_layers,
                'gpu_offload_supported': gpu_offload_supported,
                'load_mode': 'GPU' if effective_n_gpu_layers > 0 else 'CPU',
                'requested': {
                    'n_ctx': requested_ctx,
                    'n_gpu_layers': requested_layers,
                    'n_threads': int(n_threads or 0),
                    'n_batch': requested_batch,
                },
                'effective': {
                    'n_ctx': effective_n_ctx,
                    'n_gpu_layers': effective_n_gpu_layers,
                    'n_threads': int(n_threads or 0),
                    'n_batch': effective_n_batch,
                },
                'loaded': bool(getattr(self, 'is_loaded', False)),
                # what the card looked like at load: why a load came out smaller than asked
                'vram_free_mb_at_load': vram_free_mb_at_load,
                'vram_held_by_others': vram_held_by_others,
                'pid': __import__('os').getpid(),
                'ts': time.time(),
            }
            snap_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
            print(f'✅ shared runtime snapshot written: {snap_path}')
        except Exception as e:
            log.debug(f'[GUI] shared runtime snapshot write failed: {e}')

    def load_model(
        self,
        model_path: str,
        n_ctx: int = 16384,
        n_threads: int = 8,
        n_gpu_layers: int = 0,
        n_batch: int = 512,
        cache_type_k: str = "",
        cache_type_v: str = "",
        use_mmap: bool = True,
        use_mlock: bool = False,
    ) -> bool:
        try:
            _pre_load_rss = 0
            try:
                from eli.runtime.inference_footprint import capture_process_rss_bytes
                _pre_load_rss = int(capture_process_rss_bytes())
            except Exception:
                log.debug("[GUI][LOAD] pre-load RSS capture skipped", exc_info=True)
            try:
                from eli.core.gpu_pack_runtime import try_activate_gpu_pack
                try_activate_gpu_pack(verify=True)
            except Exception:
                log.debug("GPU pack activation before model load skipped", exc_info=True)
            try:
                from eli.core.llama_cpu_compat import preflight_runtime
                _cpu_msg = preflight_runtime()
            except Exception:
                _cpu_msg = None
            if _cpu_msg:
                print(f"❌ {_cpu_msg}")
                return False
            from llama_cpp import Llama
            from llama_cpp import llama_cpp as _llama_native
            # Co-resident vision: reserve the small fast (Moondream) model's VRAM
            # FIRST, then cap this text model's context so both fit in 8GB.
            # Best-effort and fully guarded — if anything fails, the text model
            # loads normally at full context (boot is never at risk).
            try:
                from eli.core import config as _co_cfg
                if bool(_co_cfg.get("vision_coresident", False)):
                    from eli.perception import vision as _co_vision
                    _rok, _rreason = _co_vision.load_resident_fast_model()
                    if _rok:
                        # Vision is resident. NO static ctx cap — the smart
                        # loader below sizes the text model dynamically to the
                        # VRAM actually left (ctx = model train length × the
                        # user's fraction, fitted by reducing layers→batch→ctx).
                        print("   [CO-RESIDENT] vision model resident (text ctx sized dynamically)")
                    else:
                        print(f"   [CO-RESIDENT] fast model not loaded ({_rreason})")
            except Exception as _co_err:
                print(f"   [CO-RESIDENT] setup skipped: {_co_err}")
            # Preload the nomic embedder too, before the main model's VRAM
            # budget is taken — required model, resident first. (Currently CPU /
            # ~0 VRAM, but if it ever moves to GPU the budget will see it.)
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
            self.fitted_gpu_layers = 0
            try:
                from eli.core.hardware_profile import detect_hardware as _hw_load
                _hw = _hw_load()
                self.gpu_integrated = bool(getattr(_hw, "gpu_integrated", False))
                self.gpu_name = str(getattr(_hw, "gpu_name", "") or "")
                self.gpu_vendor = str(getattr(_hw, "gpu_vendor", "") or "")
                self.vulkan_available = bool(getattr(_hw, "vulkan_available", False))
            except Exception:
                self.gpu_integrated = False
                self.gpu_name = ""
                self.gpu_vendor = ""
                self.vulkan_available = False
            # Preserve user's explicit settings — attempt 1 always honours these exactly.
            # The hardware profile is read separately and inserted as a fallback candidate
            # so it never overrides deliberate user choices.
            _user_ctx = int(n_ctx)
            # 0 is auto: sized below for this model on this machine; no request of the operator's to honour
            _ctx_is_auto = _user_ctx <= 0
            _user_gpu_layers = int(n_gpu_layers)
            _user_batch = int(n_batch)
            _hw_profile_ctx = None
            _hw_profile_gpu_layers = None
            _hw_profile_batch = None
            try:
                import json as _eli_hw_json
                from pathlib import Path as _EliHwPath
                from eli.core.paths import get_paths as _eli_get_paths
                _eli_profile_path = _EliHwPath(_eli_get_paths().artifacts_dir) / "runtime_hardware_profile.json"
                if _eli_profile_path.exists():
                    _eli_profile = _eli_hw_json.loads(_eli_profile_path.read_text(encoding="utf-8"))
                    _hw_profile_ctx = int(_eli_profile.get("n_ctx") or _user_ctx)
                    _hw_profile_gpu_layers = int(_eli_profile.get("n_gpu_layers", _user_gpu_layers))
                    _hw_profile_batch = int(_eli_profile.get("batch_size", _user_batch))
                    log.debug(
                        f"[GUI][HW_PROFILE] startup profile read: "
                        f"ctx={_hw_profile_ctx} gpu_layers={_hw_profile_gpu_layers} "
                        f"batch={_hw_profile_batch} "
                        f"(user: ctx={_user_ctx} gpu_layers={_user_gpu_layers} "
                        f"batch={_user_batch})"
                    )
                    if int(_hw_profile_gpu_layers or 0) > 0:
                        self.fitted_gpu_layers = int(_hw_profile_gpu_layers)
            except Exception as _eli_hw_err:
                log.debug(f"[GUI][HW_PROFILE] profile read failed: {_eli_hw_err}")

            requested_n_gpu_layers = int(n_gpu_layers)
            gpu_offload_supported = None
            try:
                _supports_fn = getattr(_llama_native, "llama_supports_gpu_offload", None)
                if callable(_supports_fn):
                    gpu_offload_supported = bool(_supports_fn())
            except Exception:
                gpu_offload_supported = None
            # Vulkan packs on Intel iGPU often report offload=False inside
            # AppImage even when the pack was verified — don't force CPU.
            if gpu_offload_supported is False:
                try:
                    from eli.core.gpu_pack_runtime import trust_vulkan_igpu_offload
                    if trust_vulkan_igpu_offload():
                        gpu_offload_supported = True
                        log.debug("[GUI][GPU] trusting Vulkan iGPU pack despite offload flag=False")
                except Exception:
                    log.debug("vulkan igpu offload trust probe failed", exc_info=True)
            effective_n_gpu_layers = int(n_gpu_layers)
            if requested_n_gpu_layers > 0 and gpu_offload_supported is False:
                log.debug(
                    "[GUI][GPU] GPU offload unsupported by current runtime "
                    "(driver/CUDA/backend unavailable). Forcing CPU mode.",
                )
                effective_n_gpu_layers = 0
            effective_n_batch = int(n_batch)
            if int(effective_n_gpu_layers) <= 0 and int(effective_n_batch) > 128:
                log.debug(
                    f"[GUI][CPU] Clamping batch size {effective_n_batch} -> 128 for CPU-only mode.",
                )
                effective_n_batch = 128
            log.debug(
                f"[GUI][GPU] requested_layers={requested_n_gpu_layers} "
                f"effective_layers={effective_n_gpu_layers} "
                f"offload_supported={gpu_offload_supported}",
            )
            # This dialog builds its own Llama(...) below instead of going through
            # gguf_inference.load_model() — the MoE plan has to be computed here too, or a
            # mixture-of-experts model loaded from the startup dialog never gets it.
            _moe_gui = None
            if effective_n_gpu_layers > 0:
                try:
                    from eli.core import moe_offload as _moe_offload_gui
                    _moe_gui = _moe_offload_gui.plan_for_load(str(path_obj), gpu_offload_supported)
                except Exception:
                    log.debug("[GUI] MoE plan lookup failed", exc_info=True)
            if _moe_gui:
                print(f"   GPU-layer load parameter: {effective_n_gpu_layers} "
                      f"(mixture-of-experts model — real GPU-resident layer fit measured below; "
                      f"~{_moe_gui['experts_gb']}GB of experts always stay in RAM)")
            else:
                print(f"   GPU-layer load parameter: {effective_n_gpu_layers}")
            if (
                requested_n_gpu_layers > 0
                and effective_n_gpu_layers == 0
                and gpu_offload_supported is False
            ):
                print(
                    f"   (hardware profile suggested {requested_n_gpu_layers} layers — "
                    f"this runtime is CPU-only; 0 active)",
                )
            print(f"   Batch size: {effective_n_batch}")
            # the cache type that loads for what is stored: the fits below and llama.cpp get this
            from eli.core.gguf_sizes import cache_setting as _cache_setting
            cache_type_k, cache_type_v = _cache_setting(cache_type_k), _cache_setting(cache_type_v)
            if cache_type_k or cache_type_v:
                print(f"   KV cache: K={cache_type_k or 'default'} V={cache_type_v or 'default'}")
            self.n_ctx = int(n_ctx)
            self.n_threads = int(n_threads)
            self.n_gpu_layers = int(effective_n_gpu_layers)
            self.n_batch = int(effective_n_batch)
            self.requested_n_gpu_layers = int(requested_n_gpu_layers)
            self.gpu_offload_supported = gpu_offload_supported
            _seen: set[Tuple[int, int, int]] = set()
            _attempts: List[Dict[str, Any]] = []

            def _add_attempt(label: str, ctx: int, layers: int, batch: int,
                             front: bool = False, verify: bool = False):
                _ctx = max(1024, int(ctx))
                _layers = max(0, int(layers))
                _batch = max(32, int(batch))
                if gpu_offload_supported is False:
                    _layers = 0
                key = (_ctx, _layers, _batch)
                if key in _seen:
                    return
                _seen.add(key)
                # `verify` marks a candidate that must be PROVEN before it is
                # loaded in this process, because a lazy CUDA allocation makes a
                # bad one look like a success and then aborts mid-generation.
                # Only the operator's own settings carry it: ELI's calculated
                # rungs are derived from measured free VRAM and are the fallback
                # the proof falls back TO.
                _entry = {
                    "label": str(label),
                    "n_ctx": _ctx,
                    "n_gpu_layers": _layers,
                    "n_batch": _batch,
                    "verify": bool(verify),
                }
                if front:
                    _attempts.insert(0, _entry)
                else:
                    _attempts.append(_entry)

            _base_ctx = _user_ctx
            _base_layers = int(effective_n_gpu_layers)
            _base_batch = int(effective_n_batch)
            _sf_ctx = None
            # The user's OWN settings are queued FIRST — but below, once the fit
            # has measured free VRAM, because the layer count has to be clamped
            # against it. See the note at the insertion point.
            # Fit numbers also feed the verify gate: lazy CUDA aborts when the
            # operator's ctx/batch/layers exceed what was measured (live: 30B
            # Nemotron with layers==fit but ctx 12000>>4096 skipped the probe).
            _sf_fit_layers = None
            _sf_fit_ctx = None
            _sf_fit_batch = None
            _sf_user_ctx = 0
            _sf_free_mb = None
            _sf_total_mb = None
            _load_vram_held = ""

            # ── Smart loader (fit-to-hardware fallback) ───────────────────
            # Anchor on the user's preferred ctx/batch, then fit into the VRAM
            # actually free NOW (vision + nomic already resident), reducing GPU
            # LAYERS first (in increments), then batch, then ctx — quality over
            # speed. Fully dynamic from live free VRAM; no static profile.
            # Guarded — if it fails, the user/profile/fallback attempts run.
            import os as _sf_os
            try:
                from eli.core.startup_hardware_optimizer import (
                    gpu_budget_for_fit as _sf_budget,
                    train_ctx_for_model as _sf_train_ctx,
                    detect_available_ram_gb as _sf_avail_ram,
                    cpu_ctx_ceiling_from_ram as _sf_ram_ceil,
                )
                from eli.core.hardware_profile import unified_fit_config as _sf_fit
                from eli.core.mem_units import file_size_gib as _sf_file_gib
                _sf_model_gb = _sf_file_gib(path_obj)
                _sf_train = int(_sf_train_ctx(str(path_obj)))
                _sf_igpu = bool(getattr(self, "gpu_integrated", False))
                _sf_min_batch = int(_sf_os.environ.get("ELI_MIN_BATCH", "128") or "128")
                if _sf_igpu:
                    _sf_min_batch = min(_sf_min_batch, 32)
                _sf_user_batch = max(_user_batch, _sf_min_batch)
                if _sf_igpu:
                    _sf_user_batch = min(_sf_user_batch, 32)
                _avail_gb = float(_sf_avail_ram())
                # the fit assumes the KV cache type this load will use
                _sf_kv_type = str(cache_type_k or "f16")
                _sf_kvq = _sf_kv_type.lower() not in ("f16", "bf16", "f32")
                if _user_ctx and 2048 <= int(_user_ctx) <= _sf_train:
                    _sf_user_ctx = int(_user_ctx)
                else:
                    # auto, or a request past the trained context: the target the tuner and the
                    # headless loader use, from this model and this machine's memory now
                    from eli.core.hardware_profile import auto_ctx_target as _sf_auto_ctx
                    _sf_gpu_now = _sf_budget() if gpu_offload_supported is not False else None
                    _sf_user_ctx = int(_sf_auto_ctx(
                        str(path_obj), _sf_model_gb,
                        free_vram_mb=int(getattr(_sf_gpu_now, "free_mb", 0) or 0),
                        available_ram_gb=_avail_gb,
                        use_gpu=bool(_sf_gpu_now and int(getattr(_sf_gpu_now, "free_mb", 0) or 0) > 0
                                     and int(effective_n_gpu_layers) > 0),
                        kv_quantized=_sf_kvq, kv_cache_type=_sf_kv_type))
                    if _sf_train:
                        _sf_user_ctx = min(_sf_user_ctx, _sf_train)
                _eli_floor = _eli_prompt_floor_tokens()
                if _sf_train and _eli_floor and _sf_train < _eli_floor:
                    log.warning(
                        f"[GUI][LOAD] model trained context {_sf_train} is smaller than ELI's own "
                        f"prompt (~{_eli_floor} tokens of persona and answer room): persona and "
                        f"memory will be cut. A model trained on a longer context avoids it.")
                if gpu_offload_supported is False:
                    # CPU-only: size from AVAILABLE RAM, not iGPU shared-memory VRAM.
                    # VRAM smart-fit on Iris Xe returned ctx=3996 from ~1.5GB budget while
                    # the model + KV actually live in system RAM, not iGPU VRAM budget.
                    _ram_ceiling = int(_sf_ram_ceil(_sf_model_gb, _sf_train, model_path=str(path_obj)))
                    _sf_user_ctx = min(int(_sf_user_ctx), _ram_ceiling) if _ram_ceiling > 0 else int(_sf_user_ctx)
                    from eli.core.hardware_profile import cpu_ram_budget_mb as _cpu_ram_budget
                    _ram_budget_mb = _cpu_ram_budget(_avail_gb)
                    _sf_ctx, _sf_layers, _sf_batch = _sf_fit(
                        _sf_model_gb,
                        0,
                        _avail_gb,
                        user_ctx=_sf_user_ctx,
                        user_batch=_sf_user_batch,
                        reserve_mb=512,
                        kv_quantized=_sf_kvq, kv_cache_type=_sf_kv_type,
                        min_batch=_sf_min_batch,
                        model_path=str(path_obj),
                        force_cpu=True,
                    )
                    _sf_layers = 0
                    _sf_ctx = min(int(_sf_ctx), _ram_ceiling) if _ram_ceiling > 0 else int(_sf_ctx)
                    log.debug(
                        f"[GUI][LOAD] RAM smart-fit (available={_avail_gb:.1f}GB "
                        f"budget={_ram_budget_mb}MB ceiling={_ram_ceiling} "
                        f"kv={_sf_kv_type}): "
                        f"ctx={_sf_ctx} gpu_layers=0 batch={_sf_batch}")
                    _sf_fit_layers = 0
                    _sf_fit_ctx = int(_sf_ctx)
                    _sf_fit_batch = int(_sf_batch)
                    self.fitted_gpu_layers = 0
                    # CPU-only: RAM fit is attempt 1 — user ctx that exceeds measured
                    # headroom stalls for minutes on low-RAM CPU-only hosts.
                    _add_attempt("ram-smart-fit", _sf_ctx, 0, _sf_batch, front=True)
                else:
                    _sf_gpu = _sf_budget()
                    if _sf_gpu and _sf_gpu.free_mb > 0:
                        from eli.core.hardware_profile import vram_reserve_mb as _vrm
                        _sf_reserve = int(_vrm(gpu_integrated=_sf_igpu))
                        _sf_ctx, _sf_layers, _sf_batch = _sf_fit(
                            _sf_model_gb, _sf_gpu.free_mb, _avail_gb,
                            user_ctx=_sf_user_ctx, user_batch=_sf_user_batch,
                            reserve_mb=_sf_reserve, kv_quantized=_sf_kvq,
                            kv_cache_type=_sf_kv_type, min_batch=_sf_min_batch,
                            model_path=str(path_obj),
                            gpu_integrated=_sf_igpu,
                            user_gpu_layers=_user_gpu_layers,
                            moe_resident_gb=(_moe_gui["resident_gb"] if _moe_gui else None),
                        )
                        if _moe_gui:
                            _sf_layers_real = (
                                _moe_gui["layers"] if int(_sf_layers) >= 99 else int(_sf_layers)
                            )
                            print(f"   MoE fit: {_sf_layers_real}/{_moe_gui['layers']} layers' core "
                                  f"weights fit in {_sf_gpu.free_mb}MB free VRAM "
                                  f"(~{_moe_gui['resident_gb']}GB); ~{_moe_gui['experts_gb']}GB of "
                                  f"experts stay in RAM regardless")
                            log.debug(
                                f"[GUI] MoE fit: {_sf_layers_real}/{_moe_gui['layers']} layers' core "
                                f"weights fit ({_moe_gui['resident_gb']}GB) — experts "
                                f"~{_moe_gui['experts_gb']}GB stay in RAM")
                        from eli.core.hardware_profile import describe_gpu_layers as _describe_gpu
                        log.debug(
                            f"[GUI][LOAD] smart-fit preview (post-init free={_sf_gpu.free_mb}MB "
                            f"reserve={_sf_reserve}MB kvq={_sf_kvq}) — the fallback used only if your "
                            f"requested settings below cannot be honoured: "
                            f"ctx={_sf_ctx} gpu_layers={_describe_gpu(_sf_layers)} batch={_sf_batch}")
                        if _sf_ctx < _sf_user_ctx:
                            log.debug(
                                f"[GUI][LOAD] smart-fit measured ctx {_sf_ctx} as the fit for "
                                f"{_sf_gpu.free_mb}MB free VRAM (your {_sf_user_ctx} is tried first; "
                                f"this is the fallback if it cannot be honoured — reduced to avoid "
                                f"OOM, never replaced)")
                        if _user_gpu_layers and _sf_layers < _user_gpu_layers:
                            log.debug(
                                f"[GUI][LOAD] smart-fit measured {_sf_layers} GPU layers as the fit for "
                                f"{_sf_gpu.free_mb}MB free VRAM (your {_user_gpu_layers} is tried first; "
                                f"this is the fallback). Your context of {_sf_ctx} is kept either way "
                                f"(ctx is reduced last). If the fallback is used, expect slower "
                                f"generation: the layers that did not fit run on CPU. Free VRAM or lower "
                                f"the context to get them back.")
                        _sf_fit_layers = int(_sf_layers)
                        _sf_fit_ctx = int(_sf_ctx)
                        _sf_fit_batch = int(_sf_batch)
                        self.fitted_gpu_layers = int(_sf_layers)
                        _sf_free_mb = int(_sf_gpu.free_mb)
                        _sf_total_mb = int(getattr(_sf_gpu, "total_mb", 0) or 0) or None
                        _add_attempt("smart-fit", _sf_ctx, _sf_layers, _sf_batch)
            except Exception as _sf_err:
                log.debug(f"[GUI][LOAD] smart-fit attempt skipped: {_sf_err}")

            if gpu_offload_supported is False:
                _base_layers = 0

            # ── The user's OWN settings, first ────────────────────────────
            #
            # What the operator chose in the startup dialog is attempted before
            # anything ELI calculates: their ctx and their batch, exactly as set.
            #
            # Nothing here is clamped and nothing is assumed. The operator's
            # numbers go to llama.cpp exactly as entered; ELI's own calculations
            # are only ever a FALLBACK, used when the request is PROVEN not to
            # work on this machine.
            #
            # Proving it is the part that was missing. An earlier note here
            # reasoned that "the refusal should come from the driver, not from
            # ELI pre-emptively overruling them", and that queuing the raw
            # request "costs one failed load attempt on hardware that genuinely
            # cannot honour it". The intent was right; the mechanism does not
            # exist. llama.cpp/CUDA allocate LAZILY: asking for more than fits
            # does not fail at load — it reports success, wins
            # `selected=requested`, and then aborts the whole PROCESS on the
            # first generation large enough to touch the buffers. Live at 2.2.7:
            #
            #   smart-fit ... : ctx=10384 gpu_layers=28
            #   smart-fit reduced GPU layers 99->28 to fit 6268MB free VRAM
            #   attempt 1/13: requested (ctx=10384 gpu_layers=99 batch=128)
            #   selected=requested (ctx=10384 gpu_layers=99 batch=128)
            #   ...
            #   prompt_tokens=5189
            #   ggml-cuda.cu:98: CUDA error → Aborted (core dumped)
            #
            # There is no failed attempt to fall back FROM, so none of the twelve
            # rungs below can ever run. It had survived only because earlier
            # sessions' first turn was a short greeting whose KV cache stayed
            # small; the first big prompt killed it.
            #
            # So the request is VERIFIED instead of guessed at. eli/core/
            # load_probe.py loads these exact numbers in a SEPARATE PROCESS and
            # drives a real decode through them. A process can survive a child's
            # abort(); it cannot survive its own. If the probe comes back clean
            # the operator's settings are used verbatim — which is now a fact
            # about this machine rather than an assumption. If the probe proves
            # they abort, that is the "cannot be honoured" signal the ladder
            # below was always meant to receive, and the fallbacks take over.
            #
            # No value is reduced, capped or substituted here.
            #
            # The proof is only spent where there is something to doubt. When the
            # request sits INSIDE what the fit just measured, the hardware has
            # already been shown to have room and a probe would cost a full cold
            # load to confirm what is not in question — so it is skipped and the
            # settings load immediately. Shipped without this, 2.2.8 probed every
            # GPU start, including the ones certain to pass.
            #
            # Only a request that EXCEEDS the measured capacity is proven, which
            # is the case that silently aborts the process mid-generation. The
            # verdict is cached per (model, params, GPU), so even that is paid
            # once per configuration rather than once per startup.
            #
            # Layers alone are not enough. Live 2.4.32: smart-fit measured
            # ctx=4096 gpu_layers=11 batch=128 for a 22GB Nemotron on an 8GB
            # card; the operator had layers=11 (equal) but ctx=12000 / batch=512.
            # `_needs_proof` was False, load reported success, first decode hit
            # ggml-cuda.cu and aborted the process. Prove on ANY axis that
            # exceeds the measured fit.
            _proof_bits: list[str] = []
            if _sf_fit_layers is not None and int(_base_layers) > int(_sf_fit_layers):
                _proof_bits.append(
                    f"gpu_layers {_base_layers}>{_sf_fit_layers}")
            if _sf_fit_ctx is not None and int(_base_ctx) > int(_sf_fit_ctx):
                _proof_bits.append(f"ctx {_base_ctx}>{_sf_fit_ctx}")
            if _sf_fit_batch is not None and int(_base_batch) > int(_sf_fit_batch):
                _proof_bits.append(f"batch {_base_batch}>{_sf_fit_batch}")
            # Weights alone that need more VRAM than is free cannot load: the probe would spend its
            # whole budget on a certain failure (109 s live, with another program holding 5 GB).
            # Nothing is recorded against the settings, so they are used again once the card is free.
            _cannot_fit = ""
            if gpu_offload_supported is not False and int(_base_layers) > 0 and _sf_free_mb is not None:
                try:
                    from eli.core.hardware_profile import (describe_gpu_layers as _dgl,
                                                           gpu_weights_beyond_free as _beyond)
                    _floor_layers = int(_base_layers)
                    if _moe_gui:
                        from eli.core import moe_offload as _moe_floor
                        _floor_layers = _moe_floor.full_offload_layers(_floor_layers, str(path_obj))
                    _need_mb = _beyond(str(path_obj), _floor_layers, _sf_free_mb, experts_in_ram=bool(_moe_gui))
                    if _need_mb is not None:
                        _cannot_fit = (f"{_dgl(_base_layers)} GPU layers need {_need_mb:.0f} MB of VRAM "
                                       f"for their weights alone and {_sf_free_mb} MB is free")
                except Exception:
                    log.debug("[GUI][LOAD] weight floor unavailable", exc_info=True)
            if _sf_free_mb is not None and (_cannot_fit or _proof_bits):
                try:
                    from eli.core.hardware_profile import describe_vram_holders, vram_holders
                    _held = describe_vram_holders(vram_holders(), total_mb=_sf_total_mb)
                except Exception:
                    _held = ""
                if _held:
                    _load_vram_held = _held
                    log.warning(f"[GUI][LOAD] other programs are using this card's memory: {_held}. "
                                f"Close them or have them release the GPU, then reload the model.")
            if _cannot_fit:
                log.warning(f"[GUI][LOAD] your settings cannot load right now: {_cannot_fit}. "
                            f"Loading the measured fit; your settings are kept and used again "
                            f"once that memory is free.")
                _proof_bits = []
            if _ctx_is_auto:
                # nothing was asked for: the measured fit leads, ELI's own target is never probed
                _base_ctx = int(_sf_fit_ctx or 0) or int(_sf_user_ctx or 0) or 2048
                _proof_bits = [b for b in _proof_bits if not b.startswith("ctx ")]
            _needs_proof = bool(_proof_bits)
            if _needs_proof:
                log.debug(
                    f"[GUI][LOAD] your settings exceed the measured fit "
                    f"({'; '.join(_proof_bits)}) — verifying them on this "
                    f"machine before loading (a verdict is cached; a probe that "
                    f"times out is remembered too, so it is not repeated every launch; "
                    f"set ELI_LOAD_PROBE=0 to skip)")
            if gpu_offload_supported is False:
                # Honour user settings as a fallback rung, not attempt 1 — RAM fit leads.
                _add_attempt("requested", _base_ctx, 0, _base_batch, front=False)
            elif not _cannot_fit:
                _add_attempt("requested", _base_ctx, _base_layers, _base_batch,
                             front=True, verify=_needs_proof)

            # Hardware profile recommendation (legacy static fallback) — COMPUTED
            # here, but QUEUED further down, after the reduce-to-fit rungs.
            #
            # It used to be inserted right here, as the second candidate, and that
            # broke the loader's own documented order. hardware_profile.allocate()
            # reduces "GPU layers -> batch -> shed layers -> ctx LAST", explicitly
            # so "context (quality) is preserved as long as possible". The rungs
            # that implement that (live-tuner, lower-batch-half/qtr at the user's
            # FULL ctx) all sit below — so a single smart-fit failure skipped every
            # one of them and landed on this static number instead.
            #
            # Live at 2.1.98: smart-fit asked for ctx=10384, llama.cpp refused
            # once, and the next thing tried was this profile at ctx=4096 — the
            # user's context cut 60% before batch or layers had been touched at
            # all. The session then ran in a window smaller than its own prompt.
            #
            # A static file written by an earlier optimiser run is the LEAST
            # informed candidate here: the live tuner re-measures actual free VRAM
            # and the batch rungs cost nothing but speed. This belongs after them.
            _hw_profile_attempt = None
            if _hw_profile_ctx is not None:
                _hw_eff_layers = int(_hw_profile_gpu_layers)
                if gpu_offload_supported is False:
                    _hw_eff_layers = 0
                _hw_eff_batch = int(_hw_profile_batch)
                if _hw_eff_layers <= 0 and _hw_eff_batch > 128:
                    _hw_eff_batch = 128
                _hw_profile_attempt = ("hw-profile", _hw_profile_ctx, _hw_eff_layers, _hw_eff_batch)

            if _base_layers > 0:
                # Live-tuner FIRST (hardware-measured): re-run allocate() against the GPU's
                # CURRENT free VRAM, so the second-choice attempt is what THIS machine can
                # actually support right now — not a blind fraction of the (possibly stale)
                # profile. Promoted ahead of the generic rungs below so the fallback is
                # optimised per individual hardware, which it previously was not.
                try:
                    import os as _os
                    from eli.core.startup_hardware_optimizer import (
                        gpu_budget_for_fit as _lt_budget,
                        allocate as _hw_alloc, find_model as _fm,
                        load_settings as _hls, size_gb as _sgb,
                        detect_ram_gb as _drg,
                    )
                    _lt_gpu = _lt_budget()
                    if _lt_gpu and _lt_gpu.free_mb > 0:
                        _lt_settings = _hls()
                        _lt_model = _fm(_lt_settings)
                        _lt_model_gb = _sgb(_lt_model) if _lt_model else 0.0
                        _lt_ram = _drg()
                        if _lt_model_gb > 0:
                            _lt_ctx, _lt_layers, _lt_batch, _, _, _ = _hw_alloc(
                                _lt_model, _lt_model_gb, _lt_ram, _lt_gpu
                            )
                            if _lt_layers > 0:
                                _add_attempt("live-tuner-gpu", _lt_ctx, _lt_layers, _lt_batch)
                            # CPU path at live-tuner ctx
                            _add_attempt("live-tuner-cpu", _lt_ctx, 0, max(32, _lt_batch // 2))
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                # Generic fallback rungs (deeper fallback) — fractions of user's settings;
                # tried only after the live-tuner. No hardcoded values.
                _b_half = max(64, _base_batch // 2)
                _b_qtr  = max(32, _base_batch // 4)
                _b_min  = max(32, _base_batch // 8)
                _add_attempt("lower-batch-half",   _base_ctx,                       _base_layers,               _b_half)
                _add_attempt("lower-batch-qtr",    _base_ctx,                       _base_layers,               _b_qtr)
                # Static profile lands HERE — after every rung that keeps the
                # user's ctx (live-tuner above, batch reductions just now), and
                # before the rungs that finally start cutting context. See the
                # note where it is computed.
                if _hw_profile_attempt is not None:
                    _add_attempt(*_hw_profile_attempt)
                    _hw_profile_attempt = None
                _add_attempt("ctx75pct-batch-qtr", max(2048, _base_ctx * 3 // 4),   _base_layers,               _b_qtr)
                _add_attempt("ctx50pct-half-gpu",  max(2048, _base_ctx // 2),        max(1, _base_layers // 2),  _b_qtr)
                _add_attempt("ctx37pct-half-gpu",  max(2048, _base_ctx * 3 // 8),   max(1, _base_layers // 2),  _b_min)
                _add_attempt("ctx25pct-third-gpu", max(2048, _base_ctx // 4),        max(1, _base_layers // 3),  _b_min)
                # Dynamic CPU ctx floor: derive from RAM capacity, never a hardcoded int.
                # ELI_MIN_CTX env var overrides if set.
                try:
                    import os as _os2
                    _env_min = _os2.environ.get("ELI_MIN_CTX", "").strip()
                    if _env_min:
                        _cpu_ctx_floor = int(_env_min)
                    else:
                        from eli.core.startup_hardware_optimizer import (
                            ram_ctx_cap as _rcc, detect_available_ram_gb as _drg2
                        )
                        _cpu_ctx_floor = _rcc(_drg2(), 0)
                    _cpu_ctx_floor = min(_cpu_ctx_floor, _base_ctx)
                except Exception:
                    _cpu_ctx_floor = _base_ctx
                _add_attempt("cpu-fallback",      _cpu_ctx_floor,                 0, max(32, _base_batch // 2))
                _add_attempt("cpu-fallback-half", max(1024, _cpu_ctx_floor // 2), 0, max(32, _base_batch // 4))
            else:
                # CPU-only path: derive context steps from RAM capacity + ELI_MIN_CTX.
                # No hardcoded ctx floors — all values are calculated dynamically.
                try:
                    import os as _os3
                    _env_min_cpu = _os3.environ.get("ELI_MIN_CTX", "").strip()
                    if _env_min_cpu:
                        _cpu_base_ctx = min(int(_env_min_cpu), _base_ctx)
                    else:
                        from eli.core.startup_hardware_optimizer import (
                            ram_ctx_cap as _rcc2, detect_available_ram_gb as _drg3
                        )
                        _cpu_base_ctx = min(_rcc2(_drg3(), 0), _base_ctx)
                except Exception:
                    _cpu_base_ctx = _base_ctx
                # CPU-only fallback chain — fractions of user's batch, no hardcoded caps.
                _b_half = max(32, _base_batch // 2)
                _b_qtr  = max(32, _base_batch // 4)
                _b_min  = max(32, _base_batch // 8)
                _add_attempt("cpu-batch-half",  _base_ctx,                          0, _b_half)
                _add_attempt("cpu-ctx-ram-cap", _cpu_base_ctx,                      0, _b_half)
                _add_attempt("cpu-ctx-half",    max(1024, _cpu_base_ctx // 2),      0, _b_qtr)
                _add_attempt("cpu-ctx-quarter", max(1024, _cpu_base_ctx // 4),      0, _b_min)

            # Flush the deferred static profile if no branch above queued it (the
            # CPU-only path skips the GPU rungs entirely). Deferring it must never
            # mean losing it — it is a weak candidate, not a discardable one.
            if _hw_profile_attempt is not None:
                _add_attempt(*_hw_profile_attempt)
                _hw_profile_attempt = None

            _applied = None
            _last_error = ""
            from eli.core.hardware_profile import describe_gpu_layers as _describe_gpu_attempt
            for i, _cand in enumerate(_attempts, start=1):
                log.debug(
                    f"[GUI][LOAD] attempt {i}/{len(_attempts)}: {_cand['label']} "
                    f"(ctx={_cand['n_ctx']} gpu_layers={_describe_gpu_attempt(_cand['n_gpu_layers'])} "
                    f"batch={_cand['n_batch']})",
                )
                # A candidate marked for verification is proven in a separate
                # process before it is loaded here. `except Exception` below
                # cannot catch what this is guarding against: the CUDA backend
                # calls abort(), which takes the interpreter with it, so a bad
                # configuration would never reach the next rung at all.
                if _cand.get("verify"):
                    # Say so BEFORE blocking. Shipped without this line, a live
                    # 2.2.8 launch sat on "attempt 1/13" for three and a half
                    # minutes with nothing on screen, and looked hung.
                    try:
                        from eli.core.load_probe import (
                            budget_is_ceiling_cut as _lp_cut,
                            probe_timeout_for as _lp_budget,
                        )
                        _probe_budget = _lp_budget(str(path_obj), int(_cand["n_ctx"]))
                        _probe_cut = _lp_cut(str(path_obj), int(_cand["n_ctx"]))
                    except Exception:
                        log.debug("[GUI][LOAD] probe budget lookup failed", exc_info=True)
                        _probe_budget = 30.0
                        _probe_cut = False
                    log.info(
                        f"[GUI][LOAD] verifying your settings on this machine "
                        f"(ctx={_cand['n_ctx']} gpu_layers={_cand['n_gpu_layers']} "
                        f"batch={_cand['n_batch']}) — up to "
                        f"{_probe_budget:.0f}s"
                        + (" (this model is large enough that the probe is expected "
                           "to hit that limit; if it does, that is remembered and "
                           "not repeated on later launches)"
                           if _probe_cut else " this once, then remembered…"))
                    try:
                        from eli.core import load_probe as _lp
                        _verdict, _why = _lp.probe_verdict(
                            str(path_obj), int(_cand["n_ctx"]),
                            int(_cand["n_gpu_layers"]), int(_cand["n_batch"]),
                            cache_type_k=str(cache_type_k or ""),
                            cache_type_v=str(cache_type_v or ""),
                        )
                    except Exception as _probe_err:
                        # The check itself is unavailable. That is not evidence
                        # against the operator's settings, so they stand.
                        _verdict, _why = "unavailable", f"probe unavailable: {_probe_err}"
                    # A TIMEOUT is not a pass. 2.2.9 logged "requested settings
                    # verified (probe timed out after 30s (unproven))" and then
                    # loaded ctx=10384 gpu_layers=99 against a measured fit of
                    # 28 — CUDA allocates lazily, so the load looked clean and
                    # the process died with `ggml-cuda.cu:98: CUDA error` /
                    # SIGABRT on the first real prompt. The probe only runs when
                    # the request already exceeds the measured fit, so here
                    # "could not prove it" is a reason to use the measured rungs
                    # below, not a reason to gamble the process on it.
                    #
                    # An UNAVAILABLE probe still leaves the operator's settings
                    # alone: that is no information at all, and a machine that
                    # can never run the probe must not be permanently capped.
                    if _verdict == "timeout":
                        log.warning(
                            f"[GUI][LOAD] could not verify your settings in time "
                            f"({_why}) — they exceed the measured fit"
                            f"{(' (' + '; '.join(_proof_bits) + ')') if _proof_bits else ''}"
                            f", and loading them unproven is what "
                            f"aborts mid-generation. Falling through to the measured "
                            f"fallbacks; raise ELI_LOAD_PROBE_TIMEOUT to allow the "
                            f"check more time, or set ELI_LOAD_PROBE=0 to load them "
                            f"as entered without proof.")
                        _last_error = f"requested settings unproven: {_why}"
                        continue
                    _ok = _verdict != "bad"
                    if not _ok:
                        log.warning(
                            f"[GUI][LOAD] your settings (ctx={_cand['n_ctx']} "
                            f"gpu_layers={_cand['n_gpu_layers']} batch={_cand['n_batch']}) "
                            f"were tested on this machine and could not be honoured "
                            f"({_why}). They are not being altered — falling through "
                            f"to the calculated fallbacks below. Free VRAM, or lower "
                            f"the context or GPU layers, to have them used as entered.")
                        _last_error = f"requested settings failed verification: {_why}"
                        continue
                    if _verdict == "ok":
                        log.debug(f"[GUI][LOAD] requested settings verified ({_why})")
                    else:
                        # Never call an unproven config "verified" — that line is
                        # what made the 2.2.9 crash look like a checked launch.
                        log.debug(f"[GUI][LOAD] proceeding unproven ({_why})")
                # Each candidate already carries a trustworthy number: the operator's own
                # verbatim request, or the VRAM-verified (moe_resident_gb-aware) smart-fit
                # result, or a conservative non-MoE fallback rung. No MoE override needed —
                # forcing every candidate to the full MoE layer count would also silently
                # discard an operator's own explicit partial request.
                _cand_gpu_layers = int(_cand["n_gpu_layers"])
                llama_kwargs: Dict[str, Any] = dict(
                    model_path=str(path_obj),
                    n_ctx=int(_cand["n_ctx"]),
                    n_threads=int(n_threads),
                    n_gpu_layers=_cand_gpu_layers,
                    n_batch=int(_cand["n_batch"]),
                    use_mmap=bool(use_mmap),
                    use_mlock=bool(use_mlock),
                    chat_format="chatml",
                    verbose=False,
                )
                from eli.core.gguf_sizes import llama_cache_args as _cache_args
                _kv_args = _cache_args(cache_type_k, cache_type_v)
                llama_kwargs.update(_kv_args)
                # Capture llama.cpp's own log for this attempt: verbose=False
                # silences it, and without it the only thing the user sees is
                # the destructor's phantom "no attribute 'sampler'".
                from eli.cognition.model_load_diagnostics import (
                    harden_llama_destructor as _harden,
                    capture_llama_log as _cap_log,
                    explain_load_failure as _explain,
                    is_retryable_load_failure as _retryable,
                    kv_cache_refused as _kv_refused,
                    llama_accepts as _accepts,
                )
                _harden()

                def _build_llama(kw):
                    if not _moe_gui or _cand_gpu_layers <= 0:
                        return Llama(**_accepts(Llama, kw))
                    from eli.core import moe_offload as _moe_offload_gui2
                    kw = dict(kw, n_gpu_layers=_moe_offload_gui2.full_offload_layers(
                        kw["n_gpu_layers"], kw.get("model_path")))
                    with _moe_offload_gui2.expert_offload_params():
                        return Llama(**_accepts(Llama, kw))

                _attempt_log: list = []
                try:
                    try:
                        with _cap_log() as _attempt_log:
                            self.model = _build_llama(llama_kwargs)
                    except Exception:
                        # llama.cpp refused the quantized KV cache (it needs flash attention,
                        # which this backend or build lacks): the same candidate with the
                        # default cache, and no later candidate asks for it again.
                        if not (_kv_args and _kv_refused(_attempt_log)):
                            raise
                        log.warning("[GUI][LOAD] llama.cpp refused a %s KV cache here (it needs "
                                    "flash attention); loading with the default cache instead",
                                    cache_type_k or cache_type_v)
                        for _kv_name in _kv_args:
                            llama_kwargs.pop(_kv_name, None)
                        cache_type_k = cache_type_v = ""
                        self.model = None
                        gc.collect()
                        with _cap_log() as _attempt_log:
                            self.model = _build_llama(llama_kwargs)
                    _applied = dict(_cand)
                    _applied["n_gpu_layers"] = _cand_gpu_layers
                    _applied["moe_expert_offload"] = bool(_moe_gui)
                    _applied["moe_resident_gb"] = (_moe_gui or {}).get("resident_gb") if _moe_gui else None
                    _applied["moe_experts_gb"] = (_moe_gui or {}).get("experts_gb") if _moe_gui else None
                    try:
                        # llama.cpp's own KV size for this model replaces the estimate from its header
                        from eli.core.gguf_sizes import record_load_report, loaded_cache_types
                        record_load_report(path_obj, _attempt_log)
                        _kv_loaded = loaded_cache_types(_attempt_log)
                        if _kv_loaded:
                            _applied["cache_type_k"], _applied["cache_type_v"] = _kv_loaded
                    except Exception:
                        log.debug("[GUI][LOAD] KV report not recorded", exc_info=True)
                    break
                except Exception as _attempt_err:
                    self.model = None
                    gc.collect()
                    if not _retryable(_attempt_log):
                        # A missing tensor or unknown architecture is a property
                        # of the file and this build. Thirteen more attempts
                        # would fail identically; say why and stop.
                        _last_error = _explain(
                            _attempt_err, _attempt_log, path_obj,
                            gpu_layers=_cand.get("n_gpu_layers"))
                        log.warning("[GUI][LOAD] unrecoverable: %s", _last_error)
                        break
                    _last_error = _explain(
                        _attempt_err, _attempt_log, path_obj,
                        gpu_layers=_cand.get("n_gpu_layers"))
                    log.debug(f"[GUI][LOAD] attempt failed: {_last_error}")
                    continue

            if self.model is None or _applied is None:
                raise RuntimeError(
                    "Failed to create llama_context after adaptive load attempts. "
                    f"Last error: {_last_error or 'unknown'}"
                )

            applied_n_ctx = int(_applied["n_ctx"])
            applied_n_gpu_layers = int(_applied["n_gpu_layers"])
            applied_n_batch = int(_applied["n_batch"])
            from eli.core.hardware_profile import describe_gpu_layers as _describe_gpu_applied
            log.debug(
                f"[GUI][LOAD] selected={_applied['label']} "
                f"(ctx={applied_n_ctx} gpu_layers={_describe_gpu_applied(applied_n_gpu_layers)} "
                f"batch={applied_n_batch})",
            )

            # the Llama object keeps its own: n_ctx is a method there, and overwriting it with a number
            # made every llm.n_ctx() reader fall back to the requested ctx instead of the loaded one
            self.n_ctx = int(applied_n_ctx)
            self.n_threads = int(n_threads)
            self.n_gpu_layers = int(applied_n_gpu_layers)
            self.n_batch = int(applied_n_batch)
            try:
                from eli.cognition import gguf_inference as _gg
                _gg._llm = self.model
            except Exception as _wire_err:
                log.debug(f"[GUI] gguf runtime handoff failed: {_wire_err}")
            self.is_loaded = True
            self.n_ctx = int(self.n_ctx or 0)
            self.n_threads = int(self.n_threads or 0)
            self.n_gpu_layers = int(self.n_gpu_layers or 0)
            self.n_batch = int(getattr(self.model, 'n_batch', 0) or 0)
            try:
                from eli.cognition import gguf_inference as _ggi
                from eli.runtime.inference_footprint import record_load_memory
                _ggi._llm = self.model
                _live_payload = {
                    "provider": "gguf",
                    "loaded": True,
                    "model_path": str(path_obj),
                    "model_name": path_obj.name,
                    "n_ctx": int(self.n_ctx),
                    "n_gpu_layers": int(self.n_gpu_layers),
                    "n_threads": int(self.n_threads),
                    "n_batch": int(self.n_batch),
                    "requested_n_gpu_layers": int(requested_n_gpu_layers),
                    # the cache llama.cpp says it made; what was asked for when it did not say
                    "cache_type_k": _applied.get("cache_type_k") or str(cache_type_k or "f16"),
                    "cache_type_v": _applied.get("cache_type_v") or str(cache_type_v or cache_type_k or "f16"),
                    "gpu_offload_supported": gpu_offload_supported,
                    "load_mode": "GPU" if int(self.n_gpu_layers) > 0 else "CPU",
                    "moe_expert_offload": bool(_moe_gui),
                    "moe_resident_gb": (_moe_gui or {}).get("resident_gb") if _moe_gui else None,
                    "moe_experts_gb": (_moe_gui or {}).get("experts_gb") if _moe_gui else None,
                    "live_inference_memory": record_load_memory(
                        self.model, pre_load_rss_bytes=_pre_load_rss
                    ),
                }
                _ggi.publish_live_runtime(_live_payload)
                print("✅ gguf_inference live runtime override published")
            except Exception as e:
                log.debug(f"[GUI] live runtime override publish failed: {e}")

            # The reasoning-mode budgets were derived from the startup tuner's
            # recommended ctx, which the attempt list above is free to override —
            # and did. Re-derive them from the ctx that actually loaded, or every
            # mode plans against a context the model is not running.
            try:
                from eli.core.startup_hardware_optimizer import (
                    resize_budgets_to_effective_ctx as _resize_budgets,
                )
                _budget_delta = _resize_budgets(int(self.n_ctx))
                if _budget_delta:
                    log.debug(
                        f"[GUI][LOAD] mode budgets re-derived from effective ctx={int(self.n_ctx)}: "
                        f"{sorted(_budget_delta)} (were sized for the tuner's recommendation)")
            except Exception as _budget_err:
                log.debug(f"[GUI][LOAD] mode budget re-derivation skipped: {_budget_err}")
            self.load_error = None
            print(f"✅ Model loaded successfully")
            self._write_shared_runtime_snapshot(
                str(path_obj),
                self.n_ctx,
                n_threads,
                self.n_gpu_layers,
                requested_n_gpu_layers=requested_n_gpu_layers,
                requested_n_ctx=_user_ctx,
                requested_n_batch=_user_batch,
                gpu_offload_supported=gpu_offload_supported,
                vram_free_mb_at_load=_sf_free_mb,
                vram_held_by_others=_load_vram_held,
            )
            return True
        except Exception as e:
            self.load_error = f"Failed to load model: {str(e)}"
            print(f"❌ {self.load_error}")
            self.model = None
            self.is_loaded = False
            gc.collect()
            return False
    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 2048,
             temperature: float = 0.7) -> str:
        if not self.is_loaded or not self.model:
            return "❌ Model not loaded"
        try:
            response = self.model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
            )
            return response['choices'][0]['message']['content'].strip()
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
        if not self.is_loaded or not self.model:
            yield "❌ Model not loaded"
            return
        try:
            stream = self.model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
                stream=True,
            )
            for chunk in stream:
                delta = chunk['choices'][0].get('delta', {})
                token = delta.get('content', '')
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
        try:
            from eli.integrations.ollama.client import ensure_server_running
            ensure_server_running()   # start a local Ollama that's installed but stopped
        except Exception:
            log.debug("[OLLAMA] auto-start probe failed", exc_info=True)
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
