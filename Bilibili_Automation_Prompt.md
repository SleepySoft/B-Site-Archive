# 需求文档：B站合集自动下载与跳转脚本（油猴版）

## 1. 项目概述

开发一个 Tampermonkey（油猴）脚本，用于自动化操作 Bilibili 网页版合集页面。
**核心目标**：配合浏览器插件 "Bilibili Helper"（哔哩哔哩助手），实现“自动点击合并下载 -> 等待完成 -> 随机延时 -> 跳转下一集”的无人值守循环。

## 2. 运行环境与依赖

* **目标网站**：`https://www.bilibili.com/video/*`
* **依赖插件**：页面必须加载 "Bilibili Helper" 扩展（该扩展会在页面底部生成下载面板）。
* **脚本管理器**：Tampermonkey。

## 3. 功能需求

### 3.1 用户界面 (UI)

* 在页面右上角（避开顶部导航栏，如 `top: 80px`）生成一个**悬浮控制面板**。
* **包含控件**：
* **标题**：显示版本号。
* **开始/停止按钮**：默认暂停，点击开始后执行自动化逻辑，再次点击停止。
* **日志窗口**：滚动显示操作日志（如“找到按钮”、“下载完成”、“正在跳转”等）。



### 3.2 核心逻辑流程

1. **启动检查**：用户点击“开始”后，脚本开始轮询。
2. **定位下载按钮**：
* 目标在 "Bilibili Helper" 的 DOM 中。
* **难点**：该插件使用了 **Shadow DOM**，必须穿透 ShadowRoot 才能获取元素。
* **选择器**：`a[merge="on"]`（且不能包含 `disabled` 类）。


3. **执行下载**：点击上述按钮。
4. **状态监控**：
* 持续监控插件面板内的进度条（同样在 Shadow DOM 中）。
* **完成标准**：`ul.progress` 元素的文本包含 “已完成”。


5. **随机缓冲**：下载完成后，等待 `3秒 + (0~3秒随机抖动)`，模拟人类操作。
6. **跳转下一集**：
* 定位页面右侧播放列表 (`.video-pod__list`)。
* 找到当前高亮项 (`.simple-base-item.active`)。
* 查找其父级容器 (`.pod-item`) 的下一个兄弟元素 (`nextElementSibling`)。
* 点击下一集的标题链接。
* **边界处理**：如果是列表最后一集，停止脚本并弹窗提示。



### 3.3 SPA (单页应用) 适配

* **问题**：B站切集时页面不刷新，URL 改变但脚本变量状态会残留。
* **解决方案**：脚本需维护全局 `currentUrl` 变量。轮询时检测 `window.location.href` 是否变化。若变化，必须**重置**“是否已点击”、“是否已完成”等所有状态标志，进入新一轮循环。

## 4. 技术细节与 DOM 参考

### 4.1 Shadow DOM 穿透工具函数

必须实现类似以下的辅助函数来获取插件内部元素：

```javascript
function getPluginElement(selector) {
    const host = document.getElementById('bilibili-helper-host'); // 宿主
    if (!host || !host.shadowRoot) return null;
    return host.shadowRoot.querySelector(selector);
}

```

### 4.2 关键 DOM 结构参考

**A. 页面播放列表（主 DOM）：**

```html
<div class="video-pod__list">
    <div class="pod-item">
        <div class="simple-base-item active">...</div> 
    </div>
    <div class="pod-item">
        <a class="title">下一集标题</a>
    </div>
</div>

```

**B. 下载插件（Shadow DOM 内部）：**

```html
<bilibili-helper-host id="bilibili-helper-host">
    #shadow-root (open)
        <a mode="advanced" merge="on">合并下载</a>
        <ul class="progress">已完成...</ul>
</bilibili-helper-host>

```

## 5. 交付代码要求

* 代码结构清晰，包含配置区域（Config）。
* 使用 `setInterval` 进行状态轮询。
* 具有鲁棒性：找不到元素时不要报错崩溃，而是输出日志并重试。
* **必须处理好 SPA 跳转后的状态重置问题**。