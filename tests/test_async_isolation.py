import asyncio
import concurrent.futures
import threading

# Mock PydanticAI/Graph behavior
async def mock_agent_run():
    print(f"Running in thread: {threading.current_thread().name}")
    # Simulate async work
    await asyncio.sleep(0.1)
    return "Agent Finished"

def run_isolated_async(coro):
    """
    Runs an async coroutine in a separate thread with a fresh event loop.
    This mimics the proposed fix for Streamlit.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()

def main():
    print(f"Main thread: {threading.current_thread().name}")
    try:
        result = run_isolated_async(mock_agent_run())
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
