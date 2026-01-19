from pathlib import Path
from typing import Optional
import configparser
import sys
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLineEdit, QLabel,
    QDialog, QFormLayout, QDialogButtonBox, QMessageBox, QHeaderView,
    QAbstractItemView, QCheckBox, QMenuBar, QMenu, QFileDialog, QComboBox,
    QListView
)
from PyQt5.QtCore import Qt, QEvent, pyqtSignal, QTimer, QThread
from PyQt5.QtGui import QPixmap, QImage, QIcon, QStandardItemModel, QStandardItem

from book_manager import BookManager, BookMetadata

# ISO 639-2 语言映射
LANGUAGE_MAP = {
    '英语': 'eng',
    '中文': 'zho',
    '法语': 'fra',
    '日语': 'jpn',
    '德语': 'deu'
}
# 反向映射用于显示
REVERSE_LANG_MAP = {v: k for k, v in LANGUAGE_MAP.items()}

class I18n:
    """国际化管理类"""
    def __init__(self, ini_path: str):
        self.config = configparser.ConfigParser()
        self.config.read(ini_path, encoding='utf-8')
        self.languages = self.config.sections()
        self.current_lang = 'zh_CN' if 'zh_CN' in self.languages else self.languages[0]
        
    def set_language(self, lang_code: str):
        if lang_code in self.languages:
            self.current_lang = lang_code
            
    def t(self, key: str, **kwargs) -> str:
        val = self.config.get(self.current_lang, key, fallback=key)
        if kwargs:
            return val.format(**kwargs)
        return val

# 全局 i18n 实例（将在 main 中初始化）
i18n: Optional[I18n] = None


class CheckableComboBox(QComboBox):
    """支持多选勾选的下拉框"""
    changed = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.setView(QListView(self))
        self.view().viewport().installEventFilter(self)
        self.model = QStandardItemModel(self)
        self.setModel(self.model)
        self.model.itemChanged.connect(self._on_item_changed)
        self._changed = False

    def eventFilter(self, widget, event):
        if widget == self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = self.view().indexAt(event.pos())
            if index.isValid():
                item = self.model.itemFromIndex(index)
                if item.checkState() == Qt.CheckState.Checked:
                    item.setCheckState(Qt.CheckState.Unchecked)
                else:
                    item.setCheckState(Qt.CheckState.Checked)
                return True
        return super().eventFilter(widget, event)

    def addItem(self, text, data=None):
        item = QStandardItem(text)
        item.setData(data if data is not None else text, Qt.ItemDataRole.UserRole)
        item.setCheckable(True)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.model.appendRow(item)

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def _on_item_changed(self, item):
        self._update_text()
        self.changed.emit()

    def _update_text(self):
        texts = []
        for i in range(self.model.rowCount()):
            item = self.model.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                texts.append(item.text())
        self.setEditText(", ".join(texts))
        self._changed = True

    def currentData(self):
        res = []
        for i in range(self.model.rowCount()):
            item = self.model.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                res.append(item.data(Qt.ItemDataRole.UserRole))
        return res

    def setCheckedItems(self, targets):
        self.model.blockSignals(True)
        for i in range(self.model.rowCount()):
            item = self.model.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data in targets:
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.model.blockSignals(False)
        self._update_text()
        self._changed = False

    def is_changed(self):
        return self._changed


class UpdateWorker(QThread):
    """异步更新线程，避免 UI 卡死"""
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(int)  # 进度信号 (0-100)

    def __init__(self, manager, bids, updates):
        super().__init__()
        self.manager = manager
        self.bids = bids
        self.updates = updates

    def run(self):
        try:
            self.manager.start_bulk_update()
            try:
                total = len(self.bids)
                for i, bid in enumerate(self.bids):
                    for f, v in self.updates.items():
                        if f == 'pubdate':
                            try:
                                dt = datetime.strptime(v, '%Y-%m-%d')
                                self.manager.set_field(f, {bid: dt})
                            except:
                                pass
                        else:
                            self.manager.set_field(f, {bid: v})
                    # 每完成一本书报告进度
                    self.progress.emit(int((i + 1) / total * 100))
            finally:
                self.manager.end_bulk_update()
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class TrashDialog(QDialog):
    """回收站浏览对话框"""
    
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle(i18n.t("trash_title"))
        self.setMinimumSize(600, 400)
        self.setup_ui()
        self.load_trash()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            i18n.t("header_select"),
            i18n.t("header_id"),
            i18n.t("header_title"),
            i18n.t("header_author")
        ])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        
        # 按钮
        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton(i18n.t("btn_select_all"))
        self.select_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        btn_layout.addWidget(self.select_all_btn)
        
        self.deselect_all_btn = QPushButton(i18n.t("btn_deselect_all"))
        self.deselect_all_btn.clicked.connect(lambda: self._set_all_checks(False))
        btn_layout.addWidget(self.deselect_all_btn)
        
        btn_layout.addStretch()
        
        self.restore_btn = QPushButton(i18n.t("btn_restore"))
        self.restore_btn.clicked.connect(self.restore_selected)
        btn_layout.addWidget(self.restore_btn)
        
        self.close_btn = QPushButton(i18n.t("btn_close"))
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)
        
        layout.addLayout(btn_layout)
        
        # 状态标签
        self.status_label = QLabel()
        layout.addWidget(self.status_label)
    
    def load_trash(self):
        self.table.setRowCount(0)
        self.trash_items = self.manager.list_trash()
        self.table.setRowCount(len(self.trash_items))
        
        for row, item in enumerate(self.trash_items):
            # 复选框
            cb_widget = QWidget()
            l = QHBoxLayout(cb_widget)
            l.setContentsMargins(0, 0, 0, 0)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb = QCheckBox()
            l.addWidget(cb)
            self.table.setCellWidget(row, 0, cb_widget)
            
            # ID
            id_item = QTableWidgetItem(str(item['book_id']))
            id_item.setData(Qt.ItemDataRole.UserRole, item.get('path', ''))  # 使用 path 作为数据
            self.table.setItem(row, 1, id_item)
            
            # 标题
            self.table.setItem(row, 2, QTableWidgetItem(item['title']))
            
            # 作者
            authors = ", ".join(item['authors']) if item['authors'] else ""
            self.table.setItem(row, 3, QTableWidgetItem(authors))
        
        self.status_label.setText(i18n.t("trash_count", count=len(self.trash_items)))
    
    def _set_all_checks(self, checked):
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w:
                cb = w.findChild(QCheckBox)
                if cb:
                    cb.setChecked(checked)
    
    def get_selected_paths(self):
        paths = []
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w:
                cb = w.findChild(QCheckBox)
                if cb and cb.isChecked():
                    it = self.table.item(r, 1)
                    if it:
                        path = it.data(Qt.ItemDataRole.UserRole)
                        if path:
                            paths.append(path)
        return paths
    
    def restore_selected(self):
        paths = self.get_selected_paths()
        if not paths:
            QMessageBox.warning(self, i18n.t("msg_title_tip"), i18n.t("msg_no_selection"))
            return
        
        try:
            restored = self.manager.restore_books(paths)
            QMessageBox.information(self, i18n.t("msg_title_success"), i18n.t("msg_restore_success", count=restored))
            self.load_trash()
        except Exception as e:
            QMessageBox.critical(self, i18n.t("msg_title_error"), i18n.t("msg_restore_failed", error=str(e)))


class EditDialog(QDialog):
    """编辑图书信息对话框"""
    
    def __init__(self, books: list[BookMetadata], all_languages: list[str], all_tags: list[str], parent=None):
        super().__init__(parent)
        self.books = books
        self.all_languages = all_languages
        self.all_tags = all_tags
        self.setWindowTitle(i18n.t("dialog_edit_title", count=len(books)))
        self.setMinimumWidth(400)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QFormLayout(self)
        
        if len(self.books) == 1:
            book = self.books[0]
            self.title_edit = QLineEdit(book.title)
            self.authors_edit = QLineEdit(", ".join(book.authors))
            self.publisher_edit = QLineEdit(book.publisher)
            self.pubdate_edit = QLineEdit()
            self.pubdate_edit.setPlaceholderText(i18n.t("dialog_pubdate_tip"))
            
            self.lang_combo = QComboBox()
            self.lang_combo.addItem(i18n.t("dialog_no_change"), None)
            for name, code in LANGUAGE_MAP.items():
                self.lang_combo.addItem(f"{name} ({code})", code)
            for lang_code in self.all_languages:
                if lang_code not in LANGUAGE_MAP.values() and lang_code not in LANGUAGE_MAP.keys():
                    self.lang_combo.addItem(lang_code, lang_code)
            
            current_lang = book.languages[0] if book.languages else None
            if current_lang:
                index = self.lang_combo.findData(current_lang)
                if index >= 0:
                    self.lang_combo.setCurrentIndex(index)
                else:
                    self.lang_combo.addItem(current_lang, current_lang)
                    self.lang_combo.setCurrentIndex(self.lang_combo.count() - 1)
            
            self.tags_combo = CheckableComboBox()
            self.tags_combo.addItems(self.all_tags)
            self.tags_combo.setCheckedItems(book.tags)
        else:
            self.title_edit = QLineEdit()
            self.title_edit.setPlaceholderText(i18n.t("dialog_placeholder_no_change"))
            self.authors_edit = QLineEdit()
            self.authors_edit.setPlaceholderText(i18n.t("dialog_placeholder_no_change"))
            self.publisher_edit = QLineEdit()
            self.publisher_edit.setPlaceholderText(i18n.t("dialog_placeholder_no_change"))
            self.pubdate_edit = QLineEdit()
            self.pubdate_edit.setPlaceholderText(i18n.t("dialog_pubdate_tip"))
            
            self.lang_combo = QComboBox()
            self.lang_combo.addItem(i18n.t("dialog_no_change"), None)
            for name, code in LANGUAGE_MAP.items():
                self.lang_combo.addItem(f"{name} ({code})", code)
            for lang_code in self.all_languages:
                if lang_code not in LANGUAGE_MAP.values() and lang_code not in LANGUAGE_MAP.keys():
                    self.lang_combo.addItem(lang_code, lang_code)
            
            self.tags_combo = CheckableComboBox()
            self.tags_combo.addItems(self.all_tags)
            
            books_text = "\n".join([f"#{b.id}: {b.title}" for b in self.books[:5]])
            if len(self.books) > 5:
                books_text += f"\n... ({len(self.books)} books)"
            layout.addRow(i18n.t("dialog_label_selected"), QLabel(books_text))
        
        layout.addRow(i18n.t("dialog_label_title"), self.title_edit)
        layout.addRow(i18n.t("dialog_label_author"), self.authors_edit)
        layout.addRow(i18n.t("dialog_label_publisher"), self.publisher_edit)
        layout.addRow(i18n.t("dialog_label_pubdate"), self.pubdate_edit)
        layout.addRow(i18n.t("dialog_label_language"), self.lang_combo)
        layout.addRow(i18n.t("dialog_label_tags"), self.tags_combo)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
    
    def get_updates(self) -> dict:
        updates = {}
        title = self.title_edit.text().strip()
        if title: updates['title'] = title
        authors = self.authors_edit.text().strip()
        if authors: updates['authors'] = [a.strip() for a in authors.split(',')]
        publisher = self.publisher_edit.text().strip()
        if publisher: updates['publisher'] = publisher
        pubdate = self.pubdate_edit.text().strip()
        if pubdate: updates['pubdate'] = pubdate
        lang = self.lang_combo.currentData()
        if lang: updates['languages'] = [lang]
        
        if len(self.books) == 1:
            updates['tags'] = self.tags_combo.currentData()
        elif self.tags_combo.is_changed():
            updates['tags'] = self.tags_combo.currentData()
        return updates


class MainWindow(QMainWindow):
    """主窗口"""
    
    COVER_WIDTH = 60
    COVER_HEIGHT = 80
    PAGE_SIZE = 50
    
    def __init__(self, library_path: str):
        super().__init__()
        self.library_path = library_path
        self.manager: Optional[BookManager] = None
        self.books: dict[int, BookMetadata] = {}
        self.sorted_book_ids: list[int] = []
        self.filtered_ids: list[int] = []
        self.current_page = 0
        self.total_pages = 0
        self.valid_tags: set[str] = set()
        
        self.setMinimumSize(1000, 700)
        
        # 性能优化：缓存
        self._pixmap_cache = {}  # {book_id: QPixmap}
        self._search_ids_cache = {}  # {query_str: set[int]}
        self._cached_sorted_ids = None # 缓存全库排序后的 ID
        
        # 防抖计时器
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._do_search_refresh)
        
        self.setup_ui()
        self.load_books()
    
    def setup_ui(self):
        menubar = self.menuBar()
        self.file_menu = menubar.addMenu(i18n.t("menu_file"))
        self.file_menu.addAction(i18n.t("action_open_library"), self.open_library)
        self.file_menu.addSeparator()
        self.file_menu.addAction(i18n.t("action_exit"), self.close)
        
        self.lang_menu = menubar.addMenu(i18n.t("menu_language"))
        for lang_code in i18n.languages:
            name = i18n.config.get(lang_code, "name", fallback=lang_code)
            self.lang_menu.addAction(name, lambda c=lang_code: self.change_language(c))
        
        self.trash_menu = menubar.addMenu(i18n.t("menu_trash"))
        self.trash_menu.addAction(i18n.t("action_browse_trash"), self.browse_trash)
        self.trash_menu.addAction(i18n.t("action_empty_trash"), self.empty_trash)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(i18n.t("search_placeholder"))
        self.search_edit.textChanged.connect(self.filter_books)
        toolbar.addWidget(self.search_edit)
        
        self.select_all_btn = QPushButton(i18n.t("btn_select_all"))
        self.select_all_btn.clicked.connect(self.select_all)
        toolbar.addWidget(self.select_all_btn)
        
        self.deselect_all_btn = QPushButton(i18n.t("btn_deselect_all"))
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        toolbar.addWidget(self.deselect_all_btn)
        
        self.refresh_btn = QPushButton(i18n.t("btn_refresh"))
        self.refresh_btn.clicked.connect(self.load_books)
        toolbar.addWidget(self.refresh_btn)
        
        self.edit_btn = QPushButton(i18n.t("btn_edit_selected"))
        self.edit_btn.clicked.connect(self.edit_selected)
        toolbar.addWidget(self.edit_btn)
        
        self.delete_btn = QPushButton(i18n.t("btn_delete_selected"))
        self.delete_btn.clicked.connect(self.delete_selected)
        toolbar.addWidget(self.delete_btn)
        layout.addLayout(toolbar)

        filter_layout = QHBoxLayout()
        self.filter_label = QLabel(i18n.t("label_filter"))
        filter_layout.addWidget(self.filter_label)
        self.lang_filter = QComboBox()
        self.lang_filter.currentIndexChanged.connect(lambda: self.refresh_table())
        filter_layout.addWidget(self.lang_filter)
        self.tag_filter = CheckableComboBox()
        self.tag_filter.setPlaceholderText(i18n.t("tag_all"))
        self.tag_filter.setMinimumWidth(150)
        self.tag_filter.changed.connect(lambda: self.refresh_table())
        filter_layout.addWidget(self.tag_filter)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.update_table_headers()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.on_row_double_clicked)
        self.table.verticalHeader().setDefaultSectionSize(self.COVER_HEIGHT + 10)
        layout.addWidget(self.table)
        
        page_layout = QHBoxLayout()
        self.prev_btn = QPushButton(i18n.t("btn_prev_page"))
        self.prev_btn.clicked.connect(self.prev_page)
        page_layout.addWidget(self.prev_btn)
        self.page_label = QLabel()
        page_layout.addWidget(self.page_label)
        self.next_btn = QPushButton(i18n.t("btn_next_page"))
        self.next_btn.clicked.connect(self.next_page)
        page_layout.addWidget(self.next_btn)
        page_layout.addStretch()
        layout.addLayout(page_layout)
        
        self.status_label = QLabel()
        layout.addWidget(self.status_label)
        self.filter_info_label = QLabel()
        self.filter_info_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.filter_info_label)

    def update_table_headers(self):
        headers = [i18n.t(f"header_{k}") for k in ["select", "cover", "id", "title", "author", "publisher", "pubdate", "language", "tags"]]
        self.table.setHorizontalHeaderLabels(headers)

    def change_language(self, lang_code: str):
        i18n.set_language(lang_code)
        self.setWindowTitle(i18n.t("window_title"))
        self.file_menu.setTitle(i18n.t("menu_file"))
        self.lang_menu.setTitle(i18n.t("menu_language"))
        self.search_edit.setPlaceholderText(i18n.t("search_placeholder"))
        self.select_all_btn.setText(i18n.t("btn_select_all"))
        self.deselect_all_btn.setText(i18n.t("btn_deselect_all"))
        self.refresh_btn.setText(i18n.t("btn_refresh"))
        self.edit_btn.setText(i18n.t("btn_edit_selected"))
        self.filter_label.setText(i18n.t("label_filter"))
        self.tag_filter.setPlaceholderText(i18n.t("tag_all"))
        self.update_table_headers()
        self.prev_btn.setText(i18n.t("btn_prev_page"))
        self.next_btn.setText(i18n.t("btn_next_page"))
        self._update_filter_options()
        self.refresh_table()

    def open_library(self):
        path = QFileDialog.getExistingDirectory(self, "Library", self.library_path)
        if path and (Path(path) / "metadata.db").exists():
            self.library_path = path
            self.manager = None
            self.load_books()
        elif path:
            QMessageBox.warning(self, i18n.t("msg_title_warning"), i18n.t("msg_invalid_path"))

    def load_books(self, keep_page=False):
        try:
            if not self.manager: self.manager = BookManager(self.library_path)
            
            # 只有在初次加载或非 keep_page 时重刷排序索引
            if self._cached_sorted_ids is None or not keep_page:
                self.sorted_book_ids = self.manager.get_book_ids_sorted_by_timestamp(descending=True)
                self._cached_sorted_ids = self.sorted_book_ids
                self._search_ids_cache.clear() # ID 变动，清空搜索缓存
            else:
                self.sorted_book_ids = self._cached_sorted_ids

            self.filtered_ids = list(self.sorted_book_ids)
            if not keep_page: self.current_page = 0
            self._update_filter_options()
            self.refresh_table()
        except Exception as e:
            QMessageBox.critical(self, i18n.t("msg_title_error"), i18n.t("msg_load_failed", error=str(e)))

    def _update_filter_options(self):
        self.lang_filter.blockSignals(True)
        self.tag_filter.blockSignals(True)
        curr_l, curr_t = self.lang_filter.currentData(), self.tag_filter.currentData()
        self.lang_filter.clear()
        self.lang_filter.addItem(i18n.t("lang_all"), "all")
        self.lang_filter.addItem(i18n.t("item_empty"), "none")
        for l in self.manager.get_all_languages():
            name = REVERSE_LANG_MAP.get(l, l)
            self.lang_filter.addItem(f"{name} ({l})" if name != l else l, l)
        self.tag_filter.clear()
        all_tags = self.manager.get_all_tags()
        self.valid_tags = set(all_tags)
        self.tag_filter.addItem(i18n.t("item_empty"), "none")
        self.tag_filter.addItem(i18n.t("item_invalid_tag"), "invalid")
        self.tag_filter.addItems(all_tags)
        idx = self.lang_filter.findData(curr_l)
        if idx >= 0: self.lang_filter.setCurrentIndex(idx)
        if curr_t: self.tag_filter.setCheckedItems(curr_t)
        else: self.tag_filter._update_text()
        self.lang_filter.blockSignals(False)
        self.tag_filter.blockSignals(False)

    def refresh_table(self, filter_text: Optional[str] = None):
        self.table.setRowCount(0)
        search = (filter_text if filter_text is not None else self.search_edit.text()).strip()
        lang_target, tag_targets = self.lang_filter.currentData(), self.tag_filter.currentData()
        
        q = []
        if search: q.append(f'(title:"~{search}" or authors:"~{search}" or tags:"~{search}" or publisher:"~{search}")')
        if lang_target and lang_target != 'all':
            q.append('languages:false' if lang_target == 'none' else f'languages:"={lang_target}"')
        if tag_targets:
            for t in tag_targets:
                if t == 'none': q.append('tags:false')
                elif t != 'invalid': q.append(f'tags:"={t}"')
        
        final_q = " and ".join(q) if q else ""
        
        try:
            if not final_q:
                self.filtered_ids = list(self.sorted_book_ids)
            elif final_q in self._search_ids_cache:
                matched = self._search_ids_cache[final_q]
                self.filtered_ids = [bid for bid in self.sorted_book_ids if bid in matched]
            else:
                matched = self.manager.search(final_q)
                self._search_ids_cache[final_q] = matched
                self.filtered_ids = [bid for bid in self.sorted_book_ids if bid in matched]
                
            if tag_targets and 'invalid' in tag_targets:
                # 优化非法标签过滤：直接使用集合操作
                tag_map = self.manager.cache.all_field_for('tags', self.filtered_ids)
                self.filtered_ids = [bid for bid in self.filtered_ids if any(t not in self.valid_tags for t in tag_map.get(bid, ()))]
        except: self.filtered_ids = []

        self.total_pages = (len(self.filtered_ids) + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        self.current_page = max(0, min(self.current_page, self.total_pages - 1))
        page_ids = self.filtered_ids[self.current_page*self.PAGE_SIZE:(self.current_page+1)*self.PAGE_SIZE]
        self.books = self.manager.get_metadata_batch(page_ids)
        
        self.table.blockSignals(True)
        self.table.setRowCount(len(page_ids))
        for row, bid in enumerate(page_ids):
            meta = self.books.get(bid)
            if not meta: continue
            
            # 使用复选框
            cb_widget = QWidget(); l = QHBoxLayout(cb_widget); l.setContentsMargins(0,0,0,0); l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb = QCheckBox(); cb.stateChanged.connect(self.update_status); l.addWidget(cb); self.table.setCellWidget(row, 0, cb_widget)
            
            # 使用带缓存的缩略图图标
            pix = self.get_cover_pixmap(bid)
            if pix:
                # 缓存缩略图版图标，避免重复缩放
                icon_key = f"thumb_{bid}"
                if icon_key not in self._pixmap_cache:
                    thumb = pix.scaledToHeight(self.COVER_HEIGHT, Qt.TransformationMode.SmoothTransformation)
                    icon = QIcon(thumb)
                    self._pixmap_cache[icon_key] = (icon, thumb.size())
                
                icon, size = self._pixmap_cache[icon_key]
                btn = QPushButton(); btn.setFlat(True)
                btn.setIcon(icon); btn.setIconSize(size); btn.setFixedSize(size)
                btn.clicked.connect(lambda checked, p=pix: self.show_cover_dialog(p))
                self.table.setCellWidget(row, 1, btn)
            else:
                lbl = QLabel(i18n.t("no_cover")); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.table.setCellWidget(row, 1, lbl)
            
            item = QTableWidgetItem(str(bid)); item.setData(Qt.ItemDataRole.UserRole, bid); self.table.setItem(row, 2, item)
            self.table.setItem(row, 3, QTableWidgetItem(meta.title))
            self.table.setItem(row, 4, QTableWidgetItem(", ".join(meta.authors)))
            self.table.setItem(row, 5, QTableWidgetItem(meta.publisher))
            self.table.setItem(row, 6, QTableWidgetItem(meta.pubdate))
            langs = [REVERSE_LANG_MAP.get(ln, ln) for ln in meta.languages]
            self.table.setItem(row, 7, QTableWidgetItem(", ".join(langs)))
            self.table.setItem(row, 8, QTableWidgetItem(", ".join(meta.tags)))
        self.table.blockSignals(False)

        self._update_filter_label(search, lang_target, tag_targets)
        self.update_status()
        self.update_page_controls()

    def _update_filter_label(self, search, lang, tags):
        p = []
        if search: p.append(i18n.t("filter_search", text=search))
        if lang and lang != 'all': p.append(i18n.t("filter_lang", name=REVERSE_LANG_MAP.get(lang, lang)))
        if tags:
            tn = [i18n.t(f"item_{'empty' if t=='none' else 'invalid_tag'}") if t in ('none','invalid') else t for t in tags]
            p.append(i18n.t("filter_tag", names=', '.join(tn)))
        self.filter_info_label.setText(f"{i18n.t('filter_info_prefix')} {' | '.join(p)}" if p else "")

    def update_status(self):
        sel = len(self.get_selected_books())
        self.status_label.setText(i18n.t("status_format", total=len(self.filtered_ids), selected=sel))

    def update_page_controls(self):
        self.page_label.setText(i18n.t("page_format", current=self.current_page+1, total=self.total_pages))
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < self.total_pages - 1)

    def prev_page(self):
        if self.current_page > 0: self.current_page -= 1; self.refresh_table()

    def next_page(self):
        if self.current_page < self.total_pages - 1: self.current_page += 1; self.refresh_table()

    def get_cover_pixmap(self, bid):
        # 内存缓存检查
        if bid in self._pixmap_cache:
            return self._pixmap_cache[bid]
            
        try:
            d = self.manager.get_cover(bid)
            if d:
                img = QImage(); img.loadFromData(d)
                pix = QPixmap.fromImage(img)
                # 缩放并保存到缓存（如果是用于表格显示的缩略图，可以缓存缩略图提高性能）
                self._pixmap_cache[bid] = pix
                return pix
        except: pass
        return None

    def show_cover_dialog(self, pix):
        d = QDialog(self); d.setWindowTitle("Cover"); l = QVBoxLayout(d); lbl = QLabel()
        lbl.setPixmap(pix.scaled(600, 800, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        l.addWidget(lbl); d.exec()

    def filter_books(self, t): 
        # 启动防抖计时器
        self.search_timer.start(300) # 300ms 延迟
        
    def _do_search_refresh(self):
        self.refresh_table()
    def select_all(self): self._set_all_checks(True)
    def deselect_all(self): self._set_all_checks(False)
    def _set_all_checks(self, v):
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w:
                c = w.findChild(QCheckBox)
                if c: c.setChecked(v)

    def get_selected_books(self):
        res = []
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w:
                c = w.findChild(QCheckBox)
                if c and c.isChecked():
                    it = self.table.item(r, 2)
                    if it:
                        bid = it.data(Qt.ItemDataRole.UserRole)
                        if bid in self.books: res.append(self.books[bid])
        return res

    def on_row_double_clicked(self, idx):
        if idx.column() == 1: return
        it = self.table.item(idx.row(), 2)
        if it:
            bid = it.data(Qt.ItemDataRole.UserRole)
            if bid in self.books: self._do_edit([self.books[bid]])

    def edit_selected(self):
        sel = self.get_selected_books()
        if not sel: QMessageBox.warning(self, i18n.t("msg_title_tip"), i18n.t("msg_no_selection")); return
        self._do_edit(sel)

    def delete_selected(self):
        sel = self.get_selected_books()
        if not sel:
            QMessageBox.warning(self, i18n.t("msg_title_tip"), i18n.t("msg_no_selection"))
            return
        
        # 二次确认对话框
        count = len(sel)
        titles = "\n".join([f"• {b.title}" for b in sel[:5]])
        if count > 5:
            titles += f"\n... (共 {count} 本)"
        
        reply = QMessageBox.question(
            self,
            i18n.t("msg_title_warning"),
            i18n.t("msg_delete_confirm", count=count) + "\n\n" + titles,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                bids = [b.id for b in sel]
                self.manager.delete_books(bids, permanent=False)
                # 清除缓存
                self._cached_sorted_ids = None
                self._search_ids_cache.clear()
                for bid in bids:
                    self._pixmap_cache.pop(bid, None)
                    self._pixmap_cache.pop(f"thumb_{bid}", None)
                QMessageBox.information(self, i18n.t("msg_title_success"), i18n.t("msg_delete_success", count=count))
                self.load_books()
            except Exception as e:
                QMessageBox.critical(self, i18n.t("msg_title_error"), i18n.t("msg_delete_failed", error=str(e)))

    def browse_trash(self):
        """浏览回收站"""
        dialog = TrashDialog(self.manager, self)
        dialog.exec()
        # 刷新主列表以反映恢复的书籍
        self._cached_sorted_ids = None
        self._search_ids_cache.clear()
        self.load_books()

    def empty_trash(self):
        """清空回收站"""
        # 获取回收站内容用于确认
        trash_items = self.manager.list_trash()
        if not trash_items:
            QMessageBox.information(self, i18n.t("msg_title_tip"), i18n.t("msg_trash_empty"))
            return
        
        count = len(trash_items)
        reply = QMessageBox.question(
            self,
            i18n.t("msg_title_warning"),
            i18n.t("msg_empty_trash_confirm", count=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.manager.empty_trash()
                QMessageBox.information(self, i18n.t("msg_title_success"), i18n.t("msg_empty_trash_success", count=count))
            except Exception as e:
                QMessageBox.critical(self, i18n.t("msg_title_error"), i18n.t("msg_empty_trash_failed", error=str(e)))

    def _do_edit(self, sel):

        tags_file = Path("tags.txt")
        all_tags = [l.strip() for l in tags_file.read_text("utf-8").splitlines() if l.strip()] if tags_file.exists() else []
        d = EditDialog(sel, self.manager.get_all_languages(), all_tags, self)
        if d.exec() == QDialog.DialogCode.Accepted:
            upd = d.get_updates()
            if not upd: return
            
            # 显示确定模式的进度对话框
            from PyQt5.QtWidgets import QProgressDialog
            self.progress = QProgressDialog(i18n.t("msg_updating"), None, 0, 100, self)
            self.progress.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress.setWindowTitle(i18n.t("msg_title_tip"))
            self.progress.setValue(0)
            self.progress.show()
            
            bids = [b.id for b in sel]
            self.worker = UpdateWorker(self.manager, bids, upd)
            
            def on_progress(value):
                self.progress.setValue(value)
            
            def on_finished(success, error_msg):
                self.progress.close()
                if success:
                    # 更新成功后清除受影响的查缓存
                    self._search_ids_cache.clear()
                    # 重新刷新书籍列表（保持当前页）
                    self.load_books(keep_page=True)
                    QMessageBox.information(self, i18n.t("msg_title_success"), i18n.t("msg_update_success", count=len(sel)))
                else:
                    QMessageBox.critical(self, i18n.t("msg_title_error"), i18n.t("msg_update_failed", error=error_msg))
            
            self.worker.progress.connect(on_progress)
            self.worker.finished.connect(on_finished)
            self.worker.start()


def main():
    global i18n
    i18n = I18n("langs.ini")
    lib = r"C:\Users\Muffy\Calibre 书库"
    if len(sys.argv) > 1: lib = sys.argv[1]
    if not Path(lib).exists(): print(f"Error: {lib} not found"); sys.exit(1)
    app = QApplication(sys.argv); app.setStyle('Fusion')
    win = MainWindow(lib); win.show(); sys.exit(app.exec())

if __name__ == "__main__":
    main()
