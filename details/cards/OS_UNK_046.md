# OS_UNK_046

来源定位：HOS_0061 / 2.1.8 （17） / 段落3740

正式节点：OS_UNK_046
来源 ID：HOS_0061
科目：操作系统
主知识点：OS02-04 进程状态
核心考点：先按等待对象区分已具备运行条件但等待处理器的状态与等待外部事件的状态，再结合时间片轮转下大量可运行进程排队的系统特征。
主模块：OS02 进程管理
题型：单项选择题 / 状态数量特征辨析题
解析来源：2026操作系统_带书签.pdf 第 70 页同题解析；current solution-01.png 与 BATCH-018 root recovery SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-018 current question/solution direct visual exact / Wangdao question page 63 and solution page 70 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：s0hqUA8VSiW
- OO3 路径：408 > 操作系统 > 3.操作系统 > 第二章 进程与线程 > 2.1 线程与进程 > 进程的状态与转换 > 2.1.8 （17）

> [!question] 题目
> ![[assets/OS_UNK_046/question-01.png]]

> [!answer]- 解析
> 解析图：无独立解析图，以下保留解析文字。
>
> 
> **我的错误选择：** C 阻塞态  
> **正确答案：** B 就绪态  
> 
> ---
> 
> **错误原因：**  
> - 误以为分时系统因时间片轮转，很多进程要等待 → 错选“阻塞态”。  
> - 实际上，阻塞态是等待 I/O 等事件；而分时系统主要等待 CPU → 应该是“就绪态”。  
> 
> ---
> 
> **知识点总结：**  
> - **分时系统特点：** 时间片轮转，多进程轮流使用 CPU。  
> - **就绪态：** 进程已具备运行条件，只是等待 CPU。  
> - **阻塞态：** 进程等待外部事件（I/O、信号）。  
> - **分时系统中，就绪态进程数通常最多**，因为大家都在排队等 CPU。
