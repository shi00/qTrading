import akshare as ak
import pandas as pd
import datetime
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

from utils.proxy_manager import ProxyManager

import requests
import json

class NewsFetcher:
    """
    Fetches news data using AKShare and direct robust Sina/THS clients.
    Replaces blocked EastMoney interfaces.
    """
    _executor = ThreadPoolExecutor(max_workers=4)

    @classmethod
    def shutdown(cls):
        """Shutdown the thread pool executor. Call on app exit."""
        if cls._executor:
            cls._executor.shutdown(wait=False)
            logger.info("[NewsFetcher] Executor shutdown.")

    @staticmethod
    async def get_stock_news(ts_code, limit=10):
        """
        Fetch specific stock news. 
        Note: Specific stock news is hard to get reliably without blocking.
        Trying Sina or THS via akshare is hit or miss.
        For now, we return empty list to avoid errors, or implement a broad search if critical.
        """
        # Placeholder: returning empty safely until a reliable unrestricted source is found 
        # for individual A-share stock news content.
        # Could try: ak.stock_news_ths_individual(symbol) if it existed.
        return []

    @staticmethod
    async def get_latest_global_news(limit=20):
        """
        Get major financial news (CCTV / Major Portals)
        """
        try:
            loop = asyncio.get_running_loop()
            
            def _fetch():
                 with ProxyManager.bypass_proxy_for_domestic("cls.cn"): 
                    return ak.stock_info_global_cls()

            try:
                df = await loop.run_in_executor(NewsFetcher._executor, _fetch)
            except RuntimeError:
                return []
            
            if df is None or df.empty:
                return []
                
            news_list = []
            for _, row in df.head(limit).iterrows():
                news_list.append({
                    'title': row.get('标题') or row.get('title', '无标题'),
                    'content': row.get('内容') or row.get('content', ''),
                    'time': row.get('发布时间') or row.get('时间') or row.get('time', '')
                })
            return news_list
            
        except Exception as e:
            logger.error(f"[News] Error fetching global news: {e}")
            return []

    @staticmethod
    async def get_us_major_moves():
        """
        Fetch major US Tech giants performance (NVDA, TSLA, AAPL, MSFT, GOOGL, AMZN, META).
        Uses Sina Finance Custom Sort API (Verified Working).
        """
        try:
            loop = asyncio.get_running_loop()
            
            def _fetch():
                # Direct call to Sina US API
                url = "http://stock.finance.sina.com.cn/usstock/api/jsonp.php/IO/US_CategoryService.getList"
                params = {
                    "page": "1",
                    "num": "100",
                    "sort": "mktcap", # Sort by market cap to get giants
                    "asc": "0",
                    "market": "",
                    "id": ""
                }
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                
                resp = requests.get(url, params=params, headers=headers, timeout=10)
                content = resp.text
                
                # Robust JSONP parsing: IO( {Data} );
                start = content.find("(")
                end = content.rfind(")")
                if start != -1 and end != -1 and start < end:
                    json_str = content[start+1 : end]
                    try:
                        data = json.loads(json_str)
                        return data.get('data', [])
                    except json.JSONDecodeError:
                        logger.warning("[News] Failed to decode JSON from Sina US API")
                        return []
                return []

            data_list = await loop.run_in_executor(NewsFetcher._executor, _fetch)
            
            if not data_list:
                return "Global data unavailable."
            
            # Key mappings (Sina Names -> Ticker/English)
            key_tickers = ['NVDA', 'TSLA', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'AMD']
            
            summary = []
            for item in data_list:
                # Sina structure: {name: "NVDA", cname: "英伟达", price: "135.2", diff: "3.2", chg: "2.45", ...}
                ticker = item.get('name', '')
                cname = item.get('cname', '')
                
                matched = False
                if ticker in key_tickers:
                    matched = True
                    
                # Safe float conversion
                try:
                    pct = float(item.get('chg', 0))
                except (ValueError, TypeError):
                    pct = 0.0
                
                if matched or abs(pct) > 3.0:
                    display_name = ticker if ticker else cname
                    summary.append(f"{display_name}: {pct}%")
            
            # If no giants found (unlikely with mktcap sort), take top 3 movers
            if not summary and data_list:
                for item in data_list[:5]:
                    name = item.get('name', 'Unknown')
                    chg = item.get('chg', '0')
                    summary.append(f"{name}: {chg}%")
                    
            return ", ".join(summary)
            
        except Exception as e:
            logger.error(f"[News] Error fetching US moves: {e}")
            return "Global data error."
            
    @staticmethod
    async def get_hot_concepts(limit=8):
        """
        Get top performing concept boards.
        Uses Sina Finance (verified working, not blocked).
        """
        try:
            loop = asyncio.get_running_loop()
            
            def _fetch():
                # Sina Finance - Concept Boards
                try:
                    with ProxyManager.bypass_proxy_for_domestic("sina.com.cn"):
                         return ak.stock_sector_spot(indicator="概念")
                except Exception as e:
                    logger.warning(f"[News] Sina Concept Boards failed: {e}")
                    return None

            df = await loop.run_in_executor(NewsFetcher._executor, _fetch)
            
            if df is None or df.empty:
                return []
            
            # Sina returns: 板块, 涨跌幅
            if '涨跌幅' in df.columns:
                df = df.sort_values('涨跌幅', ascending=False)
            
            results = []
            for _, row in df.head(limit).iterrows():
                name = row.get('板块', '')
                if not name: continue 

                try:
                    raw_val = row.get('涨跌幅', 0)
                    # Handle NaN from pandas
                    if pd.isna(raw_val):
                        change_val = 0.0
                    else:
                        change_val = float(raw_val)
                    change_str = f"{change_val:.2f}%"
                except:
                    change_str = "0.00%"
                    change_val = 0.0

                # Color: red=up, green=down, gray=flat
                if change_val > 0:
                    color = 'red'
                elif change_val < 0:
                    color = 'green'
                else:
                    color = 'grey'

                results.append({
                    'name': name,
                    'change': change_str,
                    'color': color
                })
                
            return results
            
        except Exception as e:
            logger.error(f"[News] Error fetching hot concepts: {e}")
            return []
            

