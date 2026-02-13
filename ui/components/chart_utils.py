import plotly.graph_objects as go
import pandas as pd
from ui.theme import AppColors
from ui.i18n import I18n

import flet as ft # Needed for ThemeMode comparison

def create_kline_chart(df, title="", theme_mode=None):
    """
    Create a Plotly K-line chart (Candlestick) with Moving Averages.
    
    :param df: DataFrame with columns: trade_date, open, high, low, close
    :param title: Chart title
    :param theme_mode: "light", "dark", or None (auto-detect)
    :return: plotly.graph_objects.Figure
    """
    if df is None or df.empty:
        return go.Figure()

    # Auto-detect theme if not provided
    if theme_mode is None:
        is_dark = AppColors._CURRENT_THEME_MODE == ft.ThemeMode.DARK
    else:
        is_dark = theme_mode == 'dark'

    # Ensure date is string format (YYYY-MM-DD) for maximum compatibility
    dates = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d').tolist()
    
    # Calculate MAs
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma10'] = df['close'].rolling(window=10).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    
    # Candlestick
    candlestick = go.Candlestick(
        x=dates,
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        increasing_line_color=AppColors.RISE,  # Red for rise in China
        decreasing_line_color=AppColors.FALL,  # Green for fall in China
        name=I18n.get("chart_kline")
    )
    
    # Moving Averages
    ma5 = go.Scatter(x=dates, y=df['ma5'], mode='lines', name='MA5', line=dict(color='orange', width=1))
    ma10 = go.Scatter(x=dates, y=df['ma10'], mode='lines', name='MA10', line=dict(color='blue', width=1))
    ma20 = go.Scatter(x=dates, y=df['ma20'], mode='lines', name='MA20', line=dict(color='purple', width=1))
    
    # Create figure
    fig = go.Figure(data=[candlestick, ma5, ma10, ma20])
    
    # Layout styling matching App Theme
    # is_dark is already calculated above
    bg_color = AppColors.BACKGROUND if is_dark else '#FFFFFF' # AppColors might be hex, plotly handles it
    paper_color = AppColors.SURFACE if is_dark else '#FFFFFF'
    grid_color = '#333333' if is_dark else '#EEEEEE'
    text_color = '#FFFFFF' if is_dark else '#333333'
    
    fig.update_layout(
        title=dict(text=title, font=dict(color=text_color, size=16)),
        xaxis_rangeslider_visible=False,
        plot_bgcolor=bg_color,
        paper_bgcolor=paper_color,
        xaxis=dict(
            showgrid=True, 
            gridcolor=grid_color,
            tickfont=dict(color=text_color)
        ),
        yaxis=dict(
            showgrid=True, 
            gridcolor=grid_color, 
            side='right',
            tickfont=dict(color=text_color)
        ),
        margin=dict(l=10, r=40, t=40, b=20),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(color=text_color)
        ),
        hovermode='x unified'
    )
    
    return fig

def generate_kline_html(df, title="", theme_mode=None):
    """
    Generate HTML string for the K-line chart.
    """
    fig = create_kline_chart(df, title, theme_mode)
    
    # Generate HTML with CDN version of plotly.js to keep it lightweight
    # full_html=True ensures it has <html><body> tags for WebView
    import plotly.io as pio
    return pio.to_html(fig, full_html=True, include_plotlyjs='cdn')
