# CO_UNK_024

来源定位：HCO_0024 / 5.1.4 （10） / 段落3000

正式节点：CO_UNK_024
来源 ID：HCO_0024
科目：计算机组成原理
主知识点：CO05-05 寄存器
核心考点：程序计数器的顺序推进与执行阶段转移改写是独立状态事件，条件转移还受条件是否成立约束
主模块：CO05 中央处理器 CPU
题型：单项选择题
解析来源：2026计算机组成原理_带书签.pdf 第 223 页同题解析；current solution-01.png 与 BATCH-014 恢复回执 SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-014 current question/solution direct visual exact / Wangdao question page 221 and solution page 223 / WD2026-CO05-5.1.4-MCQ-10 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：z9OtPilxSaK
- OO3 路径：408 > 计算机组成原理 > 2.计算机组成原理 > 第五章 中央处理器 > CPU的功能和基本结构结构 > 5.1.4 （10）HCO_0024 CO_UNK_024

> [!question] 题目
> ![[assets/CO_UNK_024/question-01.png]]

> [!answer]- 解析
>
> # 错题回顾：程序计数器（PC）
> 
> 题目：  
> 关于 PC 的描述中，错误的是（）。
> 
> A. PC 中总是存放指令地址  
> B. PC 的值由 CPU 在执行指令过程中进行修改  
> C. 执行转移指令时，PC 的值总是修改为转移指令的目标地址  
> D. PC 的位数一般和 MAR 的位数一样  
> 
> 正确答案： C  
> 我的错误： 误以为总是修改  
> 
> ---
> 
> ## 知识点总结
> - PC 作用：存放下一条将要执行指令的地址  
> - 更新方式：  
>   - 顺序执行 → PC+1  
>   - 转移指令 → PC 改为目标地址（但不一定“总是”，条件转移时未必改变）  
> - 位数：与 MAR 位数相同  
> 
> ---
> 
> ✅ 记忆要点  
> - PC 不是“总是”被修改为目标地址，要区分条件转移与无条件转移。

## 图片 QA 记录

- 2026-07-03 partial-pass T 批：直接查看当前题图，题图主体清晰可复做，题号/来源头部裁切不影响复做；当前无独立解析图，但折叠解析文字与 PC 更新规则主题一致。Tutor 安全范围：`CO05-05`、`CO01-08`。
