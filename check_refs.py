import pandas as pd
import os
import config as cfg

path = os.path.join(cfg.get_data_path(), cfg.REFERENTIELS_FILE)
df = pd.read_parquet(path)

# Look at FAP codes and ROME families if present
print("--- FAP Examples ---")
print(df[df['key'] == 'fap_codes'].head(10)[['code', 'label']])

# Check if we have ROME mapping
print("\n--- Any ROME mapping? ---")
print(df[df['key'].str.contains('rome', case=False)].head(5))
