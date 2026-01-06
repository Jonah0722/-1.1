import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import io

# ==========================================
# 1. 基础配置
# ==========================================
st.set_page_config(page_title="补货模型1.2 ", layout="wide")
pd.set_option("styler.render.max_elements", 5000000)

# ==========================================
# 2. 侧边栏：参数与设置
# ==========================================
with st.sidebar:
    st.header("📈 补货模型 1.2")
    st.caption("修复：支持直接读取单件吊牌价")
    
    # 文件上传
    st.subheader("1. 数据源")
    uploaded_file = st.file_uploader("上传SKU级数据 (Excel)", type=["xlsx", "xls"])
    
    st.markdown("---")
    st.subheader("2. 补货参数")
    
    target_wos = st.slider("目标周转周数 (Target WOS)", 2, 16, 6)
    moq = st.number_input("最小补货单位 (MOQ)", value=1, min_value=1)
    
    st.markdown("---")
    st.subheader("3. 阈值设置")
    high_discount_threshold = st.slider("低折扣预警线 (< ?%)", 0, 100, 30, 
                                      help="如果折扣率低于30%（即3折），视为低价清仓品。")

# ==========================================
# 3. 数据处理
# ==========================================
df = None

if uploaded_file:
    try:
        raw_df = pd.read_excel(uploaded_file)
        cols = raw_df.columns.tolist()
        
        st.success("✅ 文件读取成功")
        
        # === 列名映射 (修改了这里) ===
        with st.expander("🛠️ 数据列映射 (请仔细核对)", expanded=True):
            st.info("👇 注意：最后一项请选择【单件】的吊牌价/原价")
            c1, c2, c3, c4 = st.columns(4)
            def get_idx(opts, kws):
                for i, o in enumerate(opts):
                    for k in kws: 
                        if k in str(o): return i
                return 0

            col_sku = c1.selectbox("货号/SKU", cols, index=get_idx(cols, ['货号','SKU']))
            col_sales_qty = c2.selectbox("销量(件)", cols, index=get_idx(cols, ['销量','数量']))
            col_stock = c3.selectbox("库存(件)", cols, index=get_idx(cols, ['库存','现存']))
            
            # 新增：金额相关
            col_sales_amt = c4.selectbox("销售金额(实收)", cols, index=get_idx(cols, ['金额','实收','GMV']))
            
            # --- 关键修改点：明确这是单价 ---
            col_tag_price = st.selectbox("吊牌价 (单件/标准价)", cols, index=get_idx(cols, ['吊牌','码洋','标准','单价']))

            has_transit = st.checkbox("包含'在途库存'", value=False)
            col_transit = None if not has_transit else st.selectbox("在途列", cols, index=get_idx(cols, ['在途']))

        # === 数据清洗 ===
        df = raw_df.copy()
        rename_map = {
            col_sku: '货号', col_sales_qty: '销量', col_stock: '库存',
            col_sales_amt: '销售额', col_tag_price: '吊牌单价' # 映射为单价
        }
        if has_transit and col_transit: rename_map[col_transit] = '在途'
        
        df = df.rename(columns=rename_map)
        if '在途' not in df.columns: df['在途'] = 0
        
        # 数值化处理
        num_cols = ['销量', '库存', '在途', '销售额', '吊牌单价']
        for c in num_cols:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    except Exception as e:
        st.error(f"数据处理错误: {e}")
        st.stop()
else:
    # 模拟数据生成
    st.info("👋 请上传数据。当前为演示模式。")
    data = []
    for i in range(50):
        sales = np.random.randint(0, 200)
        stock = np.random.randint(0, 400)
        tag_price = 1000 # 单价
        discount = np.random.choice([0.2, 0.3, 0.5, 0.7]) 
        
        data.append({
            '货号': f'SKU_{i:03d}',
            '销量': sales,
            '库存': stock,
            '在途': 0,
            '销售额': sales * tag_price * discount,
            '吊牌单价': tag_price # 只有单价
        })
    df = pd.DataFrame(data)

# ==========================================
# 4. 核心计算逻辑 (1.2版本)
# ==========================================

# 1. 基础指标
df['总盘口'] = df['销量'] + df['库存']
df['售罄率'] = np.where(df['总盘口']>0, df['销量']/df['总盘口'], 0)
df['周流速'] = df['销量'] / 4.0

# --- 关键修改点：自动计算吊牌总额 ---
# 逻辑：吊牌总额 = 销量 * 吊牌单价
df['吊牌总额'] = df['销量'] * df['吊牌单价']

# 2. 折扣率计算 (实收 / 吊牌总额)
# 避免除以0
df['平均折扣率'] = np.where(df['吊牌总额']>0, df['销售额']/df['吊牌总额'], 0)

# 3. 补货计算
df['目标库存'] = df['周流速'] * target_wos
df['缺口'] = df['目标库存'] - (df['库存'] + df['在途'])
df['缺口'] = df['缺口'].fillna(0)

def apply_moq(val, moq):
    if val <= 0: return 0
    return int(np.ceil(val / moq) * moq)

df['建议补货量'] = df['缺口'].apply(lambda x: apply_moq(x, moq))

# 4. 商品分层逻辑
def analyze_product(row):
    if row['售罄率'] > 0.5 and row['平均折扣率'] > 0.5:
        return "🌟 爆款 (优先补)"
    elif row['售罄率'] > 0.5 and row['平均折扣率'] < 0.3:
        return "🔥 跑量 (谨慎补)"
    elif row['售罄率'] < 0.2:
        return "🐢 滞销 (勿补)"
    else:
        return "⚖️ 平销款 (正常补)"

df['商品分析标签'] = df.apply(analyze_product, axis=1)

# 5. 修正补货建议：滞销款强制为0
df.loc[df['商品分析标签'].str.contains("滞销"), '建议补货量'] = 0

# ==========================================
# 5. 可视化看板
# ==========================================
st.title("🛒 补货模型 1.2 ")

# --- KPI ---
k1, k2, k3, k4 = st.columns(4)
total_gmv = df['销售额'].sum()
# 全场平均折扣 = 总销售额 / 总吊牌额
avg_discount = df['销售额'].sum() / df['吊牌总额'].sum() if df['吊牌总额'].sum() > 0 else 0
need_replenish_amt = df[df['建议补货量']>0]['建议补货量'].sum()

k1.metric("近30天GMV", f"¥{total_gmv:,.0f}")
k2.metric("平均折扣率", f"{avg_discount:.1%}", delta_color="off", help="公式：总实收 / (销量*单价)")
k3.metric("平均售罄率", f"{df['售罄率'].mean():.1%}")
k4.metric("建议补货总数", f"{need_replenish_amt:,.0f} 件")

st.divider()

# --- 图表 ---
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("📊 商品四象限分析")
    # 过滤掉异常数据（比如没销量的）以便图表好看
    plot_df = df[df['销量'] > 0]
    fig = px.scatter(plot_df, x="平均折扣率", y="售罄率", 
                     color="商品分析标签", size="销量",
                     hover_data=['货号', '建议补货量'],
                     title="气泡大小=销量")
    
    fig.add_hline(y=0.5, line_dash="dash", line_color="grey")
    fig.add_vline(x=0.3, line_dash="dash", line_color="red")
    
    # 锁定X轴范围在0到1.2之间，防止有极个别数据把图撑得太宽
    fig.update_xaxes(range=[0, 1.2], tickformat='.0%')
    fig.update_yaxes(tickformat='.0%')
    
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("💡 标签分布")
    st.dataframe(df['商品分析标签'].value_counts(), width=300)

# --- 表格 ---
st.subheader("📋 智能补货建议单")

final_df = df.sort_values(by=['商品分析标签', '建议补货量'], ascending=[False, False])

def highlight_style(row):
    val = row['商品分析标签']
    if "爆款" in val: return ['background-color: #d4edda'] * len(row)
    elif "跑量" in val: return ['background-color: #fff3cd'] * len(row)
    elif "滞销" in val: return ['color: #adb5bd'] * len(row)
    return [''] * len(row)

# 展示列
show_cols = ['货号', '商品分析标签', '平均折扣率', '售罄率', '销量', '库存', '缺口', '建议补货量', '吊牌单价', '吊牌总额']

st.dataframe(
    final_df[show_cols].style
    .format({
        '平均折扣率': '{:.0%}', 
        '售罄率': '{:.1%}',
        '建议补货量': '{:.0f}',
        '缺口': '{:.0f}',
        '吊牌总额': '{:,.0f}'
    })
    .apply(highlight_style, axis=1)
)

# 导出
output = io.BytesIO()
with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
    final_df.to_excel(writer, index=False)
    
st.download_button("📥 下载完整分析表 (Excel)", output.getvalue(), "补货模型1.2.xlsx")
