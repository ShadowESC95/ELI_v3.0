"""models — extracted from gui_eli_original.py (v3 modularisation)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class LocalModelManager:
    provider_name = "gguf"
    def __init__(self):
        self.model = None
        self.model_path = None
        self.is_loaded = False
        self.load_error = None
    def _write_shared_runtime_snapshot(
        self,
        model_path: str,
        n_ctx: int,
        n_threads: int,
        n_gpu_layers: int,
        *,
        requested_n_gpu_layers: Optional[int] = None,
        gpu_offload_supported: Optional[bool] = None,
    ):
        try:
            from eli.core.paths import get_paths
            snap_path = Path(get_paths().artifacts_dir) / 'runtime_snapshot.json'
            effective_n_gpu_layers = int(n_gpu_layers or 0)
            requested_layers = int(
                requested_n_gpu_layers
                if requested_n_gpu_layers is not None
                else effective_n_gpu_layers
            )
            payload = {
                'provider': 'gguf',
                'model_path': str(model_path),
                'model_name': Path(model_path).name if model_path else '',
                'n_ctx': int(n_ctx or 0),
                'n_gpu_layers': effective_n_gpu_layers,
                'n_threads': int(n_threads or 0),
                'n_batch': int(getattr(self, 'n_batch', 0) or 0),
                'requested_n_gpu_layers': requested_layers,
                'gpu_offload_supported': gpu_offload_supported,
                'load_mode': 'GPU' if effective_n_gpu_layers > 0 else 'CPU',
                'requested': {
                    'n_ctx': int(n_ctx or 0),
                    'n_gpu_layers': requested_layers,
                    'n_threads': int(n_threads or 0),
                    'n_batch': int(getattr(self, 'n_batch', 0) or 0),
                },
                'effective': {
                    'n_ctx': int(n_ctx or 0),
                    'n_gpu_layers': effective_n_gpu_layers,
                    'n_threads': int(n_threads or 0),
                    'n_batch': int(getattr(self, 'n_batch', 0) or 0),
                },
                'loaded': bool(getattr(self, 'is_loaded', False)),
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
            print(f"🔄 Loading model: {path_obj.name}")
            print(f"   Size: {path_obj.stat().st_size / (1024**3):.2f} GB")
            # Preserve user's explicit settings — attempt 1 always honours these exactly.
            # The hardware profile is read separately and inserted as a fallback candidate
            # so it never overrides deliberate user choices.
            _user_ctx = int(n_ctx)
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
                    _hw_profile_ctx = int(_eli_profile.get("n_ctx", _user_ctx))
                    _hw_profile_gpu_layers = int(_eli_profile.get("n_gpu_layers", _user_gpu_layers))
                    _hw_profile_batch = int(_eli_profile.get("batch_size", _user_batch))
                    log.debug(
                        f"[GUI][HW_PROFILE] startup profile read: "
                        f"ctx={_hw_profile_ctx} gpu_layers={_hw_profile_gpu_layers} "
                        f"batch={_hw_profile_batch} "
                        f"(user: ctx={_user_ctx} gpu_layers={_user_gpu_layers} "
                        f"batch={_user_batch})"
                    )
            except Exception as _eli_hw_err:
                log.debug(f"[GUI][HW_PROFILE] profile read failed: {_eli_hw_err}")

            print(f"   GPU-layer load parameter: {n_gpu_layers}")
            print(f"   Batch size: {n_batch}")
            if cache_type_k or cache_type_v:
                print(f"   KV cache: K={cache_type_k or 'default'} V={cache_type_v or 'default'}")
            requested_n_gpu_layers = int(n_gpu_layers)
            gpu_offload_supported = None
            try:
                _supports_fn = getattr(_llama_native, "llama_supports_gpu_offload", None)
                if callable(_supports_fn):
                    gpu_offload_supported = bool(_supports_fn())
            except Exception:
                gpu_offload_supported = None
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
            # The user's OWN settings are queued FIRST — but below, once the fit
            # has measured free VRAM, because the layer count has to be clamped
            # against it. See the note at the insertion point.
            _sf_fit_layers = None

            # ── Smart loader (fit-to-hardware fallback) ───────────────────
            # Anchor on the user's preferred ctx/batch, then fit into the VRAM
            # actually free NOW (vision + nomic already resident), reducing GPU
            # LAYERS first (in increments), then batch, then ctx — quality over
            # speed. Fully dynamic from live free VRAM; no static profile.
            # Guarded — if it fails, the user/profile/fallback attempts run.
            import os as _sf_os
            try:
                from eli.core.startup_hardware_optimizer import (
                    detect_nvidia_gpus as _sf_dng, select_gpu as _sf_sg,
                )
                from eli.core.hardware_profile import smart_fit_config as _sf_fit
                from eli.core.startup_hardware_optimizer import train_ctx_for_model as _sf_train_ctx
                _sf_gpu = _sf_sg(_sf_dng())
                if _sf_gpu and _sf_gpu.free_mb > 0:
                    _sf_model_gb = path_obj.stat().st_size / (1024 ** 3)
                    from eli.core.hardware_profile import vram_reserve_mb as _vrm
                    _sf_reserve = int(_vrm())
                    _sf_kvq = bool(_sf_gpu.total_mb and _sf_gpu.total_mb < 12000)
                    # Anchor the fit on the user's CHOSEN ctx. The startup popup's
                    # "Context target fraction" (ELI_CTX_FRACTION) is the canonical control:
                    # requested ctx = fraction × the model's trained length. smart_fit_config
                    # then honours it by SHEDDING GPU LAYERS (ctx preserved — quality over
                    # speed), so the fraction is a real speed↔context dial. An explicit
                    # SMALLER settings n_ctx still caps it (user wants less). No hardcode.
                    _sf_train = int(_sf_train_ctx(str(path_obj)))  # model's REAL n_ctx_train (metadata)
                    # ELI's NEED, derived (not a magic number): the full persona/memory brief
                    # budget + a generation reserve. Sizing ctx below this is what forces the
                    # head/tail chop that mangles the prompt → weak-model garbage. So this is a
                    # FLOOR, not just a target: any model that CAN hold the brief is never asked
                    # for less, so the brief is never chopped. Both terms are env-tunable, and an
                    # explicit user n_ctx still overrides — fully alterable.
                    # DERIVED, not a flat 12288. That literal is the brief's budget in
                    # CHARACTERS and was being spent as TOKENS, so every machine was asked
                    # for four times the context the brief can occupy — and smart_fit pays
                    # for context by shedding GPU layers, so on a small card it moved the
                    # whole model to the CPU to reserve room nothing would use. Same source
                    # as the assembler's own budgets, so the two cannot drift.
                    try:
                        from eli.kernel.engine import eli_brief_budget_tokens as _sf_brief_fn
                        _sf_brief_default = _sf_brief_fn()
                    except Exception:
                        _sf_brief_default = 3072
                    _sf_brief_floor = int(_sf_os.environ.get("ELI_CTX_BRIEF_FLOOR", "")
                                          or _sf_brief_default)
                    _sf_gen_reserve = int(_sf_os.environ.get("ELI_CTX_GEN_RESERVE", "4096") or "4096")
                    _eli_need = _sf_brief_floor + _sf_gen_reserve            # ~16k: brief + generation
                    # Target defaults to ELI's need; never below it. A bigger ctx just costs VRAM
                    # (→ fewer GPU layers), so we right-size to the need rather than maxing the
                    # model's full trained length — keeps layers on the GPU AND fits the brief.
                    _sf_target = int(_sf_os.environ.get("ELI_CTX_TARGET", str(_eli_need)) or str(_eli_need))
                    _sf_target = max(_sf_target, _eli_need)
                    # Round the grain UP, not down: the target is a NEED (brief +
                    # generation reserve), and flooring it hands back part of the very
                    # reserve it exists to guarantee — 7168 became 6144, 1024 tokens
                    # short. Still capped by the model's trained length.
                    _sf_grain_ctx = -(-int(_sf_target) // 2048) * 2048
                    _sf_user_ctx = max(2048, min(_sf_grain_ctx, _sf_train))
                    # No-chop floor: if the model's trained context can hold ELI's brief, request
                    # at least that — even when VRAM is tight (smart_fit sheds layers→batch→ctx,
                    # so ctx is preserved). Guarantees the persona/memory brief is never truncated
                    # on a capable model. (A model below the need — e.g. the 4k phi-3 — can't be
                    # raised past its trained length, so it's flagged incompatible below instead.)
                    if _sf_train >= _eli_need:
                        _sf_user_ctx = max(_sf_user_ctx, -(-_eli_need // 2048) * 2048)
                    # A settings n_ctx wins ONLY when the operator actually chose it.
                    # Unpinned values are this machine's derived profile (see
                    # runtime_settings._apply_hardware_derived_defaults), and letting a
                    # derived number override the need reintroduced the chop it exists to
                    # prevent: a profile computed at ctx=4096 would be honoured as an
                    # "explicit choice", cutting ELI's own brief in half. Pinned means the
                    # operator typed it, and then it is theirs.
                    try:
                        from eli.core.runtime_settings import (
                            operator_pinned_keys as _sf_pinned, load_settings as _sf_ls)
                        _sf_ctx_is_operators = "n_ctx" in _sf_pinned(_sf_ls())
                    except Exception:
                        _sf_ctx_is_operators = True   # unknown provenance → honour it
                    if (_user_ctx and 2048 <= int(_user_ctx) <= _sf_train
                            and (_sf_ctx_is_operators or int(_user_ctx) >= _sf_user_ctx)):
                        _sf_user_ctx = int(_user_ctx)  # explicit user ctx wins (capped to real ctx)
                    # Incompatibility guard: a model whose ENTIRE trained context is smaller than
                    # ELI's prompt budget can't run without truncating persona/memory — warn loudly
                    # instead of silently degrading or limping onto the CPU (the 4k phi-3 case).
                    if _sf_train < _sf_brief_floor:
                        log.warning(
                            f"[GUI][LOAD] model trained context {_sf_train} < ELI's prompt budget "
                            f"~{_sf_brief_floor}: persona/memory will be truncated and output quality "
                            f"will suffer. Choose a model with >= {_sf_brief_floor} context "
                            f"(e.g. Qwen3-8B = 40960).")
                    _sf_min_batch = int(_sf_os.environ.get("ELI_MIN_BATCH", "128") or "128")
                    _sf_ctx, _sf_layers, _sf_batch = _sf_fit(
                        _sf_model_gb, _sf_gpu.free_mb,
                        user_ctx=_sf_user_ctx, user_batch=max(_user_batch, _sf_min_batch),
                        reserve_mb=_sf_reserve, kv_quantized=_sf_kvq,
                        min_batch=_sf_min_batch,
                    )
                    log.debug(
                        f"[GUI][LOAD] smart-fit (post-init free={_sf_gpu.free_mb}MB "
                        f"reserve={_sf_reserve}MB kvq={_sf_kvq}): "
                        f"ctx={_sf_ctx} gpu_layers={_sf_layers} batch={_sf_batch}")
                    if _sf_ctx < _sf_user_ctx:
                        log.debug(
                            f"[GUI][LOAD] smart-fit reduced ctx {_sf_user_ctx}->{_sf_ctx} to fit "
                            f"{_sf_gpu.free_mb}MB free VRAM (your setting didn't fit — reduced to "
                            f"avoid OOM, not replaced)")
                    # Layers get the same announcement as ctx. Shedding GPU layers is
                    # the FIRST thing smart-fit does (ctx is protected to last), so it
                    # is the reduction users actually hit — and it was near-silent: a
                    # 99 -> 31 cut showed up only as a number in the parameter list
                    # while per-turn latency went from ~3s to ~14s.
                    if _user_gpu_layers and _sf_layers < _user_gpu_layers:
                        log.debug(
                            f"[GUI][LOAD] smart-fit reduced GPU layers {_user_gpu_layers}->{_sf_layers} "
                            f"to fit {_sf_gpu.free_mb}MB free VRAM — your context of {_sf_ctx} was kept "
                            f"(ctx is reduced last). Expect slower generation: the layers that did not "
                            f"fit run on CPU. Free VRAM or lower the context to get them back.")
                    _sf_fit_layers = int(_sf_layers)
                    _add_attempt("smart-fit", _sf_ctx, _sf_layers, _sf_batch)
            except Exception as _sf_err:
                log.debug(f"[GUI][LOAD] smart-fit attempt skipped: {_sf_err}")

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
            _needs_proof = (
                _sf_fit_layers is not None
                and int(_base_layers) > int(_sf_fit_layers)
            )
            if _needs_proof:
                log.debug(
                    f"[GUI][LOAD] your {_base_layers} GPU layers exceed the "
                    f"{_sf_fit_layers} measured to fit — verifying them on this "
                    f"machine before loading (one-off, then cached; set "
                    f"ELI_LOAD_PROBE=0 to skip)")
            _add_attempt("requested", _base_ctx, _base_layers, _base_batch,
                         front=True, verify=_needs_proof)

            # User's exact settings — in case smart-fit was skipped (no GPU
            # detected) or the user's request already fits unchanged.
            # (queued above, ahead of smart-fit — see the note there)

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
                        detect_nvidia_gpus as _dng, select_gpu as _sg,
                        allocate as _hw_alloc, find_model as _fm,
                        load_settings as _hls, size_gb as _sgb,
                        detect_ram_gb as _drg,
                    )
                    _lt_gpus = _dng()
                    _lt_gpu = _sg(_lt_gpus)
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
                    pass
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
                            ram_ctx_cap as _rcc, detect_ram_gb as _drg2
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
                            ram_ctx_cap as _rcc2, detect_ram_gb as _drg3
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
            for i, _cand in enumerate(_attempts, start=1):
                log.debug(
                    f"[GUI][LOAD] attempt {i}/{len(_attempts)}: {_cand['label']} "
                    f"(ctx={_cand['n_ctx']} gpu_layers={_cand['n_gpu_layers']} batch={_cand['n_batch']})",
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
                    log.info(
                        f"[GUI][LOAD] verifying your settings on this machine "
                        f"(ctx={_cand['n_ctx']} gpu_layers={_cand['n_gpu_layers']} "
                        f"batch={_cand['n_batch']}) — up to "
                        f"{int(float(os.environ.get('ELI_LOAD_PROBE_TIMEOUT', '') or 30))}s "
                        f"this once, then remembered…")
                    try:
                        from eli.core.load_probe import probe_config
                        _ok, _why = probe_config(
                            str(path_obj), int(_cand["n_ctx"]),
                            int(_cand["n_gpu_layers"]), int(_cand["n_batch"]),
                        )
                    except Exception as _probe_err:
                        # The check itself is unavailable. That is not evidence
                        # against the operator's settings, so they stand.
                        _ok, _why = True, f"probe unavailable: {_probe_err}"
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
                    log.debug(f"[GUI][LOAD] requested settings verified ({_why})")
                llama_kwargs: Dict[str, Any] = dict(
                    model_path=str(path_obj),
                    n_ctx=int(_cand["n_ctx"]),
                    n_threads=int(n_threads),
                    n_gpu_layers=int(_cand["n_gpu_layers"]),
                    n_batch=int(_cand["n_batch"]),
                    use_mmap=bool(use_mmap),
                    use_mlock=bool(use_mlock),
                    chat_format="chatml",
                    verbose=False,
                )
                if cache_type_k:
                    llama_kwargs["cache_type_k"] = str(cache_type_k)
                if cache_type_v:
                    llama_kwargs["cache_type_v"] = str(cache_type_v)
                # Capture llama.cpp's own log for this attempt: verbose=False
                # silences it, and without it the only thing the user sees is
                # the destructor's phantom "no attribute 'sampler'".
                from eli.cognition.model_load_diagnostics import (
                    harden_llama_destructor as _harden,
                    capture_llama_log as _cap_log,
                    explain_load_failure as _explain,
                    is_retryable_load_failure as _retryable,
                )
                _harden()
                _attempt_log: list = []
                try:
                    with _cap_log() as _attempt_log:
                        try:
                            self.model = Llama(**llama_kwargs)
                        except TypeError:
                            llama_kwargs.pop("cache_type_k", None)
                            llama_kwargs.pop("cache_type_v", None)
                            self.model = Llama(**llama_kwargs)
                    _applied = dict(_cand)
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
            log.debug(
                f"[GUI][LOAD] selected={_applied['label']} "
                f"(ctx={applied_n_ctx} gpu_layers={applied_n_gpu_layers} batch={applied_n_batch})",
            )

            setattr(self.model, "n_ctx", int(applied_n_ctx))
            setattr(self.model, "n_threads", int(n_threads))
            setattr(self.model, "n_gpu_layers", int(applied_n_gpu_layers))
            setattr(self.model, "n_batch", int(applied_n_batch))
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
                _ggi._llm = self.model
                _ggi.set_live_runtime_override({
                    "provider": "gguf",
                    "loaded": True,
                    "model_path": str(path_obj),
                    "model_name": path_obj.name,
                    "n_ctx": int(self.n_ctx),
                    "n_gpu_layers": int(self.n_gpu_layers),
                    "n_threads": int(self.n_threads),
                    "n_batch": int(self.n_batch),
                    "requested_n_gpu_layers": int(requested_n_gpu_layers),
                    "gpu_offload_supported": gpu_offload_supported,
                    "load_mode": "GPU" if int(self.n_gpu_layers) > 0 else "CPU",
                })
                print("✅ gguf_inference live runtime override published")

                # The reasoning-mode budgets were derived from the startup tuner's
                # recommended ctx, which the attempt list above is free to override —
                # and does. Re-derive them from the ctx that actually loaded, or every
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
            except Exception as e:
                log.debug(f"[GUI] live runtime override publish failed: {e}")
            self.load_error = None
            print(f"✅ Model loaded successfully")
            self._write_shared_runtime_snapshot(
                str(path_obj),
                self.n_ctx,
                n_threads,
                self.n_gpu_layers,
                requested_n_gpu_layers=requested_n_gpu_layers,
                gpu_offload_supported=gpu_offload_supported,
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
