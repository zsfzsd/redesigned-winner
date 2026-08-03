import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sqlite3
import hashlib
from datetime import datetime, timedelta

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
# yfinance 原生支持的间隔
YF_INTERVALS = ["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"]
# 需要从更细粒度合成的周期
CUSTOM_INTERVALS = {
    "120min": {"base": "5m", "rule": "2h"},   # 用5分钟数据 resample 成2小时=120分钟
    "85min": {"base": "5m", "rule": "85min"},  # 直接 resample 85分钟
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

# ==================== 用户登录 ====================
def init_db():
    """初始化用户表和标记表"""
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
    # 插入默认用户（用户自行修改密码）
    default_user = "admin"
    default_pass = hashlib.sha256("admin".encode()).hexdigest()
    try:
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (default_user, default_pass))
    except sqlite3.IntegrityError:
        pass
    conn.commit()
    conn.close()

init_db()

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

# 登录状态管理
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

# ==================== 数据获取函数 ====================
def get_data(ticker, period_str, days_back=7):
    """
    获取港股分钟/日/周/月数据。
    对于自定义周期，用更细粒度数据合成。
    """
    # 处理月线、周线、日线 —— 需要更长的历史保证 KDJ 计算
    if period_str in ["1mo", "1wk", "1d"]:
        if period_str == "1d":
            df = yf.download(ticker, period="3mo", interval="1d", progress=False)
        elif period_str == "1wk":
            df = yf.download(ticker, period="6mo", interval="1wk", progress=False)
        else:  # 1mo
            df = yf.download(ticker, period="2y", interval="1mo", progress=False)
        
        if df is None or df.empty:
            return None
        df = df[['Open','High','Low','Close']]
        df.columns = ['open','high','low','close']
        return df

    # 对于分钟线，用 period="7d" 获取7天内最高分辨率数据
    if period_str in YF_INTERVALS:
        df = yf.download(ticker, period="7d", interval=period_str, progress=False)
        if df is None or df.empty:
            return None
        df = df[['Open','High','Low','Close']]
        df.columns = ['open','high','low','close']
        return df

    # 自定义周期合成
    if period_str in CUSTOM_INTERVALS:
        base = CUSTOM_INTERVALS[period_str]["base"]
        rule = CUSTOM_INTERVALS[period_str]["rule"]
        df_base = yf.download(ticker, period="7d", interval=base, progress=False)
        if df_base is None or df_base.empty:
            return None
        df_base.columns = ['Open','High','Low','Close','Volume']
        # 重采样为自定义周期
        df_resampled = df_base.resample(rule).agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last'
        }).dropna()
        df_resampled.columns = ['open','high','low','close']
        return df_resampled
    return None

@st.cache_data(ttl=3600)
def load_and_calc(ticker, period_str):
    """加载数据，计算KDJ和均线，返回DataFrame"""
    try:
        df = get_data(ticker, period_str)
    except Exception as e:
        st.error(f"❌ 数据获取异常（{ticker} {period_str}）：{e}")
        return None

    # 诊断输出
    if df is None:
        st.warning(f"⚠️ get_data 返回 None，代码：{ticker}，周期：{period_str}")
        return None
    if df.empty:
        st.warning(f"⚠️ 数据为空 DataFrame，代码：{ticker}，周期：{period_str}")
        # 还可以打印列名看看
        st.write("返回的列：", df.columns.tolist())
        return None

    # 先显示一下前几行，确认有内容
    st.success(f"✅ 成功获取 {len(df)} 条数据")
    st.dataframe(df.head(3), use_container_width=True)

    # 计算均线
    for ma in MA_PERIODS:
        df[f'MA{ma}'] = df['close'].rolling(window=ma).mean()

    # 计算 KDJ
    low_min = df['low'].rolling(window=KDJ_N).min()
    high_max = df['high'].rolling(window=KDJ_N).max()
    rsv = (df['close'] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=KDJ_M1-1, adjust=False).mean()
    d = k.ewm(com=KDJ_M2-1, adjust=False).mean()
    j = 3 * k - 2 * d
    df['K'] = k
    df['D'] = d
    df['J'] = j
    st.write("最终要绘图的数据：", df[['close','K','D','J']].tail())
    return df.dropna()

def get_daily_kdj(ticker):
    """获取日线级别KDJ（用于叠加到分钟图）"""
    df_daily = yf.download(ticker, period="3mo", interval="1d", progress=False)
    if df_daily.empty:
        return None
    df_daily = df_daily[['Open','High','Low','Close']]
    df_daily.columns = ['open','high','low','close']
    low_min = df_daily['low'].rolling(window=KDJ_N).min()
    high_max = df_daily['high'].rolling(window=KDJ_N).max()
    rsv = (df_daily['close'] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=KDJ_M1-1, adjust=False).mean()
    d = k.ewm(com=KDJ_M2-1, adjust=False).mean()
    j = 3 * k - 2 * d
    df_daily['KDaily'] = k
    df_daily['DDaily'] = d
    df_daily['JDaily'] = j
    return df_daily[['KDaily','DDaily','JDaily']].dropna()

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

# ---- 侧边栏：退出登录和手机横屏按钮 ----
with st.sidebar:
    st.write(f"👤 {st.session_state.username}")
    if st.button("退出登录"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.rerun()

    st.markdown("---")
    st.subheader("📱 显示模式")
    if st.button("🔁 切换横屏/竖屏"):
        # 通过JS旋转页面（简单实现）
        st.components.v1.html(
            """
            <script>
            if (window.screen.orientation && window.screen.orientation.lock) {
                window.screen.orientation.lock('landscape').catch(() => {});
            } else {
                alert('请手动将手机横屏');
            }
            </script>
            """,
            height=0,
        )
    st.caption("点击按钮后，浏览器会自动请求横屏；如被拒绝请手动旋转。")

    st.markdown("---")
    st.subheader("⏰ 提醒配置（预留）")
    st.text_input("J值超买阈值", value="100", disabled=True, key="remind_overbought")
    st.text_input("J值超卖阈值", value="0", disabled=True, key="remind_oversold")
    st.caption("功能开发中，敬请期待")

# ---- 股票选择 ----
col1, col2 = st.columns([3, 1])
with col1:
    selected_stock = None
    fixed_cols = st.columns(len(FIXED_STOCKS))
    for i, code in enumerate(FIXED_STOCKS):
        if fixed_cols[i].button(code, use_container_width=True):
            st.session_state.selected_stock = code
            st.session_state.manual_code = ""
    # 手动输入
    manual_code = st.text_input("或手动输入代码（如 0700.HK）", key="manual_code")
    if manual_code:
        if st.button("查看手动代码"):
            st.session_state.selected_stock = manual_code.upper()

if "selected_stock" not in st.session_state:
    st.session_state.selected_stock = FIXED_STOCKS[0]  # 默认第一支

current_stock = st.session_state.selected_stock

# ---- 周期选择 ----
period_label = st.selectbox("选择周期", list(PERIOD_MAP.keys()))
period_str = PERIOD_MAP[period_label]

# ---- 叠加日线KDJ选项 ----
show_daily_kdj = st.checkbox("叠加日线 KDJ（仅分钟周期有效）", value=False)

# ---- 加载数据 ----
with st.spinner("正在下载数据..."):
    df = load_and_calc(current_stock, period_str)
    if df is None:
        st.error("获取数据失败，请检查代码或网络")
        st.stop()

    daily_kdj = None
    if show_daily_kdj and period_label not in ["月线", "周线", "日线"]:
        daily_kdj = get_daily_kdj(current_stock)

# ---- 临时诊断绘图 ----
fig = go.Figure()
fig.add_trace(go.Scatter(x=df.index, y=df['close'], mode='lines', name='收盘价'))
fig.update_layout(title="仅显示收盘价（诊断模式）", height=400)
st.write(type(df.index))
st.plotly_chart(fig, use_container_width=True)

# ==================== 标记管理面板 ====================
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
        # 自动获取最接近时间的价格
        try:
            mark_dt = pd.to_datetime(mark_time)
            if mark_dt in df.index:
                auto_price = df.loc[mark_dt, 'close']
            else:
                idx = df.index.get_indexer([mark_dt], method='nearest')[0]
                auto_price = df.iloc[idx]['close']
                mark_time = str(df.index[idx])  # 校正为实际时间
        except:
            auto_price = 0.0
        price_input = st.number_input("价格", value=float(auto_price), step=0.01)

    if st.button("✅ 添加标记"):
        add_mark(st.session_state.username, current_stock, mark_time, price_input, mark_dir, mark_remark)
        st.success("标记已保存")
        st.rerun()

with tab_view:
    if marks:
        # 转换为DataFrame展示
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
        # 获取当前标记信息
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
            new_remark = st.selectbox("新备注", REMARK_OPTIONS, index=REMARK_OPTIONS.index(old_remark) if old_remark in REMARK_OPTIONS else 0)
            if st.button("✏️ 保存修改"):
                update_mark(modify_id, new_time, new_price, new_dir, new_remark)
                st.success("修改已保存")
                st.rerun()
        else:
            st.warning("标记ID不存在")
    else:
        st.info("暂无标记可修改")

# ---- 数据增量更新提示 ----
st.caption(f"数据覆盖最近7天，最后更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
