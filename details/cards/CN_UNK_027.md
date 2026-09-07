# CN_UNK_027

来源定位：HCN_0027 / 2.1.4（11） / 段落5939

正式节点：CN_UNK_027
来源 ID：HCN_0027
科目：计算机网络
主知识点：CN02-15 奈奎斯特定理
核心考点：无噪声带限信道先由状态数求每码元信息量，再用奈奎斯特码元率上限换算最大比特率。
主模块：CN02 物理层
题型：单项选择题 / 奈奎斯特上限计算题
解析来源：2026计算机网络_带书签.pdf（解析页 51，定位 2.1.4（11））；current solution-01.png 与恢复源及 challenge SHA-256 一致
解析来源角色：独立同题解析用于复核模型派生安全机制；不据解析追加或改写用户错因。
匹配方式：BATCH-012 current question/solution direct visual exact / challenge publishable / current SHA-256 bound / formal identity, dates and user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/CN_UNK_027/question-01.png]]

> [!answer]- 解析
>
> # 错题回顾：奈奎斯特定理（无噪声信道）
> 
> ## 题目
> 对于某带宽为 4000 Hz 的低通信道，采用 16 种不同的物理状态 来表示数据。按照奈奎斯特定理，信道的最大传输速率是（ ）
> A. 4 kb/s  B. 8 kb/s  C. 16 kb/s  D. 32 kb/s
> 
> 正确答案：D
> 
> ---
> 
> ## 考纲速记
> - 码元电平数/物理状态数 V；每码元信息量 = log₂V（bit/码元）  
> - 奈奎斯特定理（无噪声、带宽受限）：  
>   Rb = 2W · log₂V（b/s），其中 W 为带宽（Hz）  
> 
> ---
> 
> ## 解题
> - 已知 W = 4000 Hz，V = 16 → log₂16 = 4 bit/码元  
> - 代入奈奎斯特：  
>   Rb = 2 × 4000 × 4 = 32000 b/s = 32 kb/s → 选 D
> 
> ---
> 
> ## 易错点
> - 把 V=16 误当成“每码元 16 bit”，正确应先取 log₂V。  
> - 忘记“无噪声极限 2W 只是码元率上限”，真正的数据速率还要乘 log₂V。
> 
> ---
> 
> ## 小拓展（统一对比）
> 1) 给带宽/电平数（无噪声）：Rb = 2W·log₂V  
> 2) 给波特率 Rs 与电平数：Rb = Rs·log₂V  
> 3) 有噪声（香农）：Rb ≤ W·log₂(1+SNR) —— 电平数再多也受 SNR 限制  
> 4) 二进制信号（V=2）：log₂V=1 ⇒ 比特率 = 波特率；V>2 时比特率 > 波特率
> 
> ---
> 
> ## 一句记忆
> > “两倍带宽管码元，log₂电平定比特。”

## 图片质检记录

- 2026-07-03：直接查看 `question-01.png`，题图与“奈奎斯特定理速率计算”主题一致；左侧红色作答痕迹已清理，原图备份到 `assets/_quarantine/red_trace_original_2026-07-03_batch_y/CN_UNK_027/question-01.png`。折叠解析与题图同主题；当前题图 + 折叠解析可阶段性复做，后续可补独立解析图。
- 2026-07-04：再次直接查看 `question-01.png`，题图主体清晰，左侧淡红痕不遮挡题设或选项主体；当前无独立 `solution-01.png`，折叠解析与奈奎斯特定理速率计算主题一致。证据图：`indexes/qa-contact-sheets/cn-qonly-batch7-direct-review-2026-07-04.png`。
