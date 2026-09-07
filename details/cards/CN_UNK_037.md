# CN_UNK_037

来源定位：HCN_0037 / 2.1.4(07) / 段落5917

正式节点：CN_UNK_037
来源 ID：HCN_0037
科目：计算机网络
主知识点：CN02-13 波特率
核心考点：由比特率与波特率的比值得到每码元信息量，再反求码元离散状态数；状态数是未知量。
主模块：CN02 物理层
题型：单项选择题 / 由比特率和波特率反求状态数
解析来源：2026计算机网络_带书签.pdf（解析页 50，定位 2.1.4（07））；current solution-01.png 与恢复源及 challenge SHA-256 一致
解析来源角色：独立同题解析用于复核模型派生安全机制；不据解析追加或改写用户错因。
匹配方式：BATCH-012 current question/solution direct visual exact / challenge publishable / current SHA-256 bound / formal identity, dates and user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/CN_UNK_037/question-01.png]]

> [!answer]- 解析
>
> # 错题回顾：波特率与比特率
> 
> ## 题目
> 某信道波特率 = 1000 Baud，数据速率 = 4 kb/s，问一个码元有效离散值个数？
> 
> 正确答案：D. 16
> 
> ---
> 
> ## 知识点
> - 关系式：Rb = Rs × log₂n
> - Rb：比特率 (bps)，Rs：波特率 (Baud)，n：码元有效离散值个数
> - 当 n=2 时，波特率 = 比特率；当 n>2 时，波特率 < 比特率。
> 
> ---
> 
> ## 解题过程
> 已知 Rb = 4000 bps，Rs = 1000 Baud  
> → log₂n = 4000 / 1000 = 4  
> → n = 2⁴ = 16  
> 
> ---
> 
> ## 错因分析
> - 忽视了波特率与比特率的区别
> - 忘记公式 Rb = Rs × log₂n
> - 误以为波特率 = 比特率
> 
> ---
> 
> ## 拓展
> - 奈奎斯特定理：Rb = 2W·log₂n
> - 香农定理：Rb ≤ W·log₂(1+SNR)
> - 常考点：编码方式与波特率/比特率关系

## 图片质检记录

- 2026-07-04：直接查看 `question-01.png` 并生成证据拼图 `indexes/qa-contact-sheets/cn-qonly-batch9-direct-review-2026-07-04.png`。题图主体清晰，轻微源水印不遮挡复做；已清理极少量边缘淡红像素，原图备份到 `assets/_quarantine/red_trace_original_2026-07-04_batch9/CN_UNK_037/`。当前无独立 `solution-01.png`，折叠解析与波特率/比特率换算主题一致，可阶段性复做。
