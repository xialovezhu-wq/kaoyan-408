# OS_UNK_041

来源定位：HOS_0056 / 2.1.8 （21） / 段落3784

正式节点：OS_UNK_041
来源 ID：HOS_0056
科目：操作系统
主知识点：OS02-07 进程创建
核心考点：创建关系建立进程树上的亲缘，但不会消除两个进程实体各自的标识和执行独立性；一方撤销对另一方的影响取决于系统语义，不能绝对化。
主模块：OS02 进程管理
题型：单项选择题 / 父子进程关系边界辨析题
解析来源：2026操作系统_带书签.pdf 第 70、71 页同题解析；current solution-01.png 与 BATCH-018 root recovery SHA-256 一致
解析来源角色：独立同题解析只支持答案安全题目机制与知识角色复核；不据解析新增、选择或改写用户事实。
匹配方式：BATCH-018 current question/solution direct visual exact / Wangdao question page 63 and solution page 70、71 / formal identity, year, locator, dates and user facts preserved

## MarginNote 来源

- OO3 item：lvgEXT-DTdG
- OO3 路径：408 > 操作系统 > 3.操作系统 > 第二章 进程与线程 > 2.1 线程与进程 > 进程控制 > 2.1.8 （21）

> [!question] 题目
> ![[assets/OS_UNK_041/question-01.png]]

> [!answer]- 解析
>
> ## 知识点总结
> - 子进程由父进程创建，但二者拥有 **不同的PID**，相互独立。
> - 父子进程可以 **并发执行**，互不依赖。
> - 子进程被撤销时，父进程不一定同时撤销。
> - 父进程执行完毕后，子进程仍可继续执行。
> 
> ---
> 
> ## 错因
> - 混淆了父子进程关系，误以为PID相同。  
> - 实际上，**PID是唯一的，不能重复**。
> 
> ---
> 
> ✅ 正确理解：父子进程通过 **进程树结构** 建立关系，而非依赖相同PID。

## 图片质检记录

- 2026-07-03 watermark/duplicate T 批：直接查看当前题图；水印覆盖但题面文字仍可读，未见单独暴露答案或作答方向的标注。强行清理会损伤题面文字，本批保留并登记。Tutor 安全范围：OS02-06。
