"""
Pikina OS — Tier 2 Ollama Intent Router
Routes unmatched commands to local Ollama LLM, retrieves context, and validates model proposed actions.
"""
import os
import re
import time
import json
import threading
import requests
from pikina.eventbus.bus import EventBus
from pikina.validation.failure_classes import FailureClass
from pikina.validation.schema_check import validate_model_action
from pikina.registry.loader import CapabilityRegistry
from pikina.registry.executor import execute_action

# Defaults; each can be overridden from .env (read when a router is created,
# so values loaded from .env after import still apply).
DEFAULT_URL       = "http://localhost:11434"
DEFAULT_MODEL     = "llama3"
DEFAULT_TIMEOUT   = 30.0
DEFAULT_KEEPALIVE = "30m"  # keep weights in RAM between commands
# Above this many tools, only the most relevant ones are sent to the model (prompt budget).
MAX_PROMPT_TOOLS = 25

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"a", "an", "the", "to", "of", "and", "or", "for", "in", "on", "at", "my", "me", "i",
              "is", "it", "be", "by", "with", "can", "you", "please", "some", "something", "do", "that", "this"}


class Tier2Unavailable(RuntimeError):
    """Ollama is not running / not reachable."""


def _stems(text: str) -> set:
    """Crude stemming: lowercase word prefixes, so 'launch'/'launches', 'task'/'tasks' match."""
    return {w[:5] for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1}


def select_relevant_tools(command: str, tools: list, limit: int) -> list:
    """
    Keep the `limit` tools whose name/description share the most word stems with
    the command. Ties keep registry order. Only used when the registry is large,
    because a changing tool list defeats Ollama's prompt-prefix cache.
    """
    if len(tools) <= limit:
        return tools
    wanted = _stems(command)
    scored = []
    for idx, t in enumerate(tools):
        haystack = _stems(t["tool"].replace(".", " ").replace("_", " ") + " " + t.get("description", ""))
        scored.append((-len(wanted & haystack), idx, t))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [t for _, _, t in scored[:limit]]


class Tier2Router:
    """Invokes local Ollama model to translate natural language command to structured tool action."""
    def __init__(self, registry=None, model_name=None, base_url=None, timeout=None):
        self.registry = registry or CapabilityRegistry()
        self.model_name = model_name or os.getenv("PIKINA_OLLAMA_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.getenv("PIKINA_OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        self.url = f"{self.base_url}/api/generate"
        self.timeout = timeout if timeout is not None else float(os.getenv("PIKINA_OLLAMA_TIMEOUT", DEFAULT_TIMEOUT))
        self.keep_alive = os.getenv("PIKINA_OLLAMA_KEEP_ALIVE", DEFAULT_KEEPALIVE)
        self.max_prompt_tools = int(os.getenv("PIKINA_TIER2_MAX_TOOLS", 0)) or None
        self.bus = EventBus()

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/api/tags", timeout=2).status_code == 200
        except Exception:
            return False

    def warm_up(self) -> bool:
        """
        Load the model into memory ahead of the first command. A cold load of an
        8B model takes 10–30 s, which is what caused the Phase 4e timeouts.
        """
        try:
            r = requests.post(self.url, json={"model": self.model_name, "prompt": "",
                                              "keep_alive": self.keep_alive}, timeout=120)
            return r.status_code == 200
        except Exception:
            return False

    def warm_up_async(self) -> None:
        def _go():
            if self.is_available():
                ok = self.warm_up()
                print(f"[Tier2] Ollama model '{self.model_name}' {'loaded' if ok else 'failed to load'}.")
            else:
                print(f"[Tier2] Ollama not reachable at {self.base_url} — Tier 2 disabled until it is.")
        threading.Thread(target=_go, name="Tier2Warmup", daemon=True).start()

    def _clean_response(self, text: str) -> str:
        """Extract the JSON substring between the first '{' and the last '}'."""
        text = text.strip()
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            return text[start:end+1]
        return text

    def _format_tool_parameters(self, manifest: dict) -> str:
        """Helper to format tool parameter schemas into a compact string representation."""
        schema = manifest.get("params_schema", {})
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if not properties:
            return "    Parameters: None"
        
        lines = ["  Parameters:"]
        for name, prop in properties.items():
            p_type = prop.get("type", "string")
            req = "required" if name in required else "optional"
            desc = prop.get("description", "")
            lines.append(f"    - {name} ({p_type}, {req}): {desc}")
        return "\n".join(lines)

    def _call_ollama(self, prompt: str) -> str:
        """POST to Ollama's generate API (JSON mode, bounded output, model kept warm)."""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "format": "json",            # constrain decoding to valid JSON
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0.0,
                "num_predict": 200,      # a tool call is ~60 tokens; stop runaway prose
            },
        }

        start_time = time.time()
        try:
            r = requests.post(self.url, json=payload, timeout=self.timeout)
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Log latency to the EventBus
            self.bus.publish(
                topic="model.latency",
                payload={"model": self.model_name, "latency_ms": latency_ms},
                provenance="system"
            )
            
            if r.status_code != 200:
                raise RuntimeError(f"Ollama API returned HTTP status {r.status_code}")
                
            response_json = r.json()
            return response_json.get("response", "").strip()
            
        except requests.Timeout as e:
            # Publish infrastructure failure event
            self.bus.publish(
                topic="router.tier2_error",
                payload={"error": "Ollama call timed out"},
                provenance="system",
                failure_class=FailureClass.INFRASTRUCTURE
            )
            raise TimeoutError(f"Ollama did not answer within {self.timeout:.0f}s") from e
        except requests.ConnectionError as e:
            raise Tier2Unavailable(f"Ollama is not reachable at {self.base_url}") from e
        except Exception as e:
            self.bus.publish(
                topic="router.tier2_error",
                payload={"error": str(e)},
                provenance="system",
                failure_class=FailureClass.INFRASTRUCTURE
            )
            raise RuntimeError(f"Ollama API request failed: {e}") from e

    def route(self, text: str) -> dict:
        """
        Retrieves context, formats prompt, calls Ollama, and validates/executes the response.
        Returns a result dict.
        """
        # 1. Assemble context (4b). Memory is optional: if the embedding model or
        #    stores are unavailable, route without it instead of crashing.
        try:
            from pikina.context.retrieval import assemble_context
            context_str = assemble_context(text, vs=None, kg=None) or "(none)"
        except Exception as exc:
            self.bus.publish(topic="router.tier2_context_error", payload={"error": str(exc)},
                             provenance="system", failure_class=FailureClass.INFRASTRUCTURE)
            context_str = "(memory unavailable)"

        # 2. Gather capability names, permission levels, descriptions and parameter schemas
        tools = select_relevant_tools(text, self.registry.list_tools(), self.max_prompt_tools or MAX_PROMPT_TOOLS)
        capabilities = []
        for tool in tools:
            tool_name = tool["tool"]
            try:
                manifest = self.registry.get_manifest(tool_name)
                level = manifest.get("permission_level", 0)
                param_desc = self._format_tool_parameters(manifest)
            except Exception:
                level = 0
                param_desc = "  Parameters: None"
            capabilities.append(
                f"- {tool['tool']} [permission_level={level}]: {tool['description']}\n{param_desc}"
            )
        capabilities_str = "\n".join(capabilities)

        # 3. Format primary prompt
        prompt = (
            f"You are a command router. Map the user's natural language command to exactly one tool.\n\n"
            f"RULES:\n"
            f"- You MUST set claimed_permission_level to exactly the permission_level shown in brackets for the chosen tool.\n"
            f"- Do not invent tools not listed below.\n"
            f"- Do not add quotes around parameter values unless they are part of the value itself.\n"
            f"- If the user's request cannot be mapped to ANY listed tool (e.g. jokes, small talk, impossible requests), "
            f"respond with exactly: {{\"tool\": \"no_match\", \"params\": {{}}, \"claimed_permission_level\": 0, \"provenance\": \"model_output\"}}\n"
            f"- Respond ONLY with a single JSON object — no prose, no markdown, no explanation.\n\n"
            f"Available capabilities:\n{capabilities_str}\n\n"
            f"Memory context:\n{context_str}\n\n"
            f"User command: \"{text}\"\n\n"
            f"Required JSON schema (fill in the blanks, copy permission_level exactly from the tool listing):\n"
            f"{{\"tool\": \"<tool_name>\", \"params\": {{...}}, "
            f"\"claimed_permission_level\": <copy exact integer from tool listing>, \"provenance\": \"model_output\"}}"
        )

        try:
            # 4. First attempt
            response_text = self._call_ollama(prompt)
            clean_text = self._clean_response(response_text)
            
            try:
                proposed_action = json.loads(clean_text)
            except json.JSONDecodeError:
                # 5. Stricter retry attempt (exactly 1 retry allowed)
                self.bus.publish(
                    topic="router.tier2_retry",
                    payload={"raw_response": response_text},
                    provenance="system"
                )
                retry_prompt = (
                    f"{prompt}\n\n"
                    f"Your previous response was not valid JSON: \"{response_text}\"\n"
                    f"Stricter Instruction: respond with valid JSON ONLY, matching the requested schema. "
                    f"No prose, no markdown fences, no formatting other than pure JSON."
                )
                response_text = self._call_ollama(retry_prompt)
                clean_text = self._clean_response(response_text)
                proposed_action = json.loads(clean_text)

        except Tier2Unavailable as ue:
            return {"status": "no_match", "reason": "tier2_unavailable", "message":
                    f"No Tier 1 command matched and the local model is offline ({ue}). "
                    "Start Ollama or rephrase, e.g. 'open notepad', 'add todo ...', 'find file ...'."}
        except TimeoutError as te:
            return {"status": "error", "reason": "timeout", "failure_class": FailureClass.INFRASTRUCTURE, "message": str(te)}
        except json.JSONDecodeError as jde:
            return {"status": "error", "reason": "json_parse_failed", "failure_class": FailureClass.VALIDATION, "message": str(jde)}
        except Exception as e:
            return {"status": "error", "reason": "api_error", "failure_class": FailureClass.INFRASTRUCTURE, "message": str(e)}

        if not isinstance(proposed_action, dict):
            return {"status": "error", "reason": "json_parse_failed", "failure_class": FailureClass.VALIDATION,
                    "message": "Model returned JSON that is not an object."}

        # 6. Handle explicit no_match declaration from model
        if proposed_action.get("tool") == "no_match":
            return {
                "status": "no_match",
                "message": "I don't have a capability for that. Try asking me to open an app, manage tasks, set reminders, find files, or do math.",
            }

        # 7. Pass parsed action into the Extended AVL (4a)
        # We start with retries_so_far = 0 for the first submission
        is_valid, fc, reason = validate_model_action(proposed_action, self.registry, retries_so_far=0)
        
        if not is_valid:
            # Return validation details to the user/caller
            return {
                "status": "error",
                "reason": "validation_failed",
                "failure_class": fc,
                "validation_reason": reason,
                "proposed_action": proposed_action
            }

        # 8. Execute through the shared executor. Consent was already obtained by the
        #    AVL above for anything that needs it, so the gate is not repeated here.
        return execute_action(
            self.registry, proposed_action["tool"], proposed_action.get("params", {}),
            raw=text, tier=2, provenance="model_output", gate=False,
        )
