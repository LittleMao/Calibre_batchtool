"""Calibre数据库接口封装"""
from pathlib import Path
from typing import Any
from dataclasses import dataclass


@dataclass
class BookMetadata:
    """图书元数据"""
    id: int
    title: str
    authors: list[str]
    tags: list[str]
    publisher: str = ""  # 出版社
    pubdate: str = ""  # 出版日期
    languages: list[str] = None  # 语言
    timestamp: float = 0.0  # 添加时间戳
    
    def __post_init__(self):
        if self.languages is None:
            self.languages = []


class BookManager:
    """Calibre图书库管理器"""
    
    def __init__(self, library_path: str):
        """
        初始化图书管理器
        
        Args:
            library_path: Calibre库路径
        """
        from calibre.library import db
        self.library_path = Path(library_path)
        self.cache = db(str(self.library_path)).new_api
    
    def get_all_book_ids(self) -> set[int]:
        """获取全部图书ID"""
        return self.cache.all_book_ids()
    
    def get_book_ids_sorted_by_timestamp(self, descending: bool = True) -> list[int]:
        """
        获取按添加时间排序的图书ID列表 (利用 Calibre 原生索引排序)
        """
        # multisort 期望 (field, ascending)，ascending=True 为升序
        # descending=True 意味着需要降序，所以 ascending = not descending
        return list(self.cache.multisort([('timestamp', not descending)]))
    
    def get_metadata(self, book_id: int) -> BookMetadata:
        """
        获取单本书的元数据
        
        Args:
            book_id: 图书ID
            
        Returns:
            BookMetadata对象
        """
        # 使用get_proxy_metadata提高性能
        mi = self.cache.get_proxy_metadata(book_id)
        return BookMetadata(
            id=book_id,
            title=mi.title or "",
            authors=list(mi.authors) if mi.authors else [],
            tags=list(mi.tags) if mi.tags else [],
        )
    
    def get_metadata_batch(self, book_ids: list[int]) -> dict[int, BookMetadata]:
        """
        批量获取元数据
        
        Args:
            book_ids: 图书ID列表
            
        Returns:
            {book_id: BookMetadata} 字典
        """
        if not book_ids:
            return {}
            
        titles = self.cache.all_field_for('title', book_ids)
        authors = self.cache.all_field_for('authors', book_ids)
        tags = self.cache.all_field_for('tags', book_ids)
        timestamps = self.cache.all_field_for('timestamp', book_ids)
        publishers = self.cache.all_field_for('publisher', book_ids)
        pubdates = self.cache.all_field_for('pubdate', book_ids)
        languages = self.cache.all_field_for('languages', book_ids)
        
        result = {}
        for book_id in book_ids:
            ts = timestamps.get(book_id)
            pd = pubdates.get(book_id)
            result[book_id] = BookMetadata(
                id=book_id,
                title=titles.get(book_id, ""),
                authors=list(authors.get(book_id, ())),
                tags=list(tags.get(book_id, ())),
                publisher=publishers.get(book_id, "") or "",
                pubdate=pd.strftime('%Y-%m-%d') if pd else "",
                languages=[getattr(l, 'name', str(l)) for l in languages.get(book_id, ())],
                timestamp=ts.timestamp() if ts else 0.0,
            )
        return result
    
    def get_cover(self, book_id: int, as_path: bool = False) -> bytes | str | None:
        """
        获取封面
        
        Args:
            book_id: 图书ID
            as_path: True则返回临时文件路径，False返回字节串
            
        Returns:
            封面数据或路径，无封面时返回None
        """
        return self.cache.cover(book_id, as_path=as_path)
    
    def search(self, query: str) -> set[int]:
        """
        搜索图书
        
        Args:
            query: 搜索查询字符串（Calibre搜索语法）
            
        Returns:
            匹配的图书ID集合
        """
        return self.cache.search(query)
    
    def set_field(self, field_name: str, book_id_to_val: dict[int, Any]) -> None:
        """
        批量设置字段值
        """
        self.cache.set_field(field_name, book_id_to_val)
    
    def set_metadata(self, book_id: int, metadata: Any) -> None:
        """
        设置完整元数据
        """
        self.cache.set_metadata(book_id, metadata)
    
    def delete_books(self, book_ids: list[int], permanent: bool = False) -> None:
        """
        删除图书
        
        Args:
            book_ids: 要删除的图书 ID 列表
            permanent: 是否永久删除（True=彻底删除，False=移至回收站）
        """
        self.cache.remove_books(set(book_ids), permanent=permanent)

    
    def start_bulk_update(self):
        """开始批量更新事务（加速写入）"""
        if hasattr(self.cache, 'start_bulk_update'):
            self.cache.start_bulk_update()

    def end_bulk_update(self):
        """结束批量更新事务并提交变化"""
        if hasattr(self.cache, 'end_bulk_update'):
            self.cache.end_bulk_update()

    def get_all_languages(self) -> list[str]:
        """获取库中存在的所有语言"""
        langs = self.cache.get_categories().get('languages', [])
        return sorted([getattr(lang, 'name', str(lang)) for lang in langs])

    def get_all_tags(self) -> list[str]:
        """获取库中存在的所有标签"""
        tags = self.cache.get_categories().get('tags', [])
        return sorted([getattr(tag, 'name', str(tag)) for tag in tags])

    def list_trash(self) -> list[dict]:
        """
        列出回收站中的图书
        """
        trash_items = []
        trash_dir = self.library_path / '.caltrash'
        
        if not trash_dir.exists():
            return trash_items
        
        import re
        
        # 遍历 .caltrash 下的所有子目录
        for author_dir in trash_dir.iterdir():
            if not author_dir.is_dir():
                continue
            # Calibre 结构: .caltrash/作者名/书名 (ID)/
            for book_dir in author_dir.iterdir():
                if not book_dir.is_dir():
                    continue
                
                title = book_dir.name
                book_id = 0
                authors = [author_dir.name]
                
                # 尝试从目录名解析书名和 ID（格式：书名 (ID)）
                match = re.match(r'^(.+)\s+\((\d+)\)$', book_dir.name)
                if match:
                    title = match.group(1)
                    book_id = int(match.group(2))
                
                # 尝试读取 metadata.opf 获取更准确的信息
                opf_file = book_dir / 'metadata.opf'
                if opf_file.exists():
                    try:
                        import xml.etree.ElementTree as ET
                        tree = ET.parse(opf_file)
                        root = tree.getroot()
                        ns = {'dc': 'http://purl.org/dc/elements/1.1/'}
                        
                        title_elem = root.find('.//dc:title', ns)
                        if title_elem is not None and title_elem.text:
                            title = title_elem.text
                        
                        creator_elem = root.find('.//dc:creator', ns)
                        if creator_elem is not None and creator_elem.text:
                            authors = [creator_elem.text]
                    except:
                        pass
                
                trash_items.append({
                    'book_id': book_id,
                    'title': title,
                    'authors': authors,
                    'path': str(book_dir),
                })
        
        return trash_items


    def restore_books(self, book_paths: list[str]) -> int:
        """从回收站恢复图书"""
        import shutil
        restored = 0
        
        for path in book_paths:
            src = Path(path)
            if not src.exists():
                continue
            
            # 查找书籍文件
            book_files = []
            for f in src.iterdir():
                if f.is_file() and f.suffix.lower() in ('.epub', '.mobi', '.azw3', '.pdf', '.txt', '.fb2', '.cbz', '.cbr'):
                    book_files.append(str(f))
            
            if not book_files:
                continue
            
            try:
                # 尝试读取 metadata.opf
                from calibre.ebooks.metadata.opf2 import OPF
                opf_file = src / 'metadata.opf'
                mi = None
                if opf_file.exists():
                    with open(opf_file, 'rb') as f:
                        mi = OPF(f).to_book_metadata()
                
                # 添加到 Calibre 数据库
                if mi:
                    book_id = self.cache.create_book_entry(mi)
                else:
                    from calibre.ebooks.metadata.book.base import Metadata
                    # 从目录名提取书名
                    import re
                    match = re.match(r'^(.+)\s+\(\d+\)$', src.name)
                    title = match.group(1) if match else src.name
                    mi = Metadata(title)
                    book_id = self.cache.create_book_entry(mi)
                
                # 添加格式文件
                for book_file in book_files:
                    fmt = Path(book_file).suffix[1:].upper()
                    with open(book_file, 'rb') as f:
                        self.cache.add_format(book_id, fmt, f)
                
                # 如果有封面，也添加封面
                cover_file = src / 'cover.jpg'
                if cover_file.exists():
                    with open(cover_file, 'rb') as f:
                        self.cache.set_cover({book_id: f.read()})
                
                # 删除回收站中的目录
                shutil.rmtree(src)
                
                # 检查父目录（作者目录）是否为空，如果为空则删除
                parent = src.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                
                restored += 1
            except Exception as e:
                print(f"Error restoring {path}: {e}")
                continue
        
        return restored


    def empty_trash(self) -> int:
        """清空回收站（永久删除）"""
        import shutil
        trash_dir = self.library_path / '.caltrash'
        count = 0
        if trash_dir.exists():
            for item in trash_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                    count += 1
                else:
                    item.unlink()
        return count
