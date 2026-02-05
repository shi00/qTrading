import asyncio
import json
import logging

import httpx
from openai import AsyncOpenAI

from data.review_manager import ReviewManager
from data.tushare_client import TushareClient
from utils.config_handler import ConfigHandler
from utils.log_decorators import log_async_operation

logger = logging.getLogger(__name__)


class AIClient:
    """
    Generic AI Client for OpenAI-compatible APIs (DeepSeek, Moonshot, etc.)
    """
    _instance = None

    def __new__(cls):
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
        self._initialized = True

    def _get_semaphore(self):
        """Get or create semaphore for current event loop"""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        # Create new semaphore if none exists or loop changed
        if self._semaphore is None or self._semaphore_loop != current_loop:
            concurrency = ConfigHandler.get_ai_concurrency()
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

    @log_async_operation(operation_name="classify_news", performance_threshold_ms=5000)
    async def classify_news(self, text: str) -> dict:
        """
        Classify news text using LLM.
        """
        if not self.client:
            return None

        model = self.config.get_setting('ai_model_name')
        if not model:
            logger.error("[AI] Configuration Error: 'ai_model_name' is not configured.")
            return None

        system_prompt = """
        You are a financial news assistant.
        Classify the input news into ONE category: [Policy, International, Macro, Market, Stock].
        Assess sentiment: [Positive, Neutral, Negative].
        
        Categories:
        - Policy: Government regulations, central bank, official announcements.
        - International: Global markets, geopolitics, exchange rates, US Fed.
        - Macro: GDP, CPI, PMI, Economy data.
        - Market: Broad market trends, sector rotation, fund flows.
        - Stock: Specific company news.

        Output JSON:
        {
            "category": "Policy",
            "sentiment": "Neutral",
            "emoji": "🏛️" 
        }
        Map Emojis: Policy=🏛️, International=🌍, Macro=📈, Market=📊, Stock=🏢.
        """

        try:
            async with self._get_semaphore():
                # 3s timeout for classification (fail fast strategy)
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": text[:500]}  # Truncate to save tokens
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.1
                    ),
                    timeout=3.0
                )
                return json.loads(response.choices[0].message.content)
        except asyncio.TimeoutError:
            logger.warning("[AI] Classification timeout (>3s), using fallback")
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
