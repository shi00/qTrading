import sys
import os
import asyncio
import logging

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from data.news_fetcher import NewsFetcher

async def main():
    print("Testing get_hot_concepts...")
    try:
        results = await NewsFetcher.get_hot_concepts(limit=8)
        print(f"\nFound {len(results)} concepts:")
        # Print raw list to debug encoding
        print(results)
        for i, item in enumerate(results):
            print(f"{i+1}. {item['name']}: {item['change']} (Color: {item['color']})")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        NewsFetcher.shutdown()

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
