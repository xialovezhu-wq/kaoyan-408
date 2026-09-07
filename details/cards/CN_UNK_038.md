# CN_UNK_038

来源定位：HCN_0038 / 2.1.4（09） / 段落5922

正式节点：CN_UNK_038
来源 ID：HCN_0038
科目：计算机网络
主知识点：CN02-13 波特率
核心考点：先由离散状态数得到每码元信息量，再由比特率反求波特率；不采用与自身计算过程冲突的折叠结果行。
主模块：CN02 物理层
题型：单项选择题 / 由状态数和比特率反求波特率
解析来源：2026计算机网络_带书签.pdf（解析页 51，定位 2.1.4（09））；current solution-01.png 与恢复源及 challenge SHA-256 一致
解析来源角色：独立同题解析用于裁决折叠结果行与其计算过程的冲突；不改用户事实。
匹配方式：BATCH-012 current question/solution direct visual exact / challenge publishable / current SHA-256 bound / formal identity, dates and user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/CN_UNK_038/question-01.png]]

> [!answer]- 解析
>
> # 错题回顾：波特率与比特率计算
> 
> ## 题目
> 信号速率 = 64 kb/s，码元有 4 个有效离散值，求波特率？
> 
> 正确答案：B. 32 kBaud
> 
> ---
> 
> ## 知识点
> - Rb = Rs × log₂n
> - Rb：比特率，Rs：波特率，n：码元有效离散值个数
> - log₂n 才是码元包含的信息量（bit/码元）
> 
> ---
> 
> ## 解题过程
> 已知 Rb = 64 kb/s，n=4  
> → 每码元信息量 = log₂4 = 2 bit  
> → Rs = 64k ÷ 2 = 32k Baud  
> 
> 注意：必须取 log₂，不能直接用 4！
> 
> ---
> 
> ## 错因分析
> - 忽视 log₂ 转换
> - 误把“离散值个数”当作比特数
> - 导致误把“离散值个数”直接当作比特数；本题应先取 log₂ 后再换算波特率。
> 
> ---
> 
> ## 拓展
> - 奈奎斯特定理：Rb = 2W·log₂n
> - 香农定理：Rb ≤ W·log₂(1+SNR)
> - 常见情况：n=2 时，波特率 = 比特率；n>2 时，波特率 < 比特率

## 图片质检记录

- 2026-07-03：直接查看 `question-01.png`，题图与“波特率与比特率计算”主题一致；左侧红色作答痕迹已清理，原图备份到 `assets/_quarantine/red_trace_original_2026-07-03_batch_y/CN_UNK_038/question-01.png`。折叠解析原先存在计算过程与标注结论不一致，已按公式换算结果修正；当前题图 + 折叠解析可阶段性复做，后续可补独立解析图。
- 2026-07-04：再次直接查看 `question-01.png` 并生成证据拼图 `indexes/qa-contact-sheets/cn-qonly-batch9-direct-review-2026-07-04.png`。题图主体清晰，仍有左侧淡红残痕，已清理并将本次清理前原图备份到 `assets/_quarantine/red_trace_original_2026-07-04_batch9/CN_UNK_038/`。当前仍无独立 `solution-01.png`，折叠解析与波特率/比特率换算主题一致，可阶段性复做。
