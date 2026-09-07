# OS_UNK_072

来源定位：HOS_0090 / 4.2.7（07） / 段落5243

正式节点：OS_UNK_072
来源 ID：HOS_0090
科目：操作系统
主知识点：OS04-22 硬链接
核心考点：沿路径名、独立目录项、共享 inode、打开文件表和文件描述符分层判断链接计数、权限与读写状态。
主模块：OS04 文件管理
题型：链接、权限与打开状态组合辨析题
解析来源：2026操作系统_带书签.pdf 第 303、304 页同题解析；current solution-01.png 与 BATCH-020 root recovery SHA-256 一致
解析来源角色：独立同题解析只支持答案安全机制与知识角色复核，不据解析新增、选择或改写用户事实。
匹配方式：BATCH-020 current question and solution direct visual exact；formal identity、year、locator、dates、redo 与 user facts preserved

## MarginNote 链接

- 未提取

> [!question] 题目
> ![[assets/OS_UNK_072/question-01.png]]

> [!answer]- 解析
>
> ### 错题回顾 - 链接与引用计数
> - A 正确：F1 与 F2 指向同一个 inode，两次 open 只涉及一次磁盘读取 inode。
> - B 错误：访问权限取决于用户身份和文件权限，不保证 P1、P2 相同。
> - C 错误：删除符号链接 F3 不影响 inode 引用计数。
> - D 错误：read() 通过 fd 操作文件，不需要路径名。
> 
> ✅ 正确答案：A
> 
> ### 联系考纲
> - OS-11 索引结点 inode  
>   inode 保存元数据；硬链接共享 inode，符号链接保存路径。  
> - OS-12 文件打开与引用计数  
>   open() 时第一次将 inode 从磁盘调入内存，后续复用；引用计数随硬链接增减。  
> - OS-13 文件共享与保护  
>   硬链接删除 → 引用计数减一；符号链接删除 → 不影响原文件。  
>   文件访问权限由权限位 + 用户身份控制，不依赖“谁打开”。
