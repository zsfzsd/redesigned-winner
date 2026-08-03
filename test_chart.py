import streamlit as st
import plotly.graph_objects as go
import pandas as pd

st.title("最小化测试")
df = pd.DataFrame({'x': [1, 2, 3], 'y': [2, 4, 1]})
fig = go.Figure()
fig.add_trace(go.Scatter(x=df['x'], y=df['y'], mode='lines'))
st.plotly_chart(fig)
