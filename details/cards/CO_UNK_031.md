# CO_UNK_031

来源定位：HCO_0031 / 5.7.5 （03） / 段落3164

正式节点：CO_UNK_031
来源 ID：HCO_0031
科目：计算机组成原理
主知识点：CO05-32 多处理器基本概念
核心考点：按指令流数量与数据流数量区分 SISD、SIMD 等并行处理类别
主模块：CO05 中央处理器 CPU
题型：单项选择题 / 概念辨析题
解析来源：2026计算机组成原理_带书签.pdf 第 290 页同题解析；current solution-01.png 与 BATCH-014 恢复回执 SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-014 current question/solution direct visual exact / Wangdao question page 289 and solution page 290 / WD2026-CO05-5.7.5-MCQ-03 / formal identity, year, locator, dates and user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/CO_UNK_031/question-01.png]]

> [!answer]- 解析
>
> ## 正确答案
> D. 标量流水线处理机
> 
> ---
> 
> ## 我的错误
> - 错选 A（并行处理机），误以为它不属于 SIMD。  
> 
> ---
> 
> ## 知识点总结
> - SIMD（单指令流多数据流）：一条指令可同时处理多个数据。  
>   - 常见机器：阵列处理机、向量处理机、并行处理机。  
> - 标量流水线处理机：一次指令只能处理单个数据 → SISD，不属于 SIMD。  
> 
> ---
> 
> ## 易错点提醒
> - SIMD vs SISD：关键在于是否能 同时处理多个数据。  
> - 标量流水线 容易被张冠李戴成 SIMD，注意区分！

## 图片 QA 记录

- 2026-07-03 partial-pass U 批：直接查看当前题图，题图主体清晰可复做，未见作答痕迹或答案提示；当前无独立解析图，折叠解析文字与 SIMD/SISD 分类主题一致。已清理原折叠解析区串入的 MIMD、UMA/NUMA 泛化章节材料。Tutor 安全范围：`CO05-32`。
- 2026-07-03 高风险复核：已再次直接打开题图并扫描折叠解析区；未发现 MIMD、UMA/NUMA 泛化残留，解析文字已从“错配已修”降级为“同题文字解析已复核干净”。仍缺独立解析图；正式关系网不变。Tutor 安全范围：`CO05-32`。
