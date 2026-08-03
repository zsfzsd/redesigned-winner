import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sqlite3
import hashlib
from datetime import datetime, timedelta
import traceback

# ==================== 页面配置 ====================
st.set_page_config(
    page_title="港股 KDJ 标记工具",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== 常量 ====================
FIXED_STOCKS = ["3690.HK", "2015.HK", "2228.HK", "9868.HK", "0981.HK", "9880.HK", "1810.HK"]
PERIOD_MAP = {
    "月线": "1mo",
    "周线": "1wk",
    "日线": "1d",
    "120分钟": "120min",
    "85分钟": "85min",
    "60分钟": "60min",
    "30分钟": "30min",
    "15分钟": "15min",
    "5分钟": "5min",
}
YF_INTERVALS = ["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"]
CUSTOM_INTERVALS = {
    "120min": {"base": "5m", "rule": "2h"},
    "85min":  {"base": "5m", "rule": "85min"},
}

# KDJ 参数
KDJ_N = 14
KDJ_M1 = 2
KDJ_M2 = 7

# 均线周期
MA_PERIODS = [5, 10, 20, 60]

# 备注选项
REMARK_OPTIONS = ["突破买入", "止损卖出", "抄底", "追高", "突破卖出", "止损买入", "其他"]

# 数据库路径
DB_PATH = "trade_marks.db"

# ==================== 数据库初始化 ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, password_hash TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS marks
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT,
                  stock_code TEXT,
                  mark_time TEXT,
                  price REAL,
                  direction TEXT,
                  remark TEXT,
                  FOREIGN KEY (username) REFERENCES users(username))''')
    default_user = "admin"
    default_pass = hashlib.sha256("admin".encode()).hexdigest()
    try:
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (default_user, default_pass))
    except sqlite3.IntegrityError:
        pass
    conn.commit()
    conn.close()

init_db()

# ==================== 用户认证 ====================
def login_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if row and row[0] == hashlib.sha256(password.encode()).hexdigest():
        return True
    return False

def register_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                  (username, hashlib.sha256(password.encode()).hexdigest()))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

if not st.session_state.logged_in:
    tab1, tab2 = st.tabs(["登录", "注册"])
    with tab1:
        username = st.text_input("用户名", key="login_user")
        password = st.text_input("密码", type="password", key="login_pass")
        if st.button("登录"):
            if login_user(username, password):
                st.session_state.logged_in = True
                st.session_state.username = username
                st.rerun()
            else:
                st.error("用户名或密码错误")
    with tab2:
        new_user = st.text_input("新用户名", key="reg_user")
        new_pass = st.text_input("新密码", type="password", key="reg_pass")
        if st.button("注册"):
            if register_user(new_user, new_pass):
                st.success("注册成功，请登录")
            else:
                st.error("用户名已存在")
    st.stop()

# ==================== 辅助函数：安全提取列名 ====================
def safe_rename_ohlc(df):
    """
    将 yfinance 返回的 DataFrame 列名统一为小写 'open','high','low','close'
    支持单级列名和 MultiIndex 列名
    """
    if df.empty:
        return df

    # 处理 MultiIndex（yfinance 常见格式：('Open', 'TICKER') ）
    if isinstance(df.columns, pd.MultiIndex):
        new_data = {}
        for col in df.columns:
            price_type = col[0].lower()
            if 'open' in price_type:
                new_data['open'] = df[col]
            elif 'high' in price_type:
                new_data['high'] = df[col]
            elif 'low' in price_type:
                new_data['low'] = df[col]
            elif 'close' in price_type:
                new_data['close'] = df[col]
        if not new_data:
            return pd.DataFrame()
        return pd.DataFrame(new_data, index=df.index)

    # 处理单级列名
    rename_map = {}
    for col in df.columns:
        col_lower = col.lower()
        if 'open' in col_lower:
            rename_map[col] = 'open'
        elif 'high' in col_lower:
            rename_map[col] = 'high'
        elif 'low' in col_lower:
            rename_map[col] = 'low'
        elif 'close' in col_lower:
            rename_map[col] = 'close'
    df = df.rename(columns=rename_map)
    # 只保留需要的列
    keep = [col for col in df.columns if col in ['open','high','low','close']]
    return df[keep]

# ==================== 数据获取函数（无缓存，带完整诊断） ====================
def get_data(ticker, period_str):
    """下载港股数据，月线/周线通过日线合成，分钟线用原生或自定义"""
    st.write(f"🔍 正在尝试下载 {ticker} 的 {period_str} 数据...")
    
    try:
        # 月线、周线：用日线重采样
        if period_str in ["1mo", "1wk"]:
            st.write("使用日线合成...")
            df_daily = yf.download(ticker, period="2y", interval="1d", progress=False)
            if df_daily is None or df_daily.empty:
                st.warning("日线下载为空，返回 None")
                return None
            df_daily = safe_rename_ohlc(df_daily)
            if df_daily.empty:
                st.warning("日线处理后为空")
                return None
            rule = "M" if period_str == "1mo" else "W"
            df_resampled = df_daily.resample(rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last'
            }).dropna()
            st.success(f"合成成功，行数：{len(df_resampled)}")
            return df_resampled

        # 日线
        if period_str == "1d":
            st.write("下载日线...")
            df = yf.download(ticker, period="3mo", interval="1d", progress=False)
            if df is None or df.empty:
                st.warning("日线下载为空")
                return None
            st.write(f"yfinance 返回 {len(df)} 行，列名: {df.columns.tolist()}")
            df = safe_rename_ohlc(df)
            st.write(f"safe_rename_ohlc 后，列名: {df.columns.tolist()}, 形状: {df.shape}")
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            st.success(f"日线下载成功，行数：{len(df)}")
            return df

        # 原生分钟线
        if period_str in YF_INTERVALS:
            st.write(f"下载分钟线 {period_str} ...")
            df = yf.download(ticker, period="7d", interval=period_str, progress=False)
            if df is None or df.empty:
                st.warning("分钟线下载为空")
                return None
            df = safe_rename_ohlc(df)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            st.success(f"分钟线下载成功，行数：{len(df)}")
            return df

        # 自定义合成周期
        if period_str in CUSTOM_INTERVALS:
            base = CUSTOM_INTERVALS[period_str]["base"]
            rule = CUSTOM_INTERVALS[period_str]["rule"]
            st.write(f"使用 {base} 合成 {period_str} ...")
            df_base = yf.download(ticker, period="7d", interval=base, progress=False)
            if df_base is None or df_base.empty:
                st.warning(f"{base} 数据为空，无法合成")
                return None
            df_base = safe_rename_ohlc(df_base)
            df_resampled = df_base.resample(rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last'
            }).dropna()
            st.success(f"合成成功，行数：{len(df_resampled)}")
            return df_resampled

        st.warning(f"不支持的周期: {period_str}")
        return None

    except Exception as e:
        st.error(f"❌ get_data 发生异常: {e}")
        st.code(traceback.format_exc())
        return None

# ⚠️ 注意：暂时取消缓存，确保每次诊断都是最新结果
def load_and_calc(ticker, period_str):
    """加载数据，计算 KDJ 和均线（无缓存版）"""
    st.write(f"调用 load_and_calc: {ticker} {period_str}")
    df = get_data(ticker, period_str)
    st.write(f"get_data 返回: 类型 {type(df)}, 是否空: {df.empty if df is not None else 'None'}")
    if df is None or df.empty:
        st.warning("load_and_calc: 数据为空或None")
        return None

    st.write(f"数据列名: {df.columns.tolist()}")
    for ma in MA_PERIODS:
        df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()

    low_min = df['low'].rolling(window=KDJ_N).min()
    high_max = df['high'].rolling(window=KDJ_N).max()
    rsv = (df['close'] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=KDJ_M1-1, adjust=False).mean()
    d = k.ewm(com=KDJ_M2-1, adjust=False).mean()
    j = 3 * k - 2 * d
    df['K'] = k
    df['D'] = d
    df['J'] = j

    result = df.dropna()
    if result.empty:
        st.warning("KDJ 计算后数据为空（可能数据量不足）")
        return None
    st.success(f"KDJ 计算完成，最终行数: {len(result)}")
    return result

def get_daily_kdj(ticker):
    """获取日线级别 KDJ"""
    df_daily = yf.download(ticker, period="3mo", interval="1d", progress=False)
    if df_daily.empty:
        return None
    df_daily = safe_rename_ohlc(df_daily)
    if df_daily.index.tz is not None:
        df_daily.index = df_daily.index.tz_localize(None)

    low_min = df_daily['low'].rolling(window=KDJ_N).min()
    high_max = df_daily['high'].rolling(window=KDJ_N).max()
    rsv = (df_daily['close'] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=KDJ_M1-1, adjust=False).mean()
    d = k.ewm(com=KDJ_M2-1, adjust=False).mean()
    j = 3 * k - 2 * d
    df_daily['KDaily'] = k
    df_daily['DDaily'] = d
    df_daily['JDaily'] = j
    return df_daily[['KDaily', 'DDaily', 'JDaily']].dropna()

# ==================== 标记管理 ====================
def add_mark(username, stock_code, mark_time, price, direction, remark):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO marks (username, stock_code, mark_time, price, direction, remark) VALUES (?,?,?,?,?,?)",
              (username, stock_code, mark_time, price, direction, remark))
    conn.commit()
    conn.close()

def get_marks(username, stock_code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, mark_time, price, direction, remark FROM marks WHERE username=? AND stock_code=? ORDER BY mark_time",
              (username, stock_code))
    rows = c.fetchall()
    conn.close()
    return rows

def update_mark(mark_id, new_time, new_price, new_direction, new_remark):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE marks SET mark_time=?, price=?, direction=?, remark=? WHERE id=?",
              (new_time, new_price, new_direction, new_remark, mark_id))
    conn.commit()
    conn.close()

def delete_mark(mark_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM marks WHERE id=?", (mark_id,))
    conn.commit()
    conn.close()

# ==================== 主界面 ====================
st.title("📈 港股 KDJ 标记工具")

with st.sidebar:
    st.write(f"👤 已登录：{st.session_state.username}")
    if st.button("退出登录"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.rerun()

    st.markdown("---")
    st.subheader("📱 显示模式")
    if st.button("🔁 请求横屏"):
        st.components.v1.html(
            """
            <script>
            if (window.screen.orientation && window.screen.orientation.lock) {
                window.screen.orientation.lock('landscape').catch(() => {
                    alert('横屏锁定被拒绝，请手动旋转手机');
                });
            } else {
                alert('您的浏览器不支持自动横屏，请手动旋转');
            }
            </script>
            """,
            height=0,
        )
    st.caption("点击后浏览器可能请求横屏，如被拒绝请手动旋转。")

    st.markdown("---")
    st.subheader("⏰ 提醒配置（预留）")
    st.text_input("J值超买阈值", value="100", disabled=True)
    st.text_input("J值超卖阈值", value="0", disabled=True)
    st.caption("功能开发中...")

col1, col2 = st.columns([3, 1])
with col1:
    fixed_cols = st.columns(len(FIXED_STOCKS))
    for i, code in enumerate(FIXED_STOCKS):
        if fixed_cols[i].button(code, use_container_width=True):
            st.session_state.selected_stock = code
            st.session_state.manual_code = ""
    manual_code = st.text_input("或手动输入港股代码（如 0700.HK）", key="manual_code")
    if manual_code:
        if st.button("查看手动代码"):
            st.session_state.selected_stock = manual_code.upper()

if "selected_stock" not in st.session_state:
    st.session_state.selected_stock = FIXED_STOCKS[0]

current_stock = st.session_state.selected_stock

period_label = st.selectbox("选择周期", list(PERIOD_MAP.keys()))
period_str = PERIOD_MAP[period_label]

show_daily_kdj = st.checkbox("叠加日线 KDJ（仅分钟周期有效）", value=False)

# 强制清除 Streamlit 服务器端缓存（一次性）
if st.button("🧹 清除服务器缓存"):
    st.cache_data.clear()
    st.success("服务器缓存已清除，请刷新页面")

with st.spinner("正在下载数据..."):
    df = load_and_calc(current_stock, period_str)

if df is None:
    st.error(f"❌ 无法获取 {current_stock} 的 {period_label} 数据，请检查代码或稍后重试。")
    st.stop()

st.success(f"✅ 已加载 {len(df)} 条数据")

daily_kdj = None
if show_daily_kdj and period_label not in ["月线", "周线", "日线"]:
    daily_kdj = get_daily_kdj(current_stock)
    if daily_kdj is None:
        st.warning("无法获取日线 KDJ 数据")

# ---- 绘图 ----
if df.index.tz is not None:
    df.index = df.index.tz_localize(None)

fig = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=[0.6, 0.4],
    subplot_titles=(f"{current_stock} K线 & 均线", "KDJ 指标")
)

fig.add_trace(
    go.Candlestick(
        x=df.index,
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name="K线",
        increasing_line_color='red',
        decreasing_line_color='green',
    ),
    row=1, col=1
)

colors = ['blue', 'orange', 'purple', 'brown']
for i, ma in enumerate(MA_PERIODS):
    col_name = f'MA{ma}'
    if col_name in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df[col_name], mode='lines',
                name=col_name, line=dict(color=colors[i], width=1)
            ),
            row=1, col=1
        )

for key, color, dash in [('K', 'blue', None), ('D', 'orange', None), ('J', 'red', 'dot')]:
    if key in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df[key], mode='lines',
                name=key, line=dict(color=color, width=1.5, dash=dash)
            ),
            row=2, col=1
        )

if daily_kdj is not None:
    combined = pd.concat([df[['K']], daily_kdj], axis=1).ffill()
    for val, color in [('KDaily', 'cyan'), ('DDaily', 'magenta'), ('JDaily', 'yellow')]:
        if val in combined.columns:
            fig.add_trace(
                go.Scatter(
                    x=combined.index, y=combined[val], mode='lines',
                    name=f'日线{val[0]}', line=dict(color=color, width=1, dash='dash'),
                    opacity=0.7
                ),
                row=2, col=1
            )

marks = get_marks(st.session_state.username, current_stock)
for mark in marks:
    mark_id, mark_time, price, direction, remark = mark
    try:
        mark_dt = pd.to_datetime(mark_time)
    except:
        continue
    color = 'red' if direction == '买入' else 'green'
    symbol = 'triangle-up' if direction == '买入' else 'triangle-down'
    fig.add_trace(
        go.Scatter(
            x=[mark_dt], y=[price],
            mode='markers+text',
            marker=dict(color=color, size=10, symbol=symbol),
            text=[f"{direction}<br>{remark}"],
            textposition="top center",
            showlegend=False,
        ),
        row=1, col=1
    )

fig.update_layout(
    xaxis_rangeslider_visible=False,
    hovermode='x unified',
    height=700,
    margin=dict(l=10, r=10, t=30, b=10),
)
fig.update_yaxes(title_text="价格", row=1, col=1)
fig.update_yaxes(title_text="KDJ", row=2, col=1, range=[-20, 120])

st.plotly_chart(fig, use_container_width=True)

# ---- 标记管理 ----
st.subheader("✏️ 买卖标记管理")
tab_add, tab_view, tab_modify = st.tabs(["添加标记", "查看/删除", "修改标记"])

with tab_add:
    col_a1, col_a2, col_a3, col_a4 = st.columns([2, 1, 1, 2])
    with col_a1:
        mark_time = st.text_input("标记时间（YYYY-MM-DD HH:MM）", value=datetime.now().strftime("%Y-%m-%d %H:%M"))
    with col_a2:
        mark_dir = st.selectbox("方向", ["买入", "卖出"])
    with col_a3:
        mark_remark = st.selectbox("备注", REMARK_OPTIONS)
    with col_a4:
        try:
            mark_dt = pd.to_datetime(mark_time)
            if mark_dt in df.index:
                auto_price = df.loc[mark_dt, 'close']
            else:
                idx = df.index.get_indexer([mark_dt], method='nearest')[0]
                auto_price = df.iloc[idx]['close']
        except:
            auto_price = 0.0
        price_input = st.number_input("价格", value=float(auto_price), step=0.01)

    if st.button("✅ 添加标记"):
        add_mark(st.session_state.username, current_stock, mark_time, price_input, mark_dir, mark_remark)
        st.success("标记已保存")
        st.rerun()

with tab_view:
    if marks:
        mark_df = pd.DataFrame(marks, columns=["ID", "时间", "价格", "方向", "备注"])
        st.dataframe(mark_df.set_index("ID"), use_container_width=True)
        delete_id = st.number_input("输入要删除的标记ID", min_value=1, step=1)
        if st.button("🗑️ 删除选中标记"):
            delete_mark(delete_id)
            st.success("已删除")
            st.rerun()
    else:
        st.info("暂无标记")

with tab_modify:
    if marks:
        modify_id = st.number_input("输入要修改的标记ID", min_value=1, step=1, key="mod_id")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT mark_time, price, direction, remark FROM marks WHERE id=?", (modify_id,))
        row = c.fetchone()
        conn.close()
        if row:
            old_time, old_price, old_dir, old_remark = row
            new_time = st.text_input("新时间", value=old_time)
            new_price = st.number_input("新价格", value=float(old_price), step=0.01)
            new_dir = st.selectbox("新方向", ["买入", "卖出"], index=0 if old_dir=="买入" else 1)
            if old_remark in REMARK_OPTIONS:
                old_idx = REMARK_OPTIONS.index(old_remark)
            else:
                old_idx = 0
            new_remark = st.selectbox("新备注", REMARK_OPTIONS, index=old_idx)
            if st.button("✏️ 保存修改"):
                update_mark(modify_id, new_time, new_price, new_dir, new_remark)
                st.success("修改已保存")
                st.rerun()
        else:
            st.warning("标记ID不存在")
    else:
        st.info("暂无标记可修改")

st.caption(f"数据覆盖范围：{period_label}，最后更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
