import asyncio
import logging
from datetime import datetime, timedelta


import akshare as ak
import pandas as pd

from utils.thread_pool import ThreadPoolManager, TaskType
from ui.i18n import I18n

logger = logging.getLogger(__name__)

import requests
import json


class NewsFetcher:
    """
    Fetches news data using AKShare and direct robust Sina/THS clients.
    Replaces blocked EastMoney interfaces.
    """


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

            def _fetch():
                # ProxyManager already whitelists domestic domains via NO_PROXY at startup
                return ak.stock_info_global_cls()

            try:
                # Use Global IO Pool
                df = await ThreadPoolManager().run_async(TaskType.IO, _fetch)
            except RuntimeError:
                return []

            if df is None or df.empty:
                return []

            news_list = []
            now = datetime.now()
            today_str = now.strftime('%Y-%m-%d')
            
            for _, row in df.head(limit).iterrows():
                # Extract raw time string
                raw_time = row.get('发布时间') or row.get('时间') or row.get('time', '')
                final_time = raw_time
                
                # Handle time-only string (e.g. "09:30:00") -> Prepend Date
                if raw_time and len(str(raw_time)) <= 8 and ':' in str(raw_time):
                    try:
                        # Parse time to determine if it's today or yesterday
                        # e.g. If now is 00:05 and news is 23:55 -> Yesterday
                        t_parts = list(map(int, str(raw_time).split(':')))
                        # Handle HH:MM or HH:MM:SS
                        if len(t_parts) >= 2:
                            news_dt = now.replace(hour=t_parts[0], minute=t_parts[1], second=t_parts[2] if len(t_parts)>2 else 0)
                            
                            # If news time is significantly in the future (> 30 mins), it's likely yesterday's news
                            # (e.g. Now 10:00, News 23:00 -> Yesterday 23:00)
                            if news_dt > now + timedelta(minutes=30):
                                news_dt -= timedelta(days=1)
                                
                            final_time = news_dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            final_time = f"{today_str} {raw_time}"
                    except Exception as e:
                        # Fallback
                        final_time = f"{today_str} {raw_time}"
                
                # Standardize time format to YYYY-MM-DD HH:MM:SS for consistent sorting
                try:
                     # Try parsing with pandas for robustness (handles multiple formats)
                    dt_obj = pd.to_datetime(final_time)
                    final_time = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    # Fallback: if pandas fails, try to ensure at least string format
                    final_time = str(final_time)

                news_list.append({
                    'title': row.get('标题') or row.get('title', I18n.get('news_no_title')),
                    'content': row.get('内容') or row.get('content', ''),
                    'time': final_time
                })
            
            # Ensure we sort by time DESC so news_list[0] is truly the latest
            news_list.sort(key=lambda x: x['time'], reverse=True)
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

            def _fetch():
                # Direct call to Sina US API
                url = "http://stock.finance.sina.com.cn/usstock/api/jsonp.php/IO/US_CategoryService.getList"
                params = {
                    "page": "1",
                    "num": "100",
                    "sort": "mktcap",  # Sort by market cap to get giants
                    "asc": "0",
                    "market": "",
                    "id": ""
                }
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

                resp = requests.get(url, params=params, headers=headers, timeout=10)
                content = resp.text

                # Robust JSONP parsing: IO( {Data} );
                start = content.find("(")
                end = content.rfind(")")
                if start != -1 and end != -1 and start < end:
                    json_str = content[start + 1: end]
                    try:
                        data = json.loads(json_str)
                        return data.get('data', [])
                    except json.JSONDecodeError:
                        logger.warning("[News] Failed to decode JSON from Sina US API")
                        return []
                return []

            data_list = await ThreadPoolManager().run_async(TaskType.IO, _fetch)

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

            def _fetch():
                # Sina Finance - Concept Boards
                # ProxyManager already whitelists domestic domains via NO_PROXY at startup
                try:
                    return ak.stock_sector_spot(indicator="概念")
                except Exception as e:
                    logger.warning(f"[News] Sina Concept Boards failed: {e}")
                    return None

            # Use Global IO Pool
            df = await ThreadPoolManager().run_async(TaskType.IO, _fetch)

            if df is None or df.empty:
                return []

            # Sina returns: 板块, 涨跌幅
            if '涨跌幅' in df.columns:
                df = df.sort_values('涨跌幅', ascending=False)

            results = []
            for _, row in df.head(limit).iterrows():
                name = row.get('板块', '')
                if not name:
                    continue

                try:
                    raw_val = row.get('涨跌幅', 0)
                    # Handle NaN from pandas
                    if pd.isna(raw_val):
                        change_val = 0.0
                    else:
                        change_val = float(raw_val)
                    change_str = f"{change_val:.2f}%"
                except (ValueError, TypeError):
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
