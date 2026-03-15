import sys
import os
import json
import re
import difflib
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QFileDialog, QMessageBox,
                             QHeaderView, QStyleFactory)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QIcon

import re
from playwright.sync_api import sync_playwright


class VideoItem:
    """数据模型：表示一个视频或视频组"""

    def __init__(self, title, duration="", is_group=False):
        self.title = title.strip()
        self.duration = duration.strip()
        self.is_group = is_group
        self.children = []  # 子集
        self.matched_file = None  # 本地文件路径
        self.index = 0  # 全局排序索引

    def to_dict(self):
        return {
            "title": self.title,
            "duration": self.duration,
            "is_group": self.is_group,
            "children": [c.to_dict() for c in self.children],
            "index": self.index
        }

    # === 补充这个方法，否则你的 load_list 会报错 ===
    @classmethod
    def from_dict(cls, data):
        item = cls(data["title"], data.get("duration", ""), data.get("is_group", False))
        item.index = data.get("index", 0)
        for child_data in data.get("children", []):
            item.children.append(cls.from_dict(child_data))
        return item


class PlaywrightScraper:
    """
    独立的抓取服务类 (已更新：兼容单P列表和多P系列)
    """

    def __init__(self, headless=True):
        self.headless = headless

    def fetch_video_structure(self, url):
        results = []

        with sync_playwright() as p:
            # 启动浏览器
            browser = p.chromium.launch(headless=self.headless)
            # 使用手机/桌面通用的 UserAgent，防止被识别为爬虫
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            page = context.new_page()

            try:
                # 访问页面
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # 等待列表容器 (video-pod__list) 加载
                try:
                    page.wait_for_selector('.video-pod__list', timeout=10000)
                except:
                    print("警告: 未检测到 .video-pod__list，尝试直接解析...")

                # 获取所有列表项 (pod-item)
                pod_items = page.query_selector_all('.video-pod__list .pod-item')

                global_idx = 1

                for pod in pod_items:
                    # -------------------------------------------------
                    # 情况 A: 多P系列 (原有逻辑，对应 .multi-p)
                    # -------------------------------------------------
                    if pod.query_selector('.multi-p'):
                        # 1. 提取系列标题
                        head_el = pod.query_selector('.head .title-txt')
                        group_title = head_el.inner_text() if head_el else "未命名系列"

                        group_item = VideoItem(group_title, is_group=True)

                        # 2. 提取子分集
                        sub_items = pod.query_selector_all('.page-list .page-item')

                        for sub in sub_items:
                            title_el = sub.query_selector('.title-txt')
                            sub_title = title_el.inner_text() if title_el else "Unknown"

                            dur_el = sub.query_selector('.stat-item.duration')
                            duration = dur_el.inner_text().strip() if dur_el else ""

                            child = VideoItem(sub_title, duration)
                            child.index = global_idx
                            global_idx += 1

                            group_item.children.append(child)

                        results.append(group_item)

                    # -------------------------------------------------
                    # 情况 B: 单P视频 (新增逻辑，对应 .single-p)
                    # -------------------------------------------------
                    elif pod.query_selector('.single-p'):
                        # 1. 提取视频信息
                        title_el = pod.query_selector('.title-txt')
                        video_title = title_el.inner_text() if title_el else "未命名视频"

                        dur_el = pod.query_selector('.stat-item.duration')
                        duration = dur_el.inner_text().strip() if dur_el else ""

                        # 2. 为了适配 GUI 的树状结构，创建一个“伪分组”
                        # 结构变成: 视频标题 (组) -> 视频标题 (子项 - 用于匹配文件)
                        group_item = VideoItem(video_title, is_group=True)

                        child = VideoItem(video_title, duration)
                        child.index = global_idx
                        global_idx += 1

                        group_item.children.append(child)
                        results.append(group_item)

            except Exception as e:
                browser.close()
                raise e

            browser.close()

        return results


# --- Worker 线程：连接 GUI 和 Playwright ---

class ScraperWorker(QThread):
    finished_signal = pyqtSignal(list, str)  # data, error_msg

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            # 在这里实例化 PlaywrightScraper，确保它在子线程运行
            # 引入上面的 PlaywrightScraper 类逻辑
            scraper = PlaywrightScraper(headless=True)
            data = scraper.fetch_video_structure(self.url)
            self.finished_signal.emit(data, "")
        except Exception as e:
            self.finished_signal.emit([], str(e))


# --- GUI 主界面 ---

class VideoManagerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频教程整理助手 (Playwright版)")
        self.resize(1100, 750)
        self.video_data = []
        self.local_files = []
        self.work_dir = ""
        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(10)

        # 1. 顶部控制栏
        top_group = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("在此输入包含视频列表的网址 (例如 Bilibili 详情页)")

        self.btn_fetch = QPushButton("🕵️ Playwright 抓取")
        self.btn_fetch.clicked.connect(self.start_scraping)

        btn_io_group = QHBoxLayout()
        btn_save = QPushButton("💾 保存列表")
        btn_save.clicked.connect(self.save_list)
        btn_load = QPushButton("📂 加载列表")
        btn_load.clicked.connect(self.load_list)

        top_group.addWidget(QLabel("网址:"))
        top_group.addWidget(self.url_input)
        top_group.addWidget(self.btn_fetch)
        top_group.addLayout(btn_io_group)
        btn_io_group.addWidget(btn_save)
        btn_io_group.addWidget(btn_load)

        layout.addLayout(top_group)

        # 2. 核心列表 (TreeWidget)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["标题结构 / 在线列表", "时长", "状态", "本地文件 (待重命名)"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 80)
        layout.addWidget(self.tree)

        # 3. 底部操作栏
        bottom_group = QHBoxLayout()

        self.lbl_dir = QLabel("未选择目录")
        self.lbl_dir.setStyleSheet("color: gray; border: 1px solid #ccc; padding: 5px; border-radius: 4px;")

        btn_dir = QPushButton("1. 选择本地目录")
        btn_dir.clicked.connect(self.select_directory)

        btn_match = QPushButton("2. 智能匹配")
        btn_match.clicked.connect(self.match_files)

        btn_rename = QPushButton("3. 一键编号重命名")
        btn_rename.setStyleSheet("background-color: #d9534f; color: white; font-weight: bold; padding: 6px;")
        btn_rename.clicked.connect(self.perform_renaming)

        bottom_group.addWidget(btn_dir)
        bottom_group.addWidget(self.lbl_dir)
        bottom_group.addStretch()
        bottom_group.addWidget(btn_match)
        bottom_group.addWidget(btn_rename)

        layout.addLayout(bottom_group)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪。请抓取网页或加载列表文件。")

    # --- 逻辑部分 ---

    def start_scraping(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入有效的 URL")
            return

        self.tree.clear()
        self.status_bar.showMessage("🚀 正在启动浏览器抓取中，这可能需要几秒钟，请稍候...")

        # 禁用按钮防止重复点击
        self.btn_fetch.setEnabled(False)

        self.worker = ScraperWorker(url)
        self.worker.finished_signal.connect(self.on_scraping_finished)
        self.worker.finished_signal.connect(lambda: self.btn_fetch.setEnabled(True))

        self.worker.start()

    def on_scraping_finished(self, items, error):
        if error:
            QMessageBox.critical(self, "抓取错误", f"Playwright 执行出错:\n{error}")
            self.status_bar.showMessage("抓取失败")
        else:
            self.video_data = items
            self.refresh_tree()
            self.status_bar.showMessage(f"✅ 抓取成功! 共找到 {len(items)} 个系列。")

    def refresh_tree(self):
        self.tree.clear()

        # 1. 渲染抓取到的数据
        for group in self.video_data:
            group_node = QTreeWidgetItem(self.tree)
            group_node.setText(0, group.title)
            # 设置灰色背景表示它是组
            for i in range(4):
                group_node.setBackground(i, QBrush(QColor("#f0f0f0")))
            group_node.setExpanded(True)

            for child in group.children:
                child_node = QTreeWidgetItem(group_node)
                # 显示：[001] 标题
                child_node.setText(0, f"[{child.index:03d}] {child.title}")
                child_node.setText(1, child.duration)

                if child.matched_file:
                    child_node.setText(2, "✅")
                    child_node.setForeground(2, QBrush(QColor("green")))
                    child_node.setText(3, os.path.basename(child.matched_file))
                    child_node.setForeground(3, QBrush(QColor("blue")))
                else:
                    child_node.setText(2, "❌")
                    child_node.setForeground(2, QBrush(QColor("red")))
                    child_node.setText(3, "")

        # 2. 显示多余文件（如果在目录模式下）
        if self.work_dir:
            matched_set = set()
            for g in self.video_data:
                for c in g.children:
                    if c.matched_file: matched_set.add(c.matched_file)

            extras = [f for f in self.local_files if f not in matched_set]
            if extras:
                extra_root = QTreeWidgetItem(self.tree)
                extra_root.setText(0, "--- ⚠️ 目录中多余的文件 ---")
                extra_root.setForeground(0, QBrush(QColor("orange")))
                for f in extras:
                    node = QTreeWidgetItem(extra_root)
                    node.setText(3, os.path.basename(f))

    def select_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择视频目录")
        if path:
            self.work_dir = path
            self.lbl_dir.setText(path)
            self.scan_local_files()
            self.status_bar.showMessage(f"目录已加载，包含 {len(self.local_files)} 个视频文件。")
            # 自动尝试一次匹配
            self.match_files()

    def scan_local_files(self):
        if not self.work_dir: return
        exts = ('.mp4', '.mkv', '.avi', '.flv', '.mov', '.wmv', '.webm')
        self.local_files = [
            os.path.join(self.work_dir, f)
            for f in os.listdir(self.work_dir)
            if f.lower().endswith(exts)
        ]

    def match_files(self):
        """
        匹配逻辑：
        1. 精确匹配：文件名开头的数字索引 (100%匹配，直接锁定并剔除池子)
        2. 模糊匹配：计算所有未匹配条目与未匹配文件的相似度，按分数从高到低全局分配
        """
        if not self.video_data or not self.work_dir:
            return

        self.scan_local_files()

        # 收集所有的子条目，并重置它们的匹配状态
        all_children = []
        for g in self.video_data:
            for c in g.children:
                c.matched_file = None
                all_children.append(c)

        # 创建待匹配池 (使用 set 方便移除元素)
        unmatched_files = set(self.local_files)
        unmatched_children = set(all_children)

        # ---------------------------------------------------------
        # 阶段 A: 精确匹配 (提取文件名开头的数字)
        # ---------------------------------------------------------
        for child in list(unmatched_children):
            target_idx = child.index
            for f_path in list(unmatched_files):
                f_name = os.path.basename(f_path)
                num_match = re.match(r'^(\d+)', f_name)

                if num_match and int(num_match.group(1)) == target_idx:
                    # 找到绝对匹配，锁定！
                    child.matched_file = f_path
                    # 从池子中移除，确保 1 对 1 独占
                    unmatched_files.remove(f_path)
                    unmatched_children.remove(child)
                    break  # 停止当前 child 的查找，进入下一个 child

        # ---------------------------------------------------------
        # 阶段 B: 全局文本相似度最优匹配
        # ---------------------------------------------------------
        match_candidates = []  # 用于存储 (相似度得分, child对象, 文件路径)

        # 计算所有剩下的 [未匹配条目] x [未匹配文件] 的得分
        for child in unmatched_children:
            target_title_clean = re.sub(r'[^\w]', '', child.title)
            for f_path in unmatched_files:
                f_name_pure = os.path.splitext(os.path.basename(f_path))[0]
                # 计算比率
                score = difflib.SequenceMatcher(None, target_title_clean, f_name_pure).ratio()

                # 设定及格线，低于0.4没必要参与竞争
                if score >= 0.4:
                    match_candidates.append((score, child, f_path))

        # 按得分从高到低排序 (关键点：让最匹配的优先挑走文件)
        match_candidates.sort(key=lambda x: x[0], reverse=True)

        # 遍历排序后的候选列表进行分配
        for score, child, f_path in match_candidates:
            # 必须双方都还在未匹配池中，才能结合
            if child in unmatched_children and f_path in unmatched_files:
                child.matched_file = f_path
                unmatched_children.remove(child)
                unmatched_files.remove(f_path)

        self.refresh_tree()
        self.status_bar.showMessage(f"匹配完成！剩余 {len(unmatched_files)} 个未匹配文件。")

    def perform_renaming(self):
        if not self.work_dir: return

        tasks = []
        for g in self.video_data:
            for c in g.children:
                if c.matched_file:
                    old_path = c.matched_file
                    dirname = os.path.dirname(old_path)
                    ext = os.path.splitext(old_path)[1]

                    # 构造新文件名：001 - 标题.mp4
                    # 去除文件名非法字符
                    safe_title = re.sub(r'[\\/:*?"<>|]', '_', c.title)
                    new_name = f"{c.index:03d} - {safe_title}{ext}"
                    new_path = os.path.join(dirname, new_name)

                    if old_path != new_path:
                        tasks.append((old_path, new_path))

        if not tasks:
            QMessageBox.information(self, "提示", "没有文件需要重命名 (可能已经命名好了或未匹配)")
            return

        # 确认弹窗
        preview = "\n".join([f"{os.path.basename(t[0])} -> {os.path.basename(t[1])}" for t in tasks[:5]])
        if len(tasks) > 5: preview += f"\n... 以及其他 {len(tasks) - 5} 个文件"

        reply = QMessageBox.question(self, "确认重命名",
                                     f"即将重命名 {len(tasks)} 个文件，此操作不可逆！\n\n预览:\n{preview}",
                                     QMessageBox.Yes | QMessageBox.No)

        if reply == QMessageBox.Yes:
            err_count = 0
            for old, new in tasks:
                try:
                    os.rename(old, new)
                except OSError as e:
                    print(f"Rename error: {e}")
                    err_count += 1

            msg = "重命名操作完成！"
            if err_count > 0:
                msg += f"\n有 {err_count} 个文件重命名失败 (可能被占用)。"

            QMessageBox.information(self, "完成", msg)
            self.scan_local_files()  # 重新扫描
            self.match_files()  # 重新匹配以刷新视图

    # --- IO 部分 ---
    def save_list(self):
        if not self.video_data: return
        path, _ = QFileDialog.getSaveFileName(self, "保存列表", "", "JSON Files (*.json)")
        if path:
            data = [item.to_dict() for item in self.video_data]
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.status_bar.showMessage(f"列表已保存至 {path}")

    def load_list(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开列表", "", "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                self.video_data = [VideoItem.from_dict(d) for d in raw]
                self.refresh_tree()
                self.status_bar.showMessage(f"已加载列表 {path}")
            except Exception as e:
                QMessageBox.critical(self, "加载失败", str(e))


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 设置一个简单的样式
    app.setStyle(QStyleFactory.create("Fusion"))

    window = VideoManagerApp()
    window.show()
    sys.exit(app.exec_())