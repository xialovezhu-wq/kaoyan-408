# OS_UNK_083

来源定位：HOS_0101 / 4.2.7（01） / 段落5045

正式节点：OS_UNK_083
来源 ID：HOS_0101
科目：操作系统
主知识点：OS04-21 目录操作
核心考点：多级目录按路径分量逐层查找，任一分量不存在即可停止；相对路径可从当前目录开始，结果是逻辑控制入口。
主模块：OS04 文件管理
题型：目录检索机制多陈述辨析题
解析来源：2026操作系统_带书签.pdf 第 303 页同题解析；current solution-01.png 与 BATCH-020 root recovery SHA-256 一致
解析来源角色：独立同题解析只支持答案安全机制与知识角色复核，不据解析新增、选择或改写用户事实。
匹配方式：BATCH-020 current question and solution direct visual exact；formal identity、year、locator、dates、redo 与 user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/OS_UNK_083/question-01.png]]

> [!answer]- 解析
>
> ### 错题回顾 - 目录检索
> - A 错误：散列法快，但不适合所有目录结构，OS 常用顺序检索。
> - B 错误：多级目录可从当前目录开始，不一定从根目录开始。
> - C 正确：顺序检索时，只要路径分量名未找到，就立即停止。
> - D 错误：检索结束得到的是逻辑控制信息（FCB/inode），不是物理地址。
> 
> ✅ 正确答案：C
> 
> ### 联系考纲
> - OS-11 文件控制块与目录项  
>   检索目录返回 inode/FCB → 逻辑地址。  
> - OS-12 目录结构  
>   多级目录逐级检索；某分量不存在 → 停止检索。  
> - OS-13 文件系统优化  
>   散列法能加快查找，但存在冲突与溢出问题，顺序检索仍是主流方法。
