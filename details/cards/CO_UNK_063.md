# CO_UNK_063

来源定位：HCO_0066 / 7.3.4 (25) / 段落3534

正式节点：CO_UNK_063
来源 ID：HCO_0066
科目：计算机组成原理
主知识点：CO06-20 DMA 传送过程
核心考点：DMA 完整过程中的 CPU 与 DMA 控制器分阶段职责边界。
主模块：CO06 总线与输入输出系统
题型：选择题 / DMA 阶段职责辨析题
解析来源：2026计算机组成原理_带书签.pdf 第 340 页同题解析；current solution-01.png 与 BATCH-016 恢复回执 SHA-256 一致
解析来源角色：独立同题解析用于复核模型派生安全机制；不据解析追加或改写用户事实。
匹配方式：BATCH-016 current question/solution direct visual exact / root recovery receipt bound / formal identity, dates and user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/CO_UNK_063/question-01.png]]

> [!answer]- 解析
>
> 我的错误选择： C  
> 正确答案： A  
> 
> ---
> 
> ### 考点总结
> 1. DMA 的三个阶段：
>    - 预处理阶段：CPU 负责初始化 DMA 控制器（传输方向、首地址、传输长度等）。  
>    - 数据传输阶段：DMA 控制器直接接管总线，进行数据传送。  
>    - 后处理阶段：DMA 控制结束后，CPU 通过中断服务程序进行收尾（如结果处理）。  
> 
> 2. 关键点：
>    - DMA 不是完全由 CPU 控制（排除 B、D）。  
>    - 也不是完全由 DMA 控制器独立完成，CPU 在前后阶段必须参与（排除 C）。  
>    - 因此正确描述是 部分 CPU 控制 + 部分 DMA 控制器控制。  
> 
> ---
> 
> ### 正确结论
> - 完整 DMA 过程 = CPU（前后处理） + DMA 控制器（数据传输）  
> - 考研 408 常考点：分工明确，CPU 不完全脱离，也不全程主导。

## 图片 QA 记录

- 2026-07-03 partial-pass W 批：直接查看当前题图，题图主体清晰可复做，右侧浅水印不遮挡关键文字，未见答案提示；当前无独立解析图，但折叠解析文字与 DMA 过程阶段和 CPU 参与边界主题一致。Tutor 安全范围：`CO06-18`、`CO06-20`。
