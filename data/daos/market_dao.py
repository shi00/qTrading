import datetime
import logging
from .base_dao import BaseDao

logger = logging.getLogger(__name__)

class MarketDao(BaseDao):

    # --- Market News ---
    async def save_market_news(self, news_item, wait=False):
        # news_item is dict
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
              INSERT INTO market_news (content, tags, publish_time, source, created_at)
              VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(content, publish_time) DO
              UPDATE SET tags = COALESCE (excluded.tags, market_news.tags) \
              """
        params = (
            news_item.get('content'), news_item.get('tags'), news_item.get('publish_time'),
            news_item.get('source', 'Sina')
        )
        return await self._write_db(sql, params, is_many=False)

    async def get_market_news(self, limit=50, offset=0, min_publish_time=None):
        sql = "SELECT * FROM market_news WHERE 1=1"
        params = []
        if min_publish_time:
            sql += " AND publish_time >= ?"
            params.append(min_publish_time)
        sql += " ORDER BY publish_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return await self._read_db(sql, params)
