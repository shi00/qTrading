
import unittest
import asyncio
import time
import logging
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.local_model_manager import LocalModelManager
from utils.config_handler import ConfigHandler

# Configure logging for tests
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("LocalModelBenchmark")

class TestLocalModelBenchmarks(unittest.TestCase):
    """
    Benchmarks and usability tests for LocalModelManager.
    Run with: python -m unittest tests/test_local_model_benchmarks.py
    """

    def setUp(self):
        """Set up async loop for each test."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Get manager instance
        self.manager = self.loop.run_until_complete(LocalModelManager.get_instance())
        
        # Ensure model is configured
        config = ConfigHandler.get_local_ai_config()
        self.model_path = config.get('local_model_path')
        if not self.model_path or not os.path.exists(self.model_path):
            self.skipTest(f"Local model not found at: {self.model_path}")

        # Ensure model is loaded (warm-up)
        if not self.manager._llm:
             self.loop.run_until_complete(self.manager.load_model(self.model_path))

    def tearDown(self):
        self.loop.close()

    def test_benchmark_runner(self):
        """
        Execute 100+ inference cases covering usability and performance.
        """
        logger.info("="*40)
        logger.info("STARTING LOCAL AI BENCHMARK (100 CASES)")
        logger.info("="*40)

        # 1. Define Test Matrix
        scenarios = [
            # (Name, Prompt, MaxTokens, Temp)
            ("Empty", "", 16, 0.1),
            ("Short_Fact", "Capital of France?", 32, 0.1),
            ("Medium_Creative", "Write a haiku about stocks.", 64, 0.8),
            ("Json_Strict", "Classify: 'Buy BABA at $80'. Output JSON.", 128, 0.1),
            ("Long_Context", "Context: " + ("data " * 50) + "\nSummarize.", 32, 0.5)
        ]

        # Generate 100 cases by repeating scenarios
        total_cases = 100
        cases = []
        for i in range(total_cases):
            scenario = scenarios[i % len(scenarios)]
            cases.append({
                "id": i,
                "name": scenario[0],
                "prompt": scenario[1],
                "max_tokens": scenario[2],
                "temp": scenario[3]
            })

        # 2. Execution Loop
        success_count = 0
        latencies = []
        token_speeds = []
        
        start_benchmark = time.time()

        for case in cases:
            case_id = case["id"]
            name = case["name"]
            
            # SubTest context for cleaner failure reporting
            with self.subTest(i=case_id, name=name):
                t0 = time.time()
                try:
                    # Run Inference
                    response = self.loop.run_until_complete(
                        self.manager.run_inference(
                            prompt=case["prompt"],
                            max_tokens=case["max_tokens"],
                            temperature=case["temp"]
                        )
                    )
                    
                    duration = time.time() - t0
                    latencies.append(duration)
                    success_count += 1
                    
                    # Basic Validation
                    self.assertIsNotNone(response)
                    self.assertIsInstance(response, str)
                    
                    # Log occasional progress
                    if (case_id + 1) % 10 == 0:
                        avg_lat = sum(latencies)/len(latencies)
                        logger.info(f"Progress: {case_id+1}/{total_cases} | Avg Latency: {avg_lat:.3f}s | Last: {duration:.3f}s")

                except Exception as e:
                    logger.error(f"Case {case_id} ({name}) Failed: {e}")
                    # Don't stop benchmark on single failure, but mark test as failed
                    self.fail(f"Case {case_id} failed: {e}")

        total_time = time.time() - start_benchmark
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        throughput = success_count / total_time

        # 3. Final Report
        logger.info("="*40)
        logger.info(f"BENCHMARK COMPLETED")
        logger.info(f"Total Cases: {total_cases}")
        logger.info(f"Success:     {success_count} ({success_count/total_cases*100:.1f}%)")
        logger.info(f"Total Time:  {total_time:.2f}s")
        logger.info(f"Avg Latency: {avg_latency:.4f}s")
        logger.info(f"Throughput:  {throughput:.2f} req/s")
        logger.info("="*40)

if __name__ == '__main__':
    unittest.main()
