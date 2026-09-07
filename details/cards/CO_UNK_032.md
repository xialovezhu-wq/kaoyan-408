# CO_UNK_032

来源定位：HCO_0032 / 5.7.5 （09） / 段落3187

正式节点：CO_UNK_032
来源 ID：HCO_0032
科目：计算机组成原理
主知识点：CO05-32 多处理器基本概念
核心考点：多核处理器的核心组织、Cache 组织、软件并行性与操作系统多任务属于不同层次
主模块：CO05 中央处理器 CPU
题型：单项选择题 / 概念辨析题
解析来源：2026计算机组成原理_带书签.pdf 第 291 页同题解析；current solution-01.png 与 BATCH-014 恢复回执 SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-014 current question/solution direct visual exact / Wangdao question page 289 and solution page 291 / WD2026-CO05-5.7.5-MCQ-09 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：UtwsR_zITxe
- OO3 路径：408 > 计算机组成原理 > 2.计算机组成原理 > 第五章 中央处理器 > 多处理的基本概念 > 多核处理器的基本概念 > 5.7.5 （09） HCO_0032 CO_UNK_032

> [!question] 题目
> ![[assets/CO_UNK_032/question-01.png]]

> [!answer]- 解析
>
> ## 我的错误
> - 错选 **A**，认为每个核心一定有自己的 Cache。  
> - 实际上核心既可以有独立 Cache，也可以共享 Cache。
> 
> ---
> 
> ## 正确答案
> - **C**：多核 CPU = 在一颗 CPU 芯片中集成多个完整执行内核 → 并行执行多个任务。
> 
> ---
> 
> ## 知识点总结
> - 多核 CPU：一颗 CPU 内含多个内核（core），每个内核相当于一个独立的处理单元。  
> - Cache：各核心 **既可独立 Cache**，也可 **共享部分 Cache**（如 LLC）。  
> - 多核 ≠ 多任务操作系统，单核 CPU 也能运行多任务（靠操作系统的时间片轮转）。  
> 
> ---
> 
> ## 易错点
> - **A 错误**：Cache 并非一定独立。  
> - **B 错误**：单个程序不能天然分配到多个核心，需依赖并行编程。  
> - **D 错误**：多任务 OS 不依赖多核，单核也能支持。

## 图片 QA 记录

- 2026-07-03 partial-pass U 批：直接查看当前题图，题图主体清晰可复做，未见作答痕迹或答案提示；当前无独立解析图，但折叠解析文字与多核 CPU/Cache 组织边界主题一致。Tutor 安全范围：`CO05-32`。
