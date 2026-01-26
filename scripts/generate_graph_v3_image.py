import os
import sys

# Ensure 'app' directory is in python path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

from agents.graph import create_odis_graph

def generate_graph_image():
    graph = create_odis_graph()
    try:
        # Generate mermaid png
        png_bytes = graph.get_graph().draw_mermaid_png()
        
        output_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'agents', 'odis_graph_v3.png')
        with open(output_path, 'wb') as f:
            f.write(png_bytes)
        print(f"✅ Graph image generated at: {output_path}")
    except Exception as e:
        print(f"❌ Failed to generate graph image: {e}")
        # Fallback to mermaid text
        try:
            mermaid_text = graph.get_graph().draw_mermaid()
            print("--- MERMAID BEGIN ---")
            print(mermaid_text)
            print("--- MERMAID END ---")
        except:
            pass

if __name__ == "__main__":
    generate_graph_image()
