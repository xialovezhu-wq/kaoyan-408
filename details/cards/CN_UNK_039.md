# CN_UNK_039

来源定位：HCN_0039 / 2.1.4（10） / 段落5927

正式节点：CN_UNK_039
来源 ID：HCN_0039
科目：计算机网络
主知识点：CN02-15 奈奎斯特定理
核心考点：由状态数求每码元信息量，将题设采样或符号速率与奈奎斯特码元率上限比较，取更严格者后换算比特率。
主模块：CN02 物理层
题型：单项选择题 / 奈奎斯特码元率取限计算题
解析来源：2026计算机网络_带书签.pdf（解析页 51，定位 2.1.4（10））；current solution-01.png 与恢复源及 challenge SHA-256 一致
解析来源角色：独立同题解析用于复核模型派生安全机制；不据解析追加或改写用户错因。
匹配方式：BATCH-012 current question/solution direct visual exact / challenge publishable / current SHA-256 bound / formal identity, dates and user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/CN_UNK_039/question-01.png]]

> [!answer]- 解析
>
> # 错题回顾：奈奎斯特定理与码元电平
> 
> ## 题目
> 8 kHz 信道，无噪声，每信号 8 级，每秒采样 24k 次，最大传输速率？
> 
> 正确答案：C. 48 kb/s
> 
> ---
> 
> ## 知识点
> - 奈奎斯特定理：Rb = 2W·log₂V
> - W：信道带宽，V：码元电平数
> - log₂V = 每码元可携带的信息量(bit/码元)
> 
> ---
> 
> ## 解题过程
> W = 8 kHz，V = 8  
> → log₂8 = 3 bit  
> → Rb = 2·8k·3 = 48 kb/s
> 
> ---
> 
> ## 错因分析
> - 混淆了“8 级”的含义：它表示电平数，不是采样次数
> - 忽略了 log₂V
> 
> ---
> 
> ## 拓展
> - 奈奎斯特定理：无噪声，速率随带宽和电平数增加
> - 香农定理：有噪声，速率受 SNR 限制
> - V=2 时：波特率=比特率；V>2 时：波特率<比特率

## 图片质检记录

- 2026-07-04：直接查看 `question-01.png` 并生成证据拼图 `indexes/qa-contact-sheets/cn-qonly-batch9-direct-review-2026-07-04.png`。题图主体清晰，关键参数与问法可读；底部页码残留不遮挡复做。当前无独立 `solution-01.png`，折叠解析与奈奎斯特定理和码元电平主题一致，可阶段性复做。
