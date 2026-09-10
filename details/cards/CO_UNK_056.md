# CO_UNK_056

来源定位：HCO_0056 / 6.1.6 （12） / 段落3218

正式节点：CO_UNK_056
来源 ID：HCO_0056
科目：计算机组成原理
主知识点：CO06-03 总线性能指标
核心考点：有效数据率等于每次传送量乘每秒传送次数，需统一 bit 与 Byte、时钟与传送周期口径
主模块：CO06 总线与输入输出系统
题型：单项选择题 / 总线性能指标概念计算题
解析来源：2026计算机组成原理_带书签.pdf 第 300、301 页同题解析；current solution-01.png 与 BATCH-015 root recovery SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-015 current question direct visual exact / Wangdao question page 298 / current single-item solution crop / WD2026-CO06-6.1.6-MCQ-12 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：ESjq8gF0Qju
- OO3 路径：408 > 计算机组成原理 > 2.计算机组成原理 > 第六章 总线 > 总线的性能指标 > 6.1.6 （12） HCO_0056 CO_UNK_056

> [!question] 题目
> ![[assets/CO_UNK_056/question-01.png]]

> [!answer]- 解析
>
> 正确答案：**B**  
> 
> 计算过程：
> - 32 位字 = 4 B。
> - 传送 1 个 32 位字需要 5 个时钟周期。
> - 时钟频率为 500 MHz，即每秒 500M 个周期。
> - 每秒可传送的字数为 500M / 5 = 100M 个 32 位字。
> - 传输速率 = 100M × 4 B = 400 MB/s。
> 
> 关键：先把 **总线宽度位数 ÷ 8** 转成字节数，再除以传送该字所需周期数。

## 图片 QA 记录

- 2026-07-03 partial-pass V 批：直接查看当前题图，题图主体清晰可复做，右侧浅水印不遮挡关键文字，未见答案提示；当前无独立解析图，折叠解析文字已补足总线速率计算过程。Tutor 安全范围：`CO06-03`。

<!-- cs408-display-assets-v1:CS408-20260910-7aa18548684cec2c:start -->

## 原始资料与归档

> [!question]- 原始题面资料
> ![[assets/CO_UNK_056/question-798963708ea3f076.png]]

<!-- cs408-display-assets-v1:CS408-20260910-7aa18548684cec2c:end -->
