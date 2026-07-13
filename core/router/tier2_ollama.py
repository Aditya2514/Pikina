"""
Pikina OS — Tier 2 Ollama Intent Router
Routes unmatched commands to local Ollama LLM, retrieves context, and validates model proposed actions.
"""
import time
import json
import re
import requests
from core.eventbus.bus import EventBus
from core.validation.failure_classes import FailureClass
from core.validation.schema_check import validate_model_action
from core.context.retrieval import assemble_context
from core.registry.loader import CapabilityRegistry


class Tier2Router:
    """Invokes local Ollama model to translate natural language command to structured tool action."""
    def __init__(self, registry=None, model_name="llama3"):
        self.registry = registry or CapabilityRegistry()
        self.model_name = model_name
        self.bus = EventBus()
        self.url = "http://localhost:11434/api/generate"

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
        """Make HTTP POST call to local Ollama generate API with 15-second timeout."""
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0
            }
        }
        
        start_time = time.time()
        try:
            r = requests.post(self.url, json=payload, timeout=15.0)
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
            raise TimeoutError("Ollama connection timed out (15s)") from e
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
        # 1. Assemble context (4b)
        context_str = assemble_context(text, vs=None, kg=None)

        # 2. Gather available capability names, permission levels, descriptions, and parameter schemas
        capabilities = []
        for tool in self.registry.list_tools():
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

        except TimeoutError as te:
            return {"status": "error", "reason": "timeout", "failure_class": FailureClass.INFRASTRUCTURE, "message": str(te)}
        except json.JSONDecodeError as jde:
            return {"status": "error", "reason": "json_parse_failed", "failure_class": FailureClass.VALIDATION, "message": str(jde)}
        except Exception as e:
            return {"status": "error", "reason": "api_error", "failure_class": FailureClass.INFRASTRUCTURE, "message": str(e)}

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

        # 7. Execute capability
        tool = proposed_action["tool"]
        params = proposed_action.get("params", {})
        
        # Log execution
        self.bus.publish(
            topic="router.executing",
            payload={"tool": tool, "params": params, "raw": text, "tier": 2},
            provenance="model_output",
            permission_level=proposed_action.get("claimed_permission_level", 0),
        )

        result = self.registry.execute(tool, params)

        self.bus.publish(
            topic="router.result",
            payload={"tool": tool, "result": result, "tier": 2},
            provenance="model_output",
            permission_level=proposed_action.get("claimed_permission_level", 0),
        )

        return result
