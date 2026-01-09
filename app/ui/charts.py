import plotly.express as px
import pandas as pd
import numpy as np

def line_polar(theta, r, line_close=True, range_r=[0, 100], **kwargs):
    """
    Creates a radar chart (polar line plot).
    
    Args:
        theta: Iterable of category names.
        r: Iterable of values.
        line_close: Whether to close the loop (default True).
        range_r: Range of radial axis [min, max].
    """
    # Ensure inputs are list-like
    if hasattr(theta, 'tolist'): theta = theta.tolist()
    if hasattr(r, 'tolist'): r = r.tolist()
    
    df = pd.DataFrame(dict(r=r, theta=theta))
    fig = px.line_polar(df, r='r', theta='theta', line_close=line_close, range_r=range_r, **kwargs)
    return fig
