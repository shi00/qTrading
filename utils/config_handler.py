import json
import os
import logging
import config
from utils.security_utils import SecurityManager
from readerwriterlock import rwlock

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join(config.APP_ROOT, "user_settings.json")

DEFAULT_AI_PROMPT = """# A股智能分析系统提示词 (System Prompt)

## 1. 角色定调 (Role)
你是一名拥有20年实战经验的中国A股资深量化基金经理。你的核心投资哲学是：**“政策指方向，资金选个股，技术找买点，财报排地雷”**。

## 2. 硬性交易约束 (Hard Constraints)
你必须严格遵守以下 `<rules>`，任何建议如有违背将被视为重大事故：

<rules>
1.  **T+1 铁律**: A股当日买入不可卖出。必须评估“隔夜风险”，严禁建议日内回转（T+0）。
2.  **涨跌幅限制**:
    *   主板: ±10%
    *   科创板(688)/创业板(300): ±20%
    *   ST/*ST: ±5%
    *   (注意: 新股上市前5日无限制)
3.  **交易时段**: 14:57-15:00 为收盘集合竞价，**不可撤单**。
4.  **风控红线**: 严禁推荐 *ST、立案调查中、年报非标、商誉占净资产比重过高的公司。
5.  **信源验证 (Source Verification)**:
    *   **权威信源**: 央视新闻(CCTV)、新华社、证监会/交易所官网、上市公司公告。 -> **权重 1.2x**
    *   **可信信源**: 财联社、证券时报、中国基金报。 -> **权重 1.0x**
    *   **噪音/传闻**: "小道消息"、"网传"、"据外媒"。 -> **必须降权 (Confidence -20)**，除非有其他信源交叉验证。
</rules>

## 3. 分析维度与权重 (Analysis Framework)
请按以下权重进行加权分析：

<dimensions>
*   **政策与宏观 (30%)**: "听党话，跟党走"。分析央行(LPR/降准)、国常会(新质生产力)、证监会监管风向。
*   **全球映射 (Global Mapping)**: "美股映射 A 股"。必须分析 `<global_context>` 中的美股/港股表现，结合 `<stock_info>.concepts` 判断。
    *   *Example*: 若概念含“特斯拉”，且美股TSLA大涨，则强烈看多。
*   **资金博弈 (25%)**: 资金即动能。重点分析 `<capital_flow>` 中的北向资金和游资龙虎榜。
*   **技术面 (20%)**: "千金难买牛回头"。在 `<technical_indicators>` 中寻找均线多头排列后的缩量回调买点。
*   **基本面 (15%)**: 业绩防雷。关注 `<financials>` 中的营收增速、PEG及商誉风险。
*   **情绪面 (10%)**: 感受市场温度。结合 `<recent_news>` 判断是贪婪还是恐慌。
</dimensions>

## 4. 输出规范 (Output Schema)
用户将提供若干 XML 数据块。请分析后返回严格的 JSON 格式：

```json
{
  "thinking": "<在此处详细输出你的思考过程/推理链 (Chain of Thought)，包含对每个维度的逐步分析>",
  "score": <0-100, 整数>,
  "decision": "<买入 / 增持 / 持有 / 减持 / 卖出 / 观望>",
  "rules_check": {
    "compliant": <true/false>,
    "remarks": "<合规性检查备注，如：科创板波动大需轻仓>",
    "source_reliability": "<High/Medium/Low - 必须基于信源评级>"
  },
  "analysis": {
    "policy_driver": "<政策面摘要>",
    "global_mapping": "<全球/美股映射逻辑，如：TSLA大涨利好拓普>",
    "capital_flow": "<资金面摘要>",
    "technical_signal": "<技术面摘要>",
    "fundamental_quality": "<基本面摘要>"
  },
  "risk_warning": "<一句话核心风险>",
  "summary": "<100字以内的专业投资建议，风格冷静客观>"
}
```"""

class ConfigHandler:
    _config_cache = None
    _last_load_time = 0
    _lock = rwlock.RWLockFair()

    @staticmethod
    def load_config():
        """Load config with Read Lock"""
        with ConfigHandler._lock.gen_rlock():
            # Use simple caching: if cache exists, return it.
            # We rely on save_config to update cache. 
            if ConfigHandler._config_cache is not None:
                 return ConfigHandler._config_cache.copy()

            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                        ConfigHandler._config_cache = json.load(f)
                        return ConfigHandler._config_cache.copy()
                except Exception:
                    return {}
            return {}

    @staticmethod
    def save_config(config_data):
        """Save config with Write Lock"""
        try:
            with ConfigHandler._lock.gen_wlock():
                # Note: We must call load_config() inside here carefully? 
                # Be careful: load_config uses read lock. 
                # RWLockFair usually allows reentrancy if we hold write lock and want read lock?
                # Check library docs. Usually yes. 
                # BUT to be safe and efficient: we just access cache directly or reload raw if needed.
                # Actually, standard pattern: 
                
                # We need to read current state to merge updates
                # Since we hold Write Lock, no one else can write.
                
                # Check if cache is trusted
                current_config = {}
                if ConfigHandler._config_cache is not None:
                    current_config = ConfigHandler._config_cache
                elif os.path.exists(CONFIG_FILE):
                    try:
                        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                            current_config = json.load(f)
                    except:
                        pass
                
                # Update
                current_config.update(config_data)
                
                # Write to disk
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(current_config, f, indent=4)
                
                # Update cache
                ConfigHandler._config_cache = current_config
                return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False


    @staticmethod
    def _is_valid_tushare_format(token):
        """
        Check if a string looks like a valid Tushare token.
        Tushare tokens are typically 32-56 character hex strings.
        Encrypted tokens are Base64 and usually much longer.
        """
        if not token or not isinstance(token, str):
            return False
        # Crude but effective check: Tushare tokens are hex, encrypted are base64 (with potential +/ symbols)
        # And length check. 
        if len(token) > 60: # Encrypted tokens are usually longer due to overhead
            return False
        # Optional: check for hex characters only?
        # Let's keep it loose to avoid breaking unusual but valid tokens, 
        # but strict enough to reject obvious ciphertext.
        return True

    @staticmethod
    def get_token():
        config = ConfigHandler.load_config()
        token = config.get("ts_token", "")
        
        if not token:
            return ""
            
        # Try to decrypt
        try:
            return SecurityManager.decrypt_data(token)
        except Exception:
            # Failed to decrypt. 
            # This happens if:
            # 1. It's a legacy plain text token (we should encrypt it).
            # 2. It's an encrypted token but the key is wrong/missing (we should discard it).
            
            if ConfigHandler._is_valid_tushare_format(token):
                # Looks like a valid plain text token, let's migrate it
                try:
                    SecurityManager.get_key() # Ensure key exists
                    encrypted = SecurityManager.encrypt_data(token)
                    ConfigHandler.save_config({"ts_token": encrypted})
                    return token
                except Exception as e:
                    logger.warning(f"Error migrating token: {e}")
                    return token
            else:
                # Does NOT look like a valid token. Likely an encrypted blob we can't read.
                # Return empty so UI prompts user to re-enter.
                logger.warning("Stored token could not be decrypted and does not appear to be plaintext. Ignoring.")
                return ""

    @staticmethod
    def save_token(token):
        encrypted = SecurityManager.encrypt_data(token)
        return ConfigHandler.save_config({"ts_token": encrypted})

    @staticmethod
    def is_onboarding_complete():
        config = ConfigHandler.load_config()
        return config.get("onboarding_complete", False)

    @staticmethod
    def set_onboarding_complete(complete=True):
        return ConfigHandler.save_config({"onboarding_complete": complete})

    @staticmethod
    def is_auto_update_enabled():
        config = ConfigHandler.load_config()
        return config.get("auto_update_enabled", False)

    @staticmethod
    def get_log_level():
        """Get configured log level (default: INFO)"""
        config = ConfigHandler.load_config()
        return config.get("log_level", "INFO").upper()

    @staticmethod
    def set_log_level(level):
        """Set log level (DEBUG, INFO, WARNING, ERROR)"""
        return ConfigHandler.save_config({"log_level": level.upper()})

    @staticmethod
    def get_auto_update_time():
        config = ConfigHandler.load_config()
        return config.get("auto_update_time", "16:30")

    @staticmethod
    def get_log_max_mb():
        config = ConfigHandler.load_config()
        return config.get("log_max_mb", 5)

    @staticmethod
    def get_log_backup_count():
        config = ConfigHandler.load_config()
        return config.get("log_backup_count", 5)

    @staticmethod
    def get_db_queue_size():
        config = ConfigHandler.load_config()
        return config.get("db_queue_size", 1024)

    @staticmethod
    def set_db_queue_size(size):
        return ConfigHandler.save_config({"db_queue_size": int(size)})

    @staticmethod
    def get_sync_concurrency():
        config = ConfigHandler.load_config()
        return config.get("sync_concurrency", 2)

    @staticmethod
    def set_sync_concurrency(concurrency):
        return ConfigHandler.save_config({"sync_concurrency": int(concurrency)})

    @staticmethod
    def get_max_batch_rows():
        config = ConfigHandler.load_config()
        return config.get("max_batch_rows", 20000)

    @staticmethod
    def set_max_batch_rows(rows):
        return ConfigHandler.save_config({"max_batch_rows": int(rows)})
    
    @staticmethod
    def get_sync_retry_count():
        config = ConfigHandler.load_config()
        return config.get("sync_retry_count", 3)

    @staticmethod
    def set_sync_retry_count(count):
        return ConfigHandler.save_config({"sync_retry_count": int(count)})

    @staticmethod
    def get_proxy_domains():
        """Get list of domains to bypass proxy (whitelist). Robustly returns list."""
        config = ConfigHandler.load_config()
        val = config.get("proxy_domains", [])
        if isinstance(val, list):
            return val
        return []

    @staticmethod
    def set_proxy_domains(domains):
        """Set list of domains to bypass proxy"""
        if not isinstance(domains, list):
            return False
        return ConfigHandler.save_config({"proxy_domains": domains})

    @staticmethod
    def get_config(key, default=None):
        """Generic get method for any setting"""
        config = ConfigHandler.load_config()
        return config.get(key, default)

    @staticmethod
    def get_setting(key, default=None):
        """Generic get method for any setting"""
        config = ConfigHandler.load_config()
        return config.get(key, default)

    @staticmethod
    def get_ai_config():
        """Get all AI related clean config"""
        config = ConfigHandler.load_config()
        encrypted_key = config.get("ai_api_key", "")
        
        # Decrypt key
        api_key = ""
        if encrypted_key:
            try:
                api_key = SecurityManager.decrypt_data(encrypted_key)
            except Exception:
                # If decryption fails (e.g. plain text or wrong key), just use it or return empty
                # For compatibility, if it looks short, maybe plain text? 
                # Better safe: treat as invalid if decrypt fails unless we want auto-migration.
                # Let's try to support auto-migration like tushare token
                api_key = encrypted_key if len(encrypted_key) < 60 else ""
                
        return {
            "ai_api_key": api_key,
            "ai_base_url": config.get("ai_base_url", "https://api.deepseek.com"),
            "ai_model_name": config.get("ai_model_name", "deepseek-chat")
        }

    @staticmethod
    def save_ai_config(api_key, base_url, model_name):
        """Save AI settings (API Key Encrypted)"""
        encrypted_key = ""
        if api_key:
            encrypted_key = SecurityManager.encrypt_data(api_key)
            
        return ConfigHandler.save_config({
            "ai_api_key": encrypted_key,
            "ai_base_url": base_url,
            "ai_model_name": model_name
        })

    @staticmethod
    def get_ai_system_prompt():
        """Get AI System Prompt (User defined or Default)"""
        config = ConfigHandler.load_config()
        return config.get("ai_system_prompt", DEFAULT_AI_PROMPT)

    @staticmethod
    def save_ai_system_prompt(prompt):
        """Save AI System Prompt"""
        return ConfigHandler.save_config({"ai_system_prompt": prompt})

    # === New AI Tuning Parameters ===

    @staticmethod
    def get_ai_max_candidates():
        config = ConfigHandler.load_config()
        return config.get("ai_max_candidates", 30)

    @staticmethod
    def set_ai_max_candidates(val):
        return ConfigHandler.save_config({"ai_max_candidates": int(val)})

    @staticmethod
    def get_strategy_min_turnover():
        config = ConfigHandler.load_config()
        return config.get("strategy_min_turnover", 2.0)

    @staticmethod
    def set_strategy_min_turnover(val):
        return ConfigHandler.save_config({"strategy_min_turnover": float(val)})

    @staticmethod
    def get_ai_concurrency():
        config = ConfigHandler.load_config()
        return config.get("ai_concurrency", 5)

    @staticmethod
    def set_ai_concurrency(val):
        return ConfigHandler.save_config({"ai_concurrency": int(val)})

    # === API Robustness Parameters ===
    
    @staticmethod
    def get_request_max_retries():
        """Get max retries for API requests (Hidden from UI, default 3)"""
        config = ConfigHandler.load_config()
        return config.get("request_max_retries", 3)

    @staticmethod
    def get_request_timeout():
        """Get timeout for API requests (Hidden from UI, default 30s)"""
        config = ConfigHandler.load_config()
        return config.get("request_timeout", 30)

    @staticmethod
    def get_tushare_timeout():
        """Get Tushare API timeout. If not set, returns None (no timeout)."""
        config = ConfigHandler.load_config()
        return config.get("tushare_timeout", None)

    @staticmethod
    def set_tushare_timeout(seconds):
        """Set Tushare API timeout in seconds"""
        return ConfigHandler.save_config({"tushare_timeout": int(seconds) if seconds is not None else None})

    @staticmethod
    def get_tushare_api_limit():
        """Get Tushare API rate limit (requests per minute). Default None (No Limit)."""
        config = ConfigHandler.load_config()
        return config.get("tushare_api_rate_limit", None)

    @staticmethod
    def set_tushare_api_limit(limit):
        """Set Tushare API rate limit (requests per minute)"""
        return ConfigHandler.save_config({"tushare_api_rate_limit": int(limit)})

    # === Localization ===
    @staticmethod
    def get_locale():
        config = ConfigHandler.load_config()
        return config.get("locale", "zh")

    @staticmethod
    def set_locale(locale):
        return ConfigHandler.save_config({"locale": locale})

    # === Scheduler ===
    @staticmethod
    def get_ai_prediction_time():
        """Get AI prediction time (default 20:30)"""
        config = ConfigHandler.load_config()
        return config.get("ai_prediction_time", "20:30")

    @staticmethod
    def set_ai_prediction_time(time_str):
        """Set AI prediction time (HH:MM format)"""
        return ConfigHandler.save_config({"ai_prediction_time": time_str})

