import os
import sys
import asyncio
from dataclasses import dataclass

# Ensure 'app' directory is in python path
sys.path.append(os.path.join(os.getcwd(), 'app'))

from pydantic_graph import End
from pydantic_graph.beta import GraphBuilder, StepContext

@dataclass
class State:
    val: str = ""

async def my_step(ctx: StepContext[State, None, None]) -> End[str]:
    return End("hello")

async def test():
    gb = GraphBuilder(state_type=State, output_type=str)
    step_node = gb.step(my_step)
    gb.add(gb.edge_from(gb.start_node).to(step_node))
    gb.add(gb.edge_from(step_node).to(gb.end_node))
    graph = gb.build()
    
    state = State()
    res = await graph.run(state=state)
    print(f"Result type: {type(res)}")
    print(f"Result: {res}")

if __name__ == "__main__":
    asyncio.run(test())
