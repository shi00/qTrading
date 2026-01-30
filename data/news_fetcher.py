import akshare as ak
import pandas as pd
import datetime
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

import os
from contextlib import contextmanager

@contextmanager
def bypass_proxy_for_domestic(domain_substring="eastmoney.com"):
    """
    Temporarily add domain to NO_PROXY to bypass system proxy for domestic APIs.
    This helps when users have global proxy that fails for domestic requests.
    """
    original_no_proxy = os.environ.get("NO_PROXY", "")
    original_no_proxy_lower = os.environ.get("no_proxy", "")
    
    # Check if already bypassed (simple check)
    if domain_substring in original_no_proxy or domain_substring in original_no_proxy_lower:
        yield
        return

    # Add to NO_PROXY
    # Use lowercase 'no_proxy' as requests/urllib usually checks both or specific
    new_no_proxy = f"{original_no_proxy},{domain_substring}" if original_no_proxy else domain_substring
    os.environ["NO_PROXY"] = new_no_proxy
    os.environ["no_proxy"] = new_no_proxy # Set both for maximum compatibility
    
    try:
        yield
    finally:
        # Restore
        if original_no_proxy:
            os.environ["NO_PROXY"] = original_no_proxy
        else:
            os.environ.pop("NO_PROXY", None)
            
        if original_no_proxy_lower:
            os.environ["no_proxy"] = original_no_proxy_lower
        else:
            os.environ.pop("no_proxy", None)

class NewsFetcher:
    """
    Fetches news data using AKShare (free, open source).
    Focuses on individual stock news.
    """
    _executor = ThreadPoolExecutor(max_workers=4)

    @staticmethod
    async def get_stock_news(ts_code, limit=10):
        """
        Fetch specific stock news from EastMoney via AKShare.
        
        :param ts_code: Stock code (e.g. '000001.SZ' or '600000.SH' or just '600000')
        :param limit: Max number of news items to return
        :return: List of dicts [{'title':..., 'content':..., 'date':...}]
        """
        try:
            # Clean code format for akshare (usually expects 6 digits)
            symbol = ts_code.split('.')[0] if '.' in ts_code else ts_code
            
            loop = asyncio.get_event_loop()
            
            # Run blocking akshare call in thread pool with proxy bypass
            def _fetch():
                with bypass_proxy_for_domestic():
                    return ak.stock_news_em(symbol=symbol)

            df = await loop.run_in_executor(
                NewsFetcher._executor,
                _fetch
            )
            
            if df is None or df.empty:
                logger.warning(f"[News] No news found for {ts_code}")
                return []
                
            # AKShare stock_news_em columns: [关键词, 标题, 内容, 发布时间, 文章来源, 网址]
            # Rename for consistency
            # Note: Columns might vary by version, robust check needed
            
            # Filter recent news
            # We want to format it nicely for the AI
            news_list = []
            count = 0
            
            for _, row in df.iterrows():
                if count >= limit:
                    break
                    
                # Robust column access
                title = row.get('标题', '')
                content = row.get('内容', '')
                date_str = row.get('发布时间', '')
                
                # Simple cleanup
                if not title: continue
                
                news_list.append({
                    'title': title,
                    'summary': content[:200] + "..." if len(content) > 200 else content, # Truncate for AI
                    'publish_time': date_str,
                    'source': row.get('文章来源', 'EastMoney')
                })
                count += 1
                
            return news_list
    
        except Exception as e:
            logger.error(f"[News] Error fetching news for {ts_code}: {e}")
            return []

    @staticmethod
    async def get_latest_global_news(limit=20):
        """
        Get major financial news (CCTV / Major Portals)
        """
        try:
            loop = asyncio.get_event_loop()
            # stock_info_global_sina or similar
            # using 'news_cctv' from akshare if available, or 'stock_news_em' general
            # For now let's use a broad market news source
            
            # Using 'stock_telegraph_cls' (Cailianshe) for real-time flashes - very good for A-share
            def _fetch():
                 with bypass_proxy_for_domestic("cls.cn"): # Cailianshe domain? Or just default
                     with bypass_proxy_for_domestic(): # Default eastmoney too
                        return ak.stock_info_global_cls()

            df = await loop.run_in_executor(
                NewsFetcher._executor,
                _fetch
            )
            
            if df is None or df.empty:
                return []
                
            # Columns: [标题, 内容, 发布时间, ...]
            news_list = []
            for _, row in df.head(limit).iterrows():
                news_list.append({
                    'title': row.get('标题', '无标题'),
                    'content': row.get('内容', ''),
                    'time': row.get('发布时间', '')
                })
            return news_list
            
        except Exception as e:
            logger.error(f"[News] Error fetching global news: {e}")
            return []

    @staticmethod
    async def get_us_major_moves():
        """
        Fetch major US Tech giants performance (NVDA, TSLA, AAPL, MSFT, GOOGL, AMZN, META).
        Used for 'Shadow Strategy' (Mapping).
        """
        try:
            loop = asyncio.get_event_loop()
            
            # Using AKShare for US Stock Spot (Pink sheet or standard)
            # ak.stock_us_spot_em() retrieves all US stocks. might be slow.
            # Use 'stock_us_famous_spot_em' - "美股知名美股"
            
            def _fetch():
                 with bypass_proxy_for_domestic():
                     return ak.stock_us_famous_spot_em()

            df = await loop.run_in_executor(
                NewsFetcher._executor,
                _fetch
            )
            
            if df is None or df.empty:
                return "Global data unavailable."
                
            # Filter for key tech giants
            key_tickers = ['NVDA', 'TSLA', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'AMD']
            # Provide mapping since names might be in Chinese or English
            # AKShare returns: [名称, 最新价, 涨跌额, 涨跌幅, code, ...]
            
            summary = []
            for _, row in df.iterrows():
                name = row.get('名称', '')
                code = row.get('代码', '').split('.')[0] # 105.NVDA -> NVDA? or just name match
                
                # Check for substring match in name or code
                # Usually code is not in the column directly in famous_spot, checking '名称'
                # Actually commonly: 英伟达, 特斯拉, 苹果...
                
                # Simple logic: Just grab Top 10 by volume or just grab known names
                # Let's iterate and match
                
                matched = False
                for k in key_tickers:
                    if k in name or k in str(row): # loose match
                        matched = True
                        break
                        
                # Also include High Volatility ones (>3% or <-3%)
                pct = row.get('涨跌幅', 0)
                try:
                    pct = float(pct)
                except:
                    pct = 0
                    
                if matched or abs(pct) > 3.0:
                    summary.append(f"{name}: {pct}%")
            
            if not summary:
                # Fallback: just take top 5
                top5 = df.head(5)
                for _, row in top5.iterrows():
                    summary.append(f"{row.get('名称')}: {row.get('涨跌幅')}%")
                    
            return ", ".join(summary)
            
        except Exception as e:
            logger.error(f"[News] Error fetching US moves: {e}")
            return "Global data error."
            
    @staticmethod
    async def get_hot_concepts(limit=8):
        """
        Get top performing concept boards (Hotspots).
        Returns list of dicts: {'name': 'Lithium', 'change': '3.5%', 'code': '...'}
        """
        try:
            loop = asyncio.get_event_loop()
            # ak.stock_board_concept_name_em() returns rank of all concepts
            
            def _fetch():
                # Tier 1: EastMoney Concepts
                try:
                    with bypass_proxy_for_domestic():
                         return ak.stock_board_concept_name_em(), "concept"
                except Exception as e:
                    logger.warning(f"[News] Tier 1 (EM Concept) failed: {e}")
                    
                # Tier 2: THS Concepts
                try:
                    with bypass_proxy_for_domestic("10jqka.com.cn"):
                         # columns: 日期, 概念名称, 成分股数量, 涨跌幅, ...
                         return ak.stock_board_concept_name_ths(), "ths_concept"
                except Exception as e:
                    logger.warning(f"[News] Tier 2 (THS Concept) failed: {e}")

                # Tier 3: EastMoney Industries (Last Resort)
                try:
                    with bypass_proxy_for_domestic():
                         return ak.stock_board_industry_name_em(), "industry"
                except Exception as e:
                    logger.error(f"[News] All data sources for hot concepts failed: {e}")
                    raise e

            result_data = await loop.run_in_executor(
                NewsFetcher._executor,
                _fetch
            )
            
            df, source_type = result_data
            
            if df is None or df.empty:
                return []
                
            # Normalization
            # 1. Rename columns to standard '板块名称', '涨跌幅'
            if source_type == "ths_concept":
                # THS cols usually: 概念名称, 涨跌幅
                rename_map = {}
                for col in df.columns:
                    if "名称" in col: rename_map[col] = "板块名称"
                    if "涨跌幅" in col: rename_map[col] = "涨跌幅"
                df.rename(columns=rename_map, inplace=True)

            # 2. Sort
            if '涨跌幅' in df.columns:
                 df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')
                 df.sort_values('涨跌幅', ascending=False, inplace=True)
            
            results = []
            for _, row in df.head(limit).iterrows():
                name = row.get('板块名称')
                if not name:
                    # Attempt to find name in other columns if not normalized correctly
                    for col in df.columns:
                        if '名称' in str(col) or 'name' in str(col).lower():
                            val = row.get(col)
                            if val:
                                name = val
                                break
                    
                if not name:
                    continue # Skip invalid rows

                change = row.get('涨跌幅', 0)
                
                # Check formatting
                change_str = f"{change:.2f}%"
                if isinstance(change, str) and '%' in change:
                    change_str = change # Already formatted
                
                results.append({
                    'name': name,
                    'change': change_str,
                    'color': 'red' if (isinstance(change, (int, float)) and change > 0) else 'green'
                })
                
            return results
            
        except Exception as e:
            logger.error(f"[News] Error fetching hot concepts: {e}")
            return []
            

