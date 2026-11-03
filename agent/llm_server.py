"""One local model for every RedZone job: mlx-community/Qwen3-1.7B-4bit loaded ONCE, with one
small LoRA adapter per job (agent/model/<name>/: watchdog, game-reasoner, ...) swapped in per
request. Never two copies of the base: ~1 GB of weights plus a short KV cache, under the ~1.5 GB
budget for all local models together.

    .venv-agent/bin/python -m agent.llm_server            # its own process, 127.0.0.1:8779
    (or hosted inside the watchdog: agent.llm_server.ensure_server() starts it in a thread when
     nothing answers on the port, so `--no-model` off is the whole install)

HTTP, localhost only (REDZONE_LLM_PORT to change the port):
  POST /generate  {adapter, messages, max_tokens, thinking?}         -> {text, ms}
  POST /score     {adapter, messages, prefix, choices, thinking?}    -> {logprobs: {choice: float}, ms}
                  one prefill of the chat prompt + prefix, then each choice's summed log-prob
                  (its tokens fed on the same cache, trimmed back after each); no decoding
  GET  /health                                                       -> {loaded, adapter, adapters, rss_mb, peak_rss_mb}
  POST /keepalive {live_games}                                       -> {ok}
The base is loaded on the first request and unloaded after IDLE_S (10 min) with neither a
request nor a keepalive reporting live games. Adapter swaps only exchange LoRA wrappers around
the same base layers (no weight copies). Requests run one at a time (MLX isn't re-entrant).
Resource governor hooks (redzone.governor, when present): memory limits before loading, a
permission check before each generation, the MLX cache freed when idle.
"""
from __future__ import annotations

import json
import logging
import os
import resource
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("redzone.llm")

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "model"
BASE = os.environ.get("REDZONE_LLM_BASE", "mlx-community/Qwen3-1.7B-4bit")
PORT = int(os.environ.get("REDZONE_LLM_PORT", "8779"))
IDLE_S = 600.0
MAX_PROMPT_TOKENS = 3072      # caps the KV cache (~0.1 GB at this size) whatever a caller sends
MAX_NEW_TOKENS = 512


def _rss_mb() -> tuple[float, float]:
    """(current, peak) resident memory of this process in MB."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 1e6 if sys.platform == "darwin" else peak / 1e3     # bytes on macOS, KB on Linux
    cur_mb = peak_mb
    try:
        with open("/proc/self/statm") as f:
            cur_mb = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except OSError:
        try:
            import subprocess
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True)
            cur_mb = int(out.stdout.strip()) / 1e3
        except Exception:
            pass
    return round(cur_mb, 1), round(peak_mb, 1)


def _governor():
    try:
        from redzone import governor
        return governor
    except Exception:
        return None


class Engine:
    """The loaded base plus its adapters. Not thread-safe: callers hold self.lock."""

    def __init__(self, base: str = BASE, models: Path = MODELS):
        self.base, self.models = base, models
        self.lock = threading.Lock()
        self.model = self.tok = None
        self.originals: dict[tuple[int, str], object] = {}     # (layer, key) -> base linear
        self.adapters: dict[str, dict[tuple[int, str], object]] = {}
        self.active = ""
        self.last_used = self.last_live = 0.0
        self._gov = None

    # ---- loading ---------------------------------------------------------------------------
    def load(self) -> None:
        if self.model is not None:
            return
        gov = _governor()
        if gov is not None:
            try:
                gov.apply_mlx_limits(gov.load_options()["models_mb"])
            except Exception as e:
                log.info("llm: governor limits not applied: %s", e)
        from mlx_lm import load
        t = time.time()
        self.model, self.tok = load(self.base)
        self.originals, self.adapters, self.active = {}, {}, ""
        log.info("llm: loaded %s in %.1f s (rss %s MB)", self.base, time.time() - t, _rss_mb()[0])

    def unload(self) -> None:
        self.model = self.tok = None
        self.originals, self.adapters, self.active = {}, {}, ""
        import gc
        gc.collect()
        self.free_cache()
        log.info("llm: unloaded (rss %s MB)", _rss_mb()[0])

    def free_cache(self) -> None:
        gov = _governor()
        try:
            if gov is not None and hasattr(gov, "free_mlx_cache"):
                gov.free_mlx_cache()
            else:
                import mlx.core as mx
                mx.clear_cache()
        except Exception:
            pass

    def _layers(self):
        return self.model.layers

    def _get(self, layer: int, key: str):
        m = self._layers()[layer]
        for part in key.split("."):
            m = m[part] if isinstance(m, dict) else getattr(m, part)
        return m

    def _set(self, layer: int, key: str, module) -> None:
        from mlx.utils import tree_unflatten
        self._layers()[layer].update_modules(tree_unflatten([(key, module)]))

    def _adapter(self, name: str) -> dict:
        """LoRA wrappers for one adapter, built once around the base layers."""
        if name in self.adapters:
            return self.adapters[name]
        import mlx.core as mx
        from mlx_lm.tuner.lora import LoRALinear
        d = self.models / name
        if not name or "/" in name or ".." in name or not (d / "adapters.safetensors").exists():
            raise FileNotFoundError(f"no adapter {name!r} in {self.models}")
        cfg = json.loads((d / "adapter_config.json").read_text())
        lp = cfg["lora_parameters"]
        n = len(self._layers())
        layers = range(n - int(cfg["num_layers"]), n)
        keys = lp.get("keys") or ["self_attn.q_proj", "self_attn.v_proj"]
        weights = mx.load(str(d / "adapters.safetensors"))
        wrappers = {}
        self.activate("")                                  # wrap the true base layers
        for i in layers:
            for k in keys:
                if f"model.layers.{i}.{k}.lora_a" not in weights:
                    continue
                base = self.originals.setdefault((i, k), self._get(i, k))
                lora = LoRALinear.from_base(base, r=int(lp["rank"]), scale=float(lp["scale"]), dropout=0.0)
                lora.lora_a = weights[f"model.layers.{i}.{k}.lora_a"]
                lora.lora_b = weights[f"model.layers.{i}.{k}.lora_b"]
                wrappers[(i, k)] = lora
        if not wrappers:
            raise ValueError(f"adapter {name!r} has no LoRA weights")
        self.adapters[name] = wrappers
        return wrappers

    def activate(self, name: str) -> None:
        """Put one adapter's wrappers in place ("" = the plain base), restoring all others."""
        if name == self.active:
            return
        want = self._adapter(name) if name else {}
        for (i, k), base in self.originals.items():
            self._set(i, k, want.get((i, k), base))
        for (i, k), lora in want.items():
            self._set(i, k, lora)
        self.active = name

    # ---- requests -----------------------------------------------------------------------------
    def _prompt(self, messages: list[dict], thinking: bool) -> str:
        return self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                            enable_thinking=bool(thinking))

    def _check(self, ids: list[int]) -> None:
        if len(ids) > MAX_PROMPT_TOKENS:
            raise ValueError(f"prompt is {len(ids)} tokens; the limit is {MAX_PROMPT_TOKENS}")
        gov = _governor()
        if gov is not None and hasattr(gov, "Governor"):
            try:
                g = self._gov or gov.Governor()
                self._gov = g
                ok = g.allow("llm")
            except Exception:
                ok = True
            if not ok:
                raise RuntimeError("busy: the resource governor is holding the local model back")

    def generate(self, adapter: str, messages: list[dict], max_tokens: int = 96, thinking: bool = False) -> dict:
        from mlx_lm import generate
        t = time.time()
        self.load()
        self.activate(adapter or "")
        prompt = self._prompt(messages, thinking)
        self._check(self.tok.encode(prompt, add_special_tokens=False))
        text = generate(self.model, self.tok, prompt=prompt,
                        max_tokens=max(1, min(int(max_tokens), MAX_NEW_TOKENS)), verbose=False)
        self.last_used = time.time()
        return {"text": text, "ms": round((time.time() - t) * 1000)}

    def score(self, adapter: str, messages: list[dict], prefix: str, choices: list[str],
              thinking: bool = False) -> dict:
        import mlx.core as mx
        from mlx_lm.models.cache import make_prompt_cache
        t = time.time()
        self.load()
        self.activate(adapter or "")
        ids = self.tok.encode(self._prompt(messages, thinking) + (prefix or ""), add_special_tokens=False)
        self._check(ids)
        cache = make_prompt_cache(self.model)
        last = self.model(mx.array([ids]), cache=cache)[0, -1]
        out = {}
        for c in choices:
            toks = self.tok.encode(c, add_special_tokens=False)
            if not toks:
                out[c] = 0.0
                continue
            lp = float(_logsoftmax(last)[toks[0]])
            if len(toks) > 1:
                logits = self.model(mx.array([toks[:-1]]), cache=cache)[0]
                ls = _logsoftmax(logits)
                lp += float(sum(ls[j, toks[j + 1]] for j in range(len(toks) - 1)))
                for layer_cache in cache:
                    layer_cache.trim(len(toks) - 1)
            out[c] = round(lp, 4)
        self.last_used = time.time()
        return {"logprobs": out, "ms": round((time.time() - t) * 1000)}

    def health(self) -> dict:
        cur, peak = _rss_mb()
        return {"loaded": self.model is not None, "base": self.base, "adapter": self.active,
                "adapters": sorted(p.name for p in self.models.glob("*") if (p / "adapters.safetensors").exists()),
                "rss_mb": cur, "peak_rss_mb": peak}

    def idle_check(self, now: float | None = None) -> bool:
        """Unload after IDLE_S with no request and no keepalive reporting live games."""
        now = now or time.time()
        if self.model is not None and now - max(self.last_used, self.last_live) > IDLE_S:
            with self.lock:
                self.unload()
            return True
        if self.model is not None and now - self.last_used > 60:
            self.free_cache()
        return False


def _logsoftmax(x):
    import mlx.core as mx
    x = x.astype(mx.float32)
    return x - mx.logsumexp(x, axis=-1, keepdims=True)


def make_handler(engine: Engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, engine.health())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                if self.path == "/keepalive":
                    if int(body.get("live_games") or 0) > 0:
                        engine.last_live = time.time()
                    return self._send(200, {"ok": True})
                if self.path not in ("/generate", "/score"):
                    return self._send(404, {"error": "not found"})
                msgs = body.get("messages")
                if not isinstance(msgs, list):
                    return self._send(400, {"error": "messages must be a list"})
                with engine.lock:
                    if self.path == "/generate":
                        r = engine.generate(str(body.get("adapter") or ""), msgs, int(body.get("max_tokens") or 96),
                                            bool(body.get("thinking")))
                    else:
                        ch = body.get("choices")
                        if not isinstance(ch, list) or not ch:
                            return self._send(400, {"error": "choices must be a non-empty list"})
                        r = engine.score(str(body.get("adapter") or ""), msgs, str(body.get("prefix") or ""),
                                         [str(c) for c in ch], bool(body.get("thinking")))
                self._send(200, r)
            except FileNotFoundError as e:
                self._send(404, {"error": str(e)})
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except RuntimeError as e:
                self._send(503, {"error": str(e)})
            except Exception as e:   # a bad request never takes the server down
                log.exception("llm: request failed")
                self._send(500, {"error": f"{type(e).__name__}: {e}"})
    return Handler


def serve(port: int = PORT, engine: Engine | None = None) -> ThreadingHTTPServer:
    """Start serving in background threads; returns the server (call .shutdown() to stop)."""
    engine = engine or Engine()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    srv.engine = engine
    threading.Thread(target=srv.serve_forever, name="llm-http", daemon=True).start()

    def reaper():
        while True:
            time.sleep(30)
            try:
                engine.idle_check()
            except Exception:
                log.exception("llm: idle check failed")
    threading.Thread(target=reaper, name="llm-idle", daemon=True).start()
    return srv


def is_up(port: int = PORT, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


_hosted: ThreadingHTTPServer | None = None


def ensure_server(port: int = PORT) -> None:
    """Make sure one server answers on the port, hosting it in this process if none does."""
    global _hosted
    if _hosted is None and not is_up(port):
        _hosted = serve(port)


def call(path: str, body: dict, port: int = PORT, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    srv = serve()
    log.info("llm: serving on 127.0.0.1:%s (base %s, adapters in %s)", PORT, BASE, MODELS)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
