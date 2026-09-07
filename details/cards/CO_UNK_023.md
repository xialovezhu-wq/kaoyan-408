# CO_UNK_023

来源定位：HCO_0023 / 5.1.4 （08） / 段落2997

正式节点：CO_UNK_023
来源 ID：HCO_0023
科目：计算机组成原理
主知识点：CO05-07 取指周期
核心考点：取指时由 PC 提供下一条指令地址，MAR 只暂存本次主存访问地址
主模块：CO05 中央处理器 CPU
题型：单项选择题
解析来源：2026计算机组成原理_带书签.pdf 第 223 页同题解析；current solution-01.png 与 BATCH-014 恢复回执 SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-014 current question/solution direct visual exact / Wangdao question page 221 and solution page 223 / WD2026-CO05-5.1.4-MCQ-08 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：JEVFZPpXQqq
- OO3 路径：408 > 计算机组成原理 > 2.计算机组成原理 > 第五章 中央处理器 > CPU的功能和基本结构结构 > 5.1.4 （08） HCO_0023 CO_UNK_023

> [!question] 题目
> ![[assets/CO_UNK_023/question-01.png]]

> [!answer]- 解析
>
> ## 知识点总结
> - **取指令阶段**：指令总是由 **程序计数器（PC）** 提供地址，从主存中读出。  
> - **地址寄存器（MAR）**：只是用来暂存地址，并不是决定指令读取来源。  
> - **关键点**：指令取出地址 **始终来源于 PC**，不会用 MAR 直接控制取指。  
> 
> ---
> 
> ## 记忆口诀
> 👉 **取指靠 PC，MAR 只是搬运工。**

## 图片 QA 记录

- 2026-07-03 partial-pass T 批：直接查看当前题图，题图主体清晰可复做，题号/来源头部裁切不影响复做；当前无独立解析图，但折叠解析文字与取指地址来源主题一致。Tutor 安全范围：`CO05-05`、`CO01-08`。
