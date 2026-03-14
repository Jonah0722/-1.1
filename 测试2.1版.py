import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import io

# ==========================================
# 1. 基础配置
# ==========================================
st.set_page_config(page_title="补货模型1.3 (纯流转版)", layout="wide")
pd.set_option("styler.render.max_elements", 5000000)

# ==========================================
# 2. 侧边栏：参数与设置
# ==========================================
with st.sidebar:
    st.header("🚚 补货模型 1.3")
    st.caption("版本特性：纯销量驱动，无金额干扰")
    
    # 文件上传
    st.subheader("1. 数据源")
    uploaded_file = st.file_uploader("上传SKU级数据 (Excel)", type=["xlsx", "xls"])
    
    st.markdown("---")
    st.subheader("2. 补货参数")
    
    # 核心参数
    target_wos = st.slider(
        "目标周转周数 (Target WOS)", 
        min_value=2, max_value=16, value=6,
        help="设定库存水位。例如6周，意味着库存要能支撑未来1.5个月的销售。"
    )
    
    moq = st.number_input("最小补货单位 (MOQ)", value=1, min_value=1)
    
    st.markdown("---")
    st.subheader("3. 熔断设置")
    str_threshold = st.slider("滞销熔断线 (售罄率 < ?%)", 0, 50, 20, 
                             help="如果售罄率低于此数值，系统将强制停止补货，防止死库存积压。")

# ==========================================
# 3. 数据处理 (精简版)
# ==========================================
df = None

if uploaded_file:
    try:
        raw_df = pd.read_excel(uploaded_file)
        cols = raw_df.columns.tolist()
        
        st.success("✅ 文件读取成功")
        
        # === 列名映射 (已移除金额列) ===
        with st.expander("🛠️ 数据列映射设置", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            def get_idx(opts, kws):
                for i, o in enumerate(opts):
                    for k in kws: 
                        if k in str(o): return i
                return 0

            col_sku = c1.selectbox("货号/SKU", cols, index=get_idx(cols, ['货号','SKU','款号']))
            col_cat = c2.selectbox("品类/大类", cols, index=get_idx(cols, ['品类','大类','分类']))
            col_sales_qty = c3.selectbox("近30天销量(件)", cols, index=get_idx(cols, ['销量','数量','30天']))
            col_stock = c4.selectbox("当前库存(件)", cols, index=get_idx(cols, ['库存','现存']))
            
            # 选填项
            has_transit = st.checkbox("包含'在途库存'", value=False)
            col_transit = None if not has_transit else st.selectbox("在途列", cols, index=get_idx(cols, ['在途']))

        # === 数据清洗 ===
        df = raw_df.copy()
        rename_map = {
            col_sku: '货号', col_cat: '品类',
            col_sales_qty: '销量', col_stock: '库存'
        }
        if has_transit and col_transit: rename_map[col_transit] = '在途'
        
        df = df.rename(columns=rename_map)
        if '在途' not in df.columns: df['在途'] = 0
        
        # 强制数值化
        for c in ['销量', '库存', '在途']:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    except Exception as e:
        st.error(f"数据处理错误: {e}")
        st.stop()
else:
    # 模拟数据 (无金额)
    st.info("👋 请上传数据。当前为演示模式。")
    data = []
    cats = ['运动', '休闲', '鞋类', '配件']
    for i in range(50):
        sales = np.random.randint(0, 300) if np.random.random() > 0.3 else 0
        stock = np.random.randint(0, 500)
        data.append({
            '货号': f'SKU_{i:03d}',
            '品类': np.random.choice(cats),
            '销量': sales,
            '库存': stock,
            '在途': 0
        })
    df = pd.DataFrame(data)

# ==========================================
# 4. 核心计算逻辑 (纯流转版)
# ==========================================

# 1. 基础指标
df['总盘口'] = df['销量'] + df['库存']
# 售罄率 = 销量 / (销量+库存)
df['售罄率'] = np.where(df['总盘口']>0, df['销量']/df['总盘口'], 0)
df['周流速'] = df['销量'] / 4.0

# 2. 补货计算
# 目标 = 周销 * 目标周数
df['目标库存'] = df['周流速'] * target_wos
# 缺口 = 目标 - (库存+在途)
df['缺口'] = df['目标库存'] - (df['库存'] + df['在途'])
df['缺口'] = df['缺口'].fillna(0)

# MOQ修正
def apply_moq(val, moq):
    if val <= 0: return 0
    return int(np.ceil(val / moq) * moq)

df['建议补货量'] = df['缺口'].apply(lambda x: apply_moq(x, moq))

# 3. 商品标签判定 (仅基于售罄率)
threshold_val = str_threshold / 100.0 # 转换百分比

def analyze_product_simple(row, limit):
    str_val = row['售罄率']
    
    # 售罄率极高 -> 爆款
    if str_val > 0.6: 
        return "🌟 爆款 (抓紧补)"
    # 售罄率过低 -> 滞销
    elif str_val < limit:
        return "🐢 滞销 (停止补)"
    # 中间态 -> 平销
    else:
        return "⚖️ 平销 (正常补)"

df['商品标签'] = df.apply(lambda row: analyze_product_simple(row, threshold_val), axis=1)

# 4. 熔断机制：滞销款强制不补
df.loc[df['商品标签'].str.contains("滞销"), '建议补货量'] = 0

# ==========================================
# 5. 可视化看板
# ==========================================
st.title("🛒 大型奥莱补货看板 (纯流转版)")

# --- KPI 卡片 ---
k1, k2, k3, k4 = st.columns(4)
total_sales = df['销量'].sum()
total_stock = df['库存'].sum()
avg_str = df['售罄率'].mean()
replenish_total = df['建议补货量'].sum()

k1.metric("近30天总销 (件)", f"{total_sales:,.0f}")
k2.metric("当前总库存 (件)", f"{total_stock:,.0f}")
k3.metric("平均售罄率", f"{avg_str:.1%}", help="反映货品周转效率")
k4.metric("建议补货总数", f"{replenish_total:,.0f} 件")

st.divider()

# --- 图表：库存供需分析 ---
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("📊 库存 vs 销量 (供需分布)")
    st.caption("左上角：销量高库存低（急缺） | 右下角：销量低库存高（积压）")
    
    # 只要有销量的或者有库存的，才展示在图上
    plot_df = df[(df['销量']>0) | (df['库存']>0)]
    
    fig = px.scatter(plot_df, x="库存", y="销量", 
                     color="商品标签", size="总盘口",
                     hover_data=['货号', '建议补货量', '售罄率'],
                     title="气泡大小=总盘口(货量)")
    
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("💡 货品结构分布")
    # 展示各标签的数量
    tag_counts = df['商品标签'].value_counts().reset_index()
    tag_counts.columns = ['标签类型', 'SKU数量']
    st.dataframe(tag_counts, hide_index=True, use_container_width=True)
    
    st.info(f"注：售罄率低于 {str_threshold}% 的滞销款，系统已自动拦截补货建议。")

# --- 详细表格 ---
st.subheader("📋 补货执行清单")

# 排序优先看需要补货的，且是爆款
final_df = df.sort_values(by=['建议补货量', '售罄率'], ascending=[False, False])

# 样式函数
def highlight_rows(row):
    if "爆款" in row['商品标签']:
        return ['background-color: #d4edda'] * len(row) # 绿底
    elif "滞销" in row['商品标签']:
        return ['color: #adb5bd'] * len(row) # 灰字
    return [''] * len(row)

# 展示列
cols_show = ['货号', '品类', '商品标签', '售罄率', '销量', '库存', '在途', '建议补货量']
cols_show = [c for c in cols_show if c in df.columns]

st.dataframe(
    final_df[cols_show].style
    .format({'售罄率': '{:.1%}', '建议补货量': '{:.0f}'})
    .apply(highlight_rows, axis=1)
)

# 导出功能
output = io.BytesIO()
with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
    final_df.to_excel(writer, index=False)
    
st.download_button("📥 下载补货建议表 (Excel)", output.getvalue(), "补货建议_纯流转版.xlsx")
