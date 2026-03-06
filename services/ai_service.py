import asyncio
import json
import logging
import threading

import httpx
from openai import AsyncOpenAI

from data.review_manager import ReviewManager
from services.local_model_manager import LocalModelManager
from utils.config_handler import ConfigHandler
from utils.log_decorators import log_async_operation, PerfThreshold

logger = logging.getLogger(__name__)


class AIService:
    """
    AI Service for OpenAI-compatible APIs (DeepSeek, Moonshot, etc.)
    Handles prompt engineering, dialogue management, and API interaction.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.config = ConfigHandler()
        self.client = None
        self._setup_client()
        self._semaphore = None  # Lazy creation to avoid cross-event-loop issues
        self._semaphore_loop = None  # Track which loop the semaphore belongs to

        # _setup_lock: lazy-initialized in property to avoid binding to wrong event loop
        self._setup_lock = None

        self._initialized = True

    async def _get_semaphore(self):
        """Get or create semaphore for current event loop"""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("[AIService] Semaphore | No running event loop, using DummySemaphore.")
            class DummySemaphore:
                async def __aenter__(self): return
                async def __aexit__(self, *args): return
            return DummySemaphore()

        # Create new semaphore if none exists on the current loop
        if not hasattr(current_loop, '_ai_semaphore'):
            # Enforce minimum concurrency of 1 to prevent deadlock
            raw_val = ConfigHandler.get_ai_max_concurrent_analysis()
            concurrency = max(1, int(raw_val)) if raw_val else 5
            setattr(current_loop, '_ai_semaphore', asyncio.Semaphore(concurrency))
            
        return getattr(current_loop, '_ai_semaphore')


    def _setup_client(self):
        """
        Initialize OpenAI client from settings.
        STRICT MODE: Requires explicit 'ai_api_key' and 'ai_base_url' in config.
        """
        ai_cfg = ConfigHandler.get_ai_config()
        api_key = ai_cfg.get('ai_api_key')
        base_url = ai_cfg.get('ai_base_url')

        if not api_key:
            logger.warning("[AIService] Config | ⚠️ API Key not found. AI features disabled.")
            self.client = None
            return

        if not base_url:
            logger.error("[AIService] Config | ❌ 'ai_base_url' is mandatory. No default fallback.")
            self.client = None
            return

        # Configure timeout and retry at SDK level
        # Total 30s timeout (matching analyze_stock), 5s connect timeout, max 2 retries
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
            max_retries=2
        )
        logger.info(f"[AIService] Init | ✅ Cloud client ready. base_url={base_url}")

    def _safe_truncate(self, text: str, max_len: int) -> str:
        """Safely truncate text to avoid token overflow"""
        if not text:
            return ""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "...(truncated)"

    async def reload_config(self):
        """Reload config when settings change"""
        self._setup_client()
        # Force semaphore rebuild with new concurrency
        self._semaphore = None
        self._semaphore_loop = None

        # Reset Local Model state to allow hot-swapping model path
        # Use simple assignment as we are in main loop single thread (mostly)
        # But to be safe with async, we just flag it. 
        # Ideally we should stop current inferences? Too complex. 
        # Just reset flags so next call re-loads.
        # We don't unset _local_llama immediately to avoid crashing running threads
        # It will be overwritten on next _setup_local_model success.

    async def _chat_completion(self, messages: list, model: str = None, provider: str = "cloud",
                               temperature: float = 0.3, timeout: float = 30.0, json_mode: bool = True,
                               on_chunk=None) -> dict:
        """
        Unified helper for Chat Completions (Cloud or Local).
        Args:
            messages: List of {"role":..., "content":...}
            model: Model name (optional, defaults to config)
            provider: 'cloud' or 'local'
            temperature: sampling temp
            timeout: timeout in seconds
            json_mode: whether to enforce JSON return
        Returns:
            dict: Parsed JSON content (or raw dict if non-json)
        Raises:
            Exception: on failure (caller should handle fallback)
        """
        response_content = ""

        # --- Local Provider ---
        if provider == "local":
            await self._setup_local_model()
            manager = await LocalModelManager.get_instance()

            system_prompt = next((m['content'] for m in messages if m['role'] == 'system'),
                                 "You are a helpful assistant.")
            user_prompt = next((m['content'] for m in messages if m['role'] == 'user'), "")

            if not manager.get_loaded_model_path():
                raise ValueError("Local model not loaded")

            response_content = await manager.run_inference(
                prompt=user_prompt,
                max_tokens=256,  # News classification only needs a small JSON (~60 tokens)
                temperature=temperature,
                system_prompt=system_prompt
            )

        # --- Cloud Provider ---
        else:
            if not self.client:
                raise ValueError("Cloud Client not initialized")

            model = model or ConfigHandler.get_setting('ai_model_name')
            if not model:
                raise ValueError("Model not configured")

            async with await self._get_semaphore():
                logger.debug(f"[AIService] Cloud | Invoking {model} ({len(messages)} messages)")
                logger.debug(f"[AIService] Cloud | Payload preview: {str(messages)[:200]}...")
                
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    # When streaming, response_format json_object is often unsupported by providers, so we disable it
                    response_format={"type": "json_object"} if json_mode and not on_chunk else None,
                    temperature=temperature,
                    timeout=timeout,  # Let the SDK handle timeout natively so retries work
                    stream=bool(on_chunk)
                )
                
                if on_chunk:
                    response_content = ""
                    reasoning_content = ""
                    async for chunk in response:
                        if not chunk.choices: continue
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            reasoning_content += delta.reasoning_content
                            on_chunk(delta.reasoning_content, True) # True for is_reasoning
                        if delta.content:
                            response_content += delta.content
                            on_chunk(delta.content, False)
                    # Guard: some models put everything in reasoning_content
                    if not response_content and reasoning_content:
                        response_content = reasoning_content
                else:
                    response_content = response.choices[0].message.content

        # --- Post-Processing (JSON Parsing) ---
        if json_mode:
            try:
                # 1. Cleaner: Try direct parse
                return json.loads(response_content)
            except json.JSONDecodeError:
                pass

            # 2. Heuristic Extraction
            try:
                start = response_content.find('{')
                if start != -1:
                    # Use raw_decode to extract ONLY the first valid JSON object
                    # ignoring trailing garbage (like "Extra data")
                    try:
                        obj, idx = json.JSONDecoder().raw_decode(response_content[start:])
                        return obj
                    except json.JSONDecodeError:
                        pass

                # 3. Fallback: Last Resort (rfind approach, but risky)
                end = response_content.rfind('}') + 1
                if end > start:
                    try:
                        json_str = response_content[start:end]
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass

            raise ValueError(f"Invalid JSON response: {response_content[:100]}...")

        return {"content": response_content}

    @log_async_operation(operation_name="analyze_stock", log_args=False, threshold_ms=PerfThreshold.AI_INFERENCE)
    async def analyze_stock(self, stock_info: dict, tech_info: dict, news_list: list, global_context="",
                            strategy_context: str = "", capital_flow_text: str = "", financials_text: str = "",
                            history_text: str = "", on_chunk=None, history_context: str = None) -> dict:
        """
        Analyze a single stock using the LLM (Cloud default, can support others).
        Requires 'ai_model_name' to be configured.
        """
        if not self.client:
            # Minimal check, though _chat_completion checks it too. 
            # But analyze_stock might return specific error dicts expected by Strategy.
            return None

        # Build Prompt
        # Convert dicts to XML-like string
        stock_xml = "\n".join([f"  {k}: {v}" for k, v in stock_info.items()])
        tech_xml = "\n".join([f"  {k}: {v}" for k, v in tech_info.items()])

        # Format news
        news_text = "\n".join([
            f"- [{n.get('source', '')}] {n.get('publish_time', '')[:10]} {n.get('title', '')}" 
            for n in news_list[:5]
        ])
        if not news_list:
            news_text = "No recent news found."

        # Fetch Concepts (Used cached if available)
        concepts_str = "None"
        try:
            # Check if concepts are already injected by Strategy (Preferred)
            injected_concepts = stock_info.get('concepts')

            if injected_concepts and isinstance(injected_concepts, list):
                # Use injected
                concepts = injected_concepts[:8]
                concepts_str = ", ".join(concepts)
            else:
                # Fallback for legacy support or when data is not fully synced
                concepts_str = "Data not synced"

        except Exception as e:
            logger.warning(f"[AIService] Analyze | ⚠️ Concepts processing failed: {e}")

        # Add concepts to stock_xml
        stock_xml += f"\n  Concepts: {concepts_str}"

        # Fetch Learning Context (Few-Shot) — skip if caller pre-fetched
        if history_context is None:
            try:
                rm = ReviewManager()
                history_context = await rm.get_learning_context()
            except Exception as e:
                logger.warning(f"[AIService] Analyze | ⚠️ Learning context fetch failed: {e}")
                history_context = ""

        # Load System Prompt from Config
        system_prompt = ConfigHandler.get_ai_system_prompt()

        # Capital flow and financials: use real data or fallback
        capital_flow_content = capital_flow_text if capital_flow_text else "(Data not available yet, assume neutral)"
        financials_content = financials_text if financials_text else "(Data not available yet, assume neutral)"

        user_prompt = f"""
        <stock_info>
        {stock_xml}
        </stock_info>

        <technical_indicators>
          {json.dumps(tech_info, ensure_ascii=False, indent=2)}
        </technical_indicators>

        <recent_news>
          {news_text}
        </recent_news>

        <global_context>
          {self._safe_truncate(global_context, 2000)}
        </global_context>

        <strategy_context>
          {self._safe_truncate(strategy_context, 1000) if strategy_context else 'No specific strategy context provided.'}
        </strategy_context>

        <recent_price_action>
          {history_text if history_text else "No historical price data available."}
        </recent_price_action>

        {self._safe_truncate(history_context, 3000)}

        <capital_flow>
          {capital_flow_content}
        </capital_flow>

        <financials>
          {financials_content}
        </financials>
        """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            # Analyze Stock uses Cloud by default as it requires high reasoning capability
            res = await self._chat_completion(messages, provider="cloud", timeout=120.0, json_mode=True, on_chunk=on_chunk)
            return res

        except asyncio.TimeoutError:
            logger.error(f"[AIService] Analyze | ❌ Timeout (120s exceeded)", exc_info=True)
            return {"error": "Analysis timeout", "score": 0}
        except Exception as e:
            logger.error(f"[AIService] Analyze | ❌ Top-level failure: {e}", exc_info=True)
            return {"error": str(e), "score": 0}

    async def _get_setup_lock(self):
        """Lazy-initialize the async lock dynamically per event loop to avoid cross-loop binding deadlocks."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("[AIService] SetupLock | No running event loop, using DummyLock.")
            class DummyLock:
                async def __aenter__(self): return
                async def __aexit__(self, *args): return
            return DummyLock()

        if not hasattr(current_loop, '_ai_setup_lock'):
            setattr(current_loop, '_ai_setup_lock', asyncio.Lock())
            
        return getattr(current_loop, '_ai_setup_lock')

    async def _setup_local_model(self):
        """
        Ensure local model is initialized via Manager.
        """
        lock = await self._get_setup_lock()
        async with lock:
            manager = await LocalModelManager.get_instance()

            # Ensure model is verified/loaded using config path
            config_path = ConfigHandler.get_setting('local_model_path')
            if config_path and not manager.get_loaded_model_path():
                await manager.load_model(config_path)

    def _parse_news_result(self, raw_result: dict) -> dict:
        """
        Helper to normalize news classification result.
        Handles the L1/L2 category logic to provide a clean 'category' string for UI.
        """
        # We combine L1 and L2 for category to display "金融核心-贵金属"
        l1 = raw_result.get('category_L1', '')
        l2 = raw_result.get('category_L2', '')
        # Prefer L2 if available, or L1-L2 combo
        final_category = f"{l2}" if l2 else l1

        # Store structured data back 
        raw_result['category'] = final_category
        # Ensure emoji/sentiment exist
        if 'emoji' not in raw_result: raw_result['emoji'] = '📰'
        if 'sentiment' not in raw_result: raw_result['sentiment'] = 'Neutral'

        return raw_result

    @log_async_operation(operation_name="classify_news", threshold_ms=PerfThreshold.AI_INFERENCE)
    async def classify_news(self, text: str) -> dict:
        """
        Classify news text using Local LLM (Preferred) or Cloud LLM (Fallback).
        """
        system_instruction = self.config.get_ai_news_prompt()
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": text[:500]}
        ]

        # 1. Try Local Model
        try:
            raw_result = await self._chat_completion(messages, provider="local", json_mode=True)
            result = self._parse_news_result(raw_result)
            logger.debug(f"[AIService] Classify | Local ✅ {result.get('category')} / {result.get('sentiment')}")
            return result
        except Exception as local_e:
            # Local failed (not configured, crash, etc.)
            # Log only if it wasn't just "not configured" (which is common)
            if "not installed" not in str(local_e) and "not configured" not in str(local_e):
                logger.warning(f"[AIService] Classify | ⚠️ Local failed, falling back to cloud: {local_e}")
            else:
                logger.warning(f"[AIService] Classify | ⚠️ Local model unavailable, falling back to cloud: {local_e}")

        # 2. Fallback to Cloud
        try:
            # Enforce global 5s timeout? The original code had per-call timeout.
            # _chat_completion has default 30s. classify used to wrap in wait_for 30s.
            # Inner cloud call had 30s timeout on client.
            # We will use 30s default.
            raw_result = await self._chat_completion(messages, provider="cloud", json_mode=True)
            result = self._parse_news_result(raw_result)
            logger.debug(f"[AIService] Classify | Cloud ✅ {result.get('category')} / {result.get('sentiment')}")
            return result
        except Exception as e:
            logger.error(f"[AIService] Classify | ❌ All providers failed: {e}", exc_info=True)
            return None

    async def verify_connection(self) -> bool:
        """
        Verify API connection by sending a minimal request.
        """
        if not self.client:
            return False

        try:
            model = self.config.get_setting('ai_model_name')
            if not model:
                raise ValueError("AI Model not configured")

            # Minimal request to test auth
            await self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=1
            )
            return True
        except Exception as e:
            logger.error(f"[AIService] Verify | ❌ Connection verification failed: {e}", exc_info=True)
            raise e

    @staticmethod
    async def test_connection(api_key: str, base_url: str, model: str) -> bool:
        """
        Static method to test connection with provided credentials (without saving).
        """
        if not api_key:
            raise ValueError("API Key is empty")

        try:
            # Create a temporary client with same timeout config
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=httpx.Timeout(10.0, connect=5.0),  # Shorter for testing
                max_retries=1  # Less retries for testing
            )

            # Simple test request
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=1
            )
            return True
        except Exception as e:
            logger.error(f"[AIService] TestConn | ❌ Test connection failed: {e}", exc_info=True)
            raise e
