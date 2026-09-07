# OS_UNK_036

来源定位：HOS_0045 / 2.1.8 （37） / 段落3835

正式节点：OS_UNK_036
来源 ID：HOS_0045
科目：操作系统
主知识点：OS02-11 线程基本概念
核心考点：线程是独立的执行与调度单位；并发描述一段时间内交替推进，所属进程是否相同不构成禁止并发的条件，并行还需多处理器支持。
主模块：OS02 进程管理
题型：单项选择题 / 并发边界判断题
解析来源：2026操作系统_带书签.pdf 第 72 页同题解析；current solution-01.png 与 BATCH-018 root recovery SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-018 current question/solution direct visual exact / Wangdao question page 64、65 and solution page 72 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：01mtTikQRfC
- OO3 路径：408 > 操作系统 > 3.操作系统 > 第二章 进程与线程 > 2.1 线程与进程 > 线程和多线程模型 > 线程的属性 > 2.1.8 （37）

> [!question] 题目
> ![[assets/OS_UNK_036/question-01.png]]

> [!answer]- 解析
>
> ### 知识点总结
> 1. **线程是 CPU 调度的基本单位**  
>    - 无论线程属于同一进程还是不同进程，都可以并发执行。  
>    - 取决于系统是否支持多道程序、时间片轮转、以及是否有多核处理器。  
> 
> 2. **并发执行的条件**  
>    - 单核 CPU：通过时间片轮转，实现“宏观并行，微观串行”。  
>    - 多核 CPU：真正实现物理上的并行执行。  
> 
> ---
> 
> ### 错因分析
> - 错把“进程间并发”与“线程并发”混淆。  
> - 实际上线程不受进程是否相同的限制，同一进程/不同进程的线程均可并发。  
> 
> ---
> 
> ✅ **记忆要点：线程是 CPU 调度的最小单位 → 线程之间（无论是否属于同一进程）都可以并发执行。**

## 图片 QA 记录

- 2026-07-03 QA（pass question only AC）：题图清晰可复做；无独立解析图；折叠解析与线程属性和线程并发执行边界主题一致；追加详情卡 QA 说明；不改正式关系网；证据图 `indexes/qa-contact-sheets/pass-question-only-batch-ac-os-current-2026-07-03.png`。检测线只保留概念范围：OS02-11；不向 Tutor 输入题图、完整解析、答案、选项字母或手写痕迹。
