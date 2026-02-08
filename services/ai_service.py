import asyncio
import json
import logging
import threading

import httpx
from openai import AsyncOpenAI

from data.review_manager import ReviewManager
from data.tushare_client import TushareClient
from utils.config_handler import ConfigHandler
from utils.log_decorators import log_async_operation
from services.local_model_manager import LocalModelManager

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
        
        # Locks for Local Model
        self._setup_lock = asyncio.Lock() # Protect auto-init
        
        self._initialized = True

    def _get_semaphore(self):
        """Get or create semaphore for current event loop"""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        # Create new semaphore if none exists or loop changed
        if self._semaphore is None or self._semaphore_loop != current_loop:
            # Enforce minimum concurrency of 1 to prevent deadlock
            # Default to 5 if config is missing/zero/negative
            raw_val = ConfigHandler.get_ai_concurrency()
            concurrency = max(1, int(raw_val)) if raw_val else 5
            self._semaphore = asyncio.Semaphore(concurrency)
            self._semaphore_loop = current_loop

        return self._semaphore

    def _setup_client(self):
        """
        Initialize OpenAI client from settings.
        STRICT MODE: Requires explicit 'ai_api_key' and 'ai_base_url' in config.
        """
        ai_cfg = ConfigHandler.get_ai_config()
        api_key = ai_cfg.get('ai_api_key')
        base_url = ai_cfg.get('ai_base_url')

        if not api_key:
            logger.warning("[AI] API Key not found. AI features will be disabled.")
            self.client = None
            return

        if not base_url:
            logger.error("[AI] Configuration Error: 'ai_base_url' is mandatory. No default fallback.")
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
        logger.info(f"[AI] Client initialized with Base URL: {base_url}")

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
        self._local_model_attempted = False
        # We don't unset _local_llama immediately to avoid crashing running threads
        # It will be overwritten on next _setup_local_model success.

    @log_async_operation(operation_name="analyze_stock", log_args=False, performance_threshold_ms=30000)
    async def analyze_stock(self, stock_info: dict, tech_info: dict, news_list: list, global_context="") -> dict:
        """
        Analyze a single stock using the LLM.
        Requires 'ai_model_name' to be configured.
        """
        if not self.client:
            return None

        model = ConfigHandler.get_setting('ai_model_name')
        if not model:
            logger.error("[AI] Configuration Error: 'ai_model_name' must be set in settings.")
            return {"error": "AI Model not configured", "score": 0}

        # Build Prompt
        # Convert dicts to XML-like string
        stock_xml = "\n".join([f"  {k}: {v}" for k, v in stock_info.items()])
        tech_xml = "\n".join([f"  {k}: {v}" for k, v in tech_info.items()])

        # Format news
        news_text = "\n".join([f"- {n.get('publish_time', '')[:10]} {n.get('title', '')}" for n in news_list[:5]])
        if not news_list:
            news_text = "No recent news found."

        # Fetch Concepts (On-demand)
        concepts_str = "None"
        try:
            ts_code = stock_info.get('ts_code')
            if ts_code:
                df_concept = TushareClient().get_concept_detail(ts_code=ts_code)
                if df_concept is not None and not df_concept.empty:
                    concepts = df_concept['concept_name'].tolist()[:8]  # Top 8 concepts
                    concepts_str = ", ".join(concepts)
        except Exception as e:
            logger.warning(f"[AI] Failed to fetch concepts: {e}")

        # Add concepts to stock_xml
        stock_xml += f"\n  Concepts: {concepts_str}"

        # Fetch Learning Context (Few-Shot)
        history_context = ""
        try:
            # We instantiate ReviewManager here. In a real app, maybe inject it.
            # But for now, simple instantiation is fine as it uses lightweight DB calls.
            rm = ReviewManager()
            history_context = await rm.get_learning_context()
        except Exception as e:
            logger.warning(f"[AI] Failed to fetch learning context: {e}")

        # Load System Prompt from Config (User Must Configure or ConfigHandler provides logic)
        system_prompt = self.config.get_ai_system_prompt()

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

        {self._safe_truncate(history_context, 3000)}

        <capital_flow>
          (Data not available yet, assume neutral)
        </capital_flow>

        <financials>
          (Data not available yet, assume neutral)
        </financials>
        """

        try:
            async with self._get_semaphore():
                # Corner case: API timeout protection (30s max)
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.3
                    ),
                    timeout=30.0  # 30 second timeout
                )

                content = response.choices[0].message.content
                return json.loads(content)

        except asyncio.TimeoutError:
            logger.error("[AI] Analysis timeout (>30s)")
            return {"error": "Analysis timeout", "score": 0}
        except json.JSONDecodeError as e:
            logger.error(f"[AI] Invalid JSON response: {e}")
            return {"error": "Invalid response format", "score": 0}
        except Exception as e:
            logger.error(f"[AI] Analysis failed: {e}")
            return {"error": str(e), "score": 0}

    async def _setup_local_model(self):
        """
        Ensure local model is loaded via Manager.
        """
        # Optimized: Delegate entirely to LocalModelManager
        # This prevents double-loading the model (once here, once in Manager)
        manager = await LocalModelManager.get_instance()
        
        # We don't need to explicitly call load_model here because 
        # manager.run_inference() performs auto-loading check.
        # But calling it here ensures "warm-up" behavior if strictly needed.
        # For now, let's just ensure the path is set in config (done by UI)
        # and checking if we need to trigger an initial load?
        
        # Actually, let's keep it simple. The Manager handles state.
        # If we want to pre-load, we can do:
        config_path = ConfigHandler.get_setting('local_model_path')
        if not config_path:
             return
             
        # Optional: Trigger load if not loaded?
        # await manager.load_model(config_path) 
        # But this might be redundant if run_inference does it.
        # However, to be consistent with method name "_setup", we should try loading.
        if not manager.get_loaded_model_path():
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
        # If L2 is present, just use L2 for conciseness as per user preference (or "L1-L2"? User originally wanted concise)
        # Reverting to my previous logic: "f"{l2}" if l2 else l1"
        final_category = f"{l2}" if l2 else l1
        
        # Store structured data back 
        raw_result['category'] = final_category
        # Ensure emoji/sentiment exist
        if 'emoji' not in raw_result: raw_result['emoji'] = '📰'
        if 'sentiment' not in raw_result: raw_result['sentiment'] = 'Neutral'
        
        return raw_result

    @log_async_operation(operation_name="classify_news", performance_threshold_ms=5000)
    async def classify_news(self, text: str) -> dict:
        """
        Classify news text using Local LLM (Preferred) or Cloud LLM (Fallback).
        Optimized for Qwen2.5 1.5B with Few-Shot prompting details in Config.
        """
        # Prompt from Config (Content Only)
        system_instruction = self.config.get_ai_news_prompt()

        # 1. Try Local Model first
        await self._setup_local_model()
        
        manager = await LocalModelManager.get_instance()
        if manager.get_loaded_model_path():
            try:
                # Use Manager for safe inference
                # output_text is the string content directly
                output_text = await manager.run_inference(
                    prompt=text[:500],
                    system_prompt=system_instruction
                )
                
                # Check for empty output
                if not output_text:
                     logger.warning("[AI] Local model returned empty output.")
                else:
                    # Try to extract JSON
                    start = output_text.find('{')
                    if start != -1:
                        # Best Practice: Use raw_decode to parse ONE valid JSON object and ignore the rest
                        # This natively handles "Extra data" without try-catch hacks
                        try:
                            decoder = json.JSONDecoder()
                            json_str = output_text[start:]
                            result, end_idx = decoder.raw_decode(json_str)
                            
                            # Optional: Log if we ignored significant garbage?
                            if end_idx < len(json_str):
                                trailing = json_str[end_idx:].strip()
                                if len(trailing) > 5:
                                    logger.info(f"[AI] Ignored trailing garbage chars: {len(trailing)}")
                                    
                            return self._parse_news_result(result)
                        except json.JSONDecodeError as e:
                            logger.warning(f"[AI] Local model JSON decode failed: {e}")
                    else:
                        logger.warning(f"[AI] Local model output not JSON: {output_text}")
                        
            except (RuntimeError, ImportError) as e:
                logger.warning(f"[AI] Local model inference failed: {e}")
                # Fallback to cloud...
            except Exception as e:
                logger.error(f"[AI] Unexpected local inference error: {e}", exc_info=True)
                # Fallback to cloud...

        # 2. Fallback to Cloud API
        if not self.client:
            return None

        model = self.config.get_setting('ai_model_name')
        if not model:
            logger.error("[AI] Configuration Error: 'ai_model_name' is not configured.")
            return None

        # Use the SAME system prompt from config for Cloud Model
        # But pass it as "content", avoiding manual ChatML tags if using standard API
        
        # NOTE: If user put ChatML tags in the Config UI manually (against default), this might leak tags.
        # But we assume standard usage (Text content).
        
        try:
            # Enforce global 5s timeout for the entire operation
            async def _do_classify():
                async with self._get_semaphore():
                    response = await self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": text[:500]}
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.1
                    )
                    content = response.choices[0].message.content
                    return json.loads(content)

            raw_result = await asyncio.wait_for(_do_classify(), timeout=30.0)
            return self._parse_news_result(raw_result)

        except asyncio.TimeoutError:
            logger.warning("[AI] Classification global timeout (>30s), dropped")
            return None
        except Exception as e:
            logger.error(f"[AI] Classification failed: {e}")
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
            logger.error(f"[AI] Verification failed: {e}")
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
            logger.error(f"[AI] Test connection failed: {e}")
            raise e
