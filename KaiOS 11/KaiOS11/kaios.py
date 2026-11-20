import sys, os, json, csv, re, urllib.request, urllib.parse, html
from kaios_accounts import AccountStore, DATA_HOME, MAX_ACCOUNTS, profile_directory
from pathlib import Path
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QSize
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QAction, QPixmap, QFont, QShortcut, QKeySequence, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLineEdit, QTextEdit, QMessageBox, QFileDialog,
    QDialog, QListWidget, QListWidgetItem, QTabWidget, QMenu, QInputDialog,
    QTableWidget, QTableWidgetItem, QComboBox, QSpinBox, QCalendarWidget,
    QSplitter, QScrollArea, QTextBrowser, QToolButton
)

ROOT = Path.home() / "KaiOS11"
DESKTOP = ROOT / "Desktop"
DOCUMENTS = ROOT / "Documents"
PHOTOS = ROOT / "Photos"
DOWNLOADS = ROOT / "Downloads"
for folder in (ROOT, DESKTOP, DOCUMENTS, PHOTOS, DOWNLOADS):
    folder.mkdir(parents=True, exist_ok=True)

WALLPAPER = Path(__file__).with_name("kaios_wallpaper.png")
BG = "#11152f"
PANEL = "#1d2450"
ACCENT = "#7165ff"
TEXT = "#f4f4ff"

# One logo throughout KaiOS: app title bars, taskbar, login screen and launchers.
KAIOS_ICON_FILE = Path(__file__).resolve().parent / 'kaios_logo.ico'
KAIOS_LOGO_PNG = Path(__file__).resolve().parent / 'kaios_logo.png'
ACCOUNT_ARROW_PNG = Path(__file__).resolve().parent / 'account_dropdown_arrow.png'


def kaios_icon():
    # The PNG has clean transparent corners. Qt scales it smoothly for
    # desktop tiles, application titlebars, menus and the Start button.
    if KAIOS_LOGO_PNG.is_file():
        return QIcon(str(KAIOS_LOGO_PNG))
    return QIcon(str(KAIOS_ICON_FILE))


def info(parent, message):
    QMessageBox.information(parent, "KaiOS 11", message)

def error(parent, message):
    QMessageBox.warning(parent, "KaiOS 11", message)

def safe_filename(name, default="Untitled"):
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_.").strip()
    return cleaned or default

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if not self.skip:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)

def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "KaiOSBrowser/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
    parser = TextExtractor()
    parser.feed(raw.decode(charset, errors="replace"))
    return " ".join(parser.parts)[:12000]

def search_web(query):
    endpoint = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0 (compatible; KaiOSBrowser/1.0)"})
    with urllib.request.urlopen(req, timeout=15) as response:
        page = response.read(2_000_000).decode("utf-8", errors="replace")
    # Parse DuckDuckGo result anchors and snippets without opening Chrome.
    import re
    results = []
    blocks = re.findall(r'<div class="result[^"]*".*?</div>\s*</div>', page, flags=re.S)
    for block in blocks:
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</', block, re.S)
        if link:
            url = html.unescape(link.group(1))
            title = re.sub("<.*?>", "", link.group(2))
            desc = re.sub("<.*?>", "", snippet.group(1)) if snippet else ""
            title = html.unescape(" ".join(title.split()))
            desc = html.unescape(" ".join(desc.split()))
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http"):
                results.append({"title": title, "url": url, "snippet": desc})
        if len(results) >= 10:
            break
    if not results:
        # Friendly fallback: show a direct search link if DDG changes its markup.
        results.append({"title": "Open DuckDuckGo search results", "url": endpoint,
                        "snippet": "The search provider changed its page format. Open the results page to continue."})
    return results

class LocalSearchServer:
    def __init__(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                try:
                    if parsed.path == "/search":
                        data = {"results": search_web(params.get("q", [""])[0])}
                    elif parsed.path == "/page":
                        url = params.get("url", [""])[0]
                        if not url.startswith(("http://", "https://")):
                            raise ValueError("Only http and https pages are supported.")
                        data = {"url": url, "text": fetch_url(url)}
                    else:
                        data = {"error": "Unknown endpoint"}
                    payload = json.dumps(data).encode("utf-8")
                    self.send_response(200)
                except Exception as exc:
                    payload = json.dumps({"error": str(exc)}).encode("utf-8")
                    self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
        self.thread = Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
    def stop(self):
        self.http.shutdown()
        self.http.server_close()

class FetchWorker(QThread):
    finished_data = Signal(dict)
    def __init__(self, endpoint):
        super().__init__()
        self.endpoint = endpoint
    def run(self):
        try:
            req = urllib.request.Request(self.endpoint, headers={"User-Agent": "KaiOS/1.0"})
            with urllib.request.urlopen(req, timeout=25) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.finished_data.emit(data)
        except Exception as exc:
            self.finished_data.emit({"error": str(exc)})

class AppWindow(QMainWindow):
    def __init__(self, title, size=(850, 600)):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowIcon(kaios_icon())
        self.resize(*size)
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}
            QPushButton {{ background: {PANEL}; color: {TEXT}; border: 1px solid #414b85;
                border-radius: 9px; padding: 8px 12px; }}
            QPushButton:hover {{ background: #303a76; }}
            QLineEdit, QTextEdit, QTableWidget, QListWidget, QComboBox {{
                background: #f8f8ff; color: #17182c; border-radius: 6px; padding: 6px; }}
            QLabel {{ color: {TEXT}; }}
        """)

class BrowserWindow(AppWindow):
    """KaiOS-styled browser using Qt WebEngine (Chromium)."""
    def __init__(self, start_url="https://www.google.com/"):
        super().__init__("Kai's Browser", (1120, 760))
        self.setMinimumSize(760, 520)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        # KaiOS-styled tab strip at the very top.
        tab_row = QHBoxLayout()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.current_tab_changed)
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #4b4b91; background: #171a35; }
            QTabBar::tab { background: #252653; color: #f4f4ff;
                border: 1px solid #6560b7; border-bottom: none;
                border-top-left-radius: 9px; border-top-right-radius: 9px;
                padding: 9px 18px; margin-right: 4px; min-width: 105px; }
            QTabBar::tab:selected { background: #5145a8; color: white; }
            QTabBar::tab:hover { background: #393777; }
        """)
        tab_row.addWidget(self.tabs, 1)
        new_tab = QPushButton("+")
        new_tab.setFixedSize(42, 36)
        new_tab.setToolTip("New tab")
        new_tab.clicked.connect(lambda: self.add_tab("https://www.google.com/"))
        tab_row.addWidget(new_tab)
        layout.addLayout(tab_row)

        # Browser toolbar, styled to match KaiOS.
        toolbar = QHBoxLayout()
        self.back_button = QPushButton("<")
        self.forward_button = QPushButton(">")
        self.reload_button = QPushButton("Refresh")
        self.home_button = QPushButton("Home")
        for button in (self.back_button, self.forward_button,
                       self.reload_button, self.home_button):
            button.setFixedHeight(38)
            toolbar.addWidget(button)

        self.address = QLineEdit()
        self.address.setPlaceholderText("Search Google or enter a website address")
        self.address.setFixedHeight(38)
        toolbar.addWidget(self.address, 1)
        self.go_button = QPushButton("Go")
        self.go_button.setFixedHeight(38)
        toolbar.addWidget(self.go_button)
        layout.addLayout(toolbar)

        self.status = QLabel("KaiOS Browser is ready.")
        self.status.setStyleSheet("color:#c5c5e8;padding:3px")
        layout.addWidget(self.status)
        self.setCentralWidget(root)

        self.go_button.clicked.connect(self.navigate_address)
        self.address.returnPressed.connect(self.navigate_address)
        self.home_button.clicked.connect(lambda: self.current_view().setUrl(QUrl("https://www.google.com/")))
        self.back_button.clicked.connect(lambda: self.current_view().back())
        self.forward_button.clicked.connect(lambda: self.current_view().forward())
        self.reload_button.clicked.connect(lambda: self.current_view().reload())

        self.add_tab(start_url)

    def current_view(self):
        view = self.tabs.currentWidget()
        if view is None:
            return self.add_tab("https://www.google.com/")
        return view

    def add_tab(self, url="https://www.google.com/"):
        view = QWebEngineView()
        index = self.tabs.addTab(view, "New Tab")
        self.tabs.setCurrentIndex(index)
        view.titleChanged.connect(lambda title, v=view: self.update_tab_title(v, title))
        view.urlChanged.connect(lambda qurl, v=view: self.update_address(v, qurl))
        view.loadStarted.connect(lambda: self.status.setText("Loading page..."))
        view.loadFinished.connect(lambda ok: self.status.setText("Page loaded." if ok else "The page could not be loaded."))
        view.setUrl(QUrl(url))
        return view

    def update_tab_title(self, view, title):
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, (title or "New Tab")[:22])

    def update_address(self, view, url):
        if view is self.tabs.currentWidget():
            self.address.setText(url.toString())

    def current_tab_changed(self, index):
        if index >= 0:
            view = self.tabs.widget(index)
            if isinstance(view, QWebEngineView):
                self.address.setText(view.url().toString())

    def close_tab(self, index):
        if index < 0 or index >= self.tabs.count():
            return
        if self.tabs.count() <= 1:
            self.tabs.widget(index).setUrl(QUrl("https://www.google.com/"))
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        widget.deleteLater()

    def navigate_address(self):
        text = self.address.text().strip()
        if not text:
            return
        if re.match(r"^https?://", text, re.I):
            url = text
        elif re.match(r"^[\w.-]+\.[a-z]{2,}([/:?#]|$)", text, re.I):
            url = "https://" + text
        else:
            url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(text)
        self.current_view().setUrl(QUrl(url))

class WordWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's Word")
        self.path = None
        w = QWidget(); l = QVBoxLayout(w)
        top = QHBoxLayout()
        for label, fn in [("New", self.new_doc), ("Open", self.open_doc), ("Save", self.save_doc), ("Save As", self.save_as)]:
            b = QPushButton(label); b.clicked.connect(fn); top.addWidget(b)
        top.addStretch(); l.addLayout(top)
        self.editor = QTextEdit(); self.editor.setPlaceholderText("Start writing your document...")
        self.editor.setStyleSheet("background:white;color:#202030;font-size:16px;padding:18px")
        l.addWidget(self.editor); self.setCentralWidget(w)
    def new_doc(self): self.path = None; self.editor.clear()
    def open_doc(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open document", str(DOCUMENTS), "Documents (*.html *.htm *.txt);;All files (*)")
        if p:
            self.path = Path(p); self.editor.setHtml(self.path.read_text(encoding="utf-8", errors="replace"))
    def save_doc(self):
        if not self.path: return self.save_as()
        self.path.write_text(self.editor.toHtml(), encoding="utf-8"); info(self, "Document saved in KaiOS Documents.")
    def save_as(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save Kai's Word document", str(DOCUMENTS / "Document.html"), "HTML document (*.html);;Text file (*.txt)")
        if p:
            self.path = Path(p); self.save_doc()

class ExcelWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's Excel", (950, 650))
        w = QWidget(); l = QVBoxLayout(w); bar = QHBoxLayout()
        for label, fn in [("New sheet", self.new_sheet), ("Open CSV", self.open_csv), ("Save CSV", self.save_csv)]:
            b = QPushButton(label); b.clicked.connect(fn); bar.addWidget(b)
        bar.addStretch(); l.addLayout(bar)
        self.table = QTableWidget(20, 8)
        self.table.setHorizontalHeaderLabels([f"Column {i+1}" for i in range(8)])
        l.addWidget(self.table); self.setCentralWidget(w)
    def new_sheet(self): self.table.clearContents()
    def open_csv(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open spreadsheet", str(DOCUMENTS), "CSV files (*.csv)")
        if not p: return
        with open(p, newline="", encoding="utf-8-sig") as f: rows = list(csv.reader(f))
        cols = max([len(r) for r in rows], default=1)
        self.table.setRowCount(max(20, len(rows))); self.table.setColumnCount(max(8, cols))
        for r, row in enumerate(rows):
            for c, value in enumerate(row): self.table.setItem(r, c, QTableWidgetItem(value))
    def save_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save spreadsheet", str(DOCUMENTS / "Spreadsheet.csv"), "CSV files (*.csv)")
        if not p: return
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for r in range(self.table.rowCount()):
                writer.writerow([self.table.item(r,c).text() if self.table.item(r,c) else "" for c in range(self.table.columnCount())])
        info(self, "Spreadsheet saved as CSV.")

class PowerPointWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's PowerPoint", (950, 650))
        self.slides = []
        w = QWidget(); l = QHBoxLayout(w)
        self.list = QListWidget(); self.list.setMaximumWidth(210)
        right = QVBoxLayout()
        self.title_edit = QLineEdit(); self.title_edit.setPlaceholderText("Slide title")
        self.body = QTextEdit(); self.body.setPlaceholderText("Write your slide content...")
        controls = QHBoxLayout()
        for label, fn in [("Add slide", self.add_slide), ("Delete slide", self.delete_slide), ("Save", self.save), ("Open", self.open_file), ("Present", self.present)]:
            b = QPushButton(label); b.clicked.connect(fn); controls.addWidget(b)
        right.addWidget(self.title_edit); right.addWidget(self.body); right.addLayout(controls)
        l.addWidget(self.list); l.addLayout(right, 1); self.setCentralWidget(w)
        self.list.currentRowChanged.connect(self.select_slide)
        self.add_slide()
    def store_current(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.slides):
            self.slides[i] = {"title": self.title_edit.text(), "body": self.body.toPlainText()}
    def add_slide(self):
        self.store_current(); self.slides.append({"title": f"Slide {len(self.slides)+1}", "body": ""})
        self.list.addItem(f"Slide {len(self.slides)}"); self.list.setCurrentRow(len(self.slides)-1)
    def select_slide(self, i):
        if 0 <= i < len(self.slides):
            s = self.slides[i]; self.title_edit.setText(s["title"]); self.body.setPlainText(s["body"])
    def delete_slide(self):
        i = self.list.currentRow()
        if i >= 0 and len(self.slides) > 1:
            self.store_current(); self.slides.pop(i); self.list.takeItem(i)
            self.list.setCurrentRow(max(0, i-1))
    def save(self):
        self.store_current()
        p, _ = QFileDialog.getSaveFileName(self, "Save presentation", str(DOCUMENTS / "Presentation.kppt"), "KaiOS presentation (*.kppt)")
        if p: Path(p).write_text(json.dumps(self.slides, indent=2), encoding="utf-8"); info(self, "Presentation saved.")
    def open_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open presentation", str(DOCUMENTS), "KaiOS presentation (*.kppt)")
        if p:
            self.slides = json.loads(Path(p).read_text(encoding="utf-8")); self.list.clear()
            for i in range(len(self.slides)): self.list.addItem(f"Slide {i+1}")
            self.list.setCurrentRow(0)
    def present(self):
        self.store_current()
        dlg = QDialog(self); dlg.setWindowTitle("Kai's Presentation"); dlg.resize(900, 600)
        lay = QVBoxLayout(dlg); view = QLabel(); view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view.setStyleSheet("background:#171b44;color:white;font-size:28px;padding:40px")
        idx = [0]
        def show():
            s = self.slides[idx[0]]
            view.setText("<h1>" + html.escape(s["title"]) + "</h1><p>" + html.escape(s["body"]).replace("\\n","<br>") + "</p>")
        buttons = QHBoxLayout(); prev = QPushButton("Previous"); nxt = QPushButton("Next")
        prev.clicked.connect(lambda: (idx.__setitem__(0, max(0, idx[0]-1)), show()))
        nxt.clicked.connect(lambda: (idx.__setitem__(0, min(len(self.slides)-1, idx[0]+1)), show()))
        buttons.addWidget(prev); buttons.addWidget(nxt); lay.addWidget(view,1); lay.addLayout(buttons); show(); dlg.exec()

class PhotosWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's Photos")
        w = QWidget(); l = QVBoxLayout(w); bar = QHBoxLayout()
        self.label = QLabel("Choose a photo from your KaiOS Photos folder.")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumHeight(420)
        self.label.setStyleSheet("background:#20264d;border-radius:12px")
        openb = QPushButton("Open photo"); folderb = QPushButton("Open Photos folder")
        openb.clicked.connect(self.open_photo); folderb.clicked.connect(lambda: os.startfile(str(PHOTOS)) if sys.platform == "win32" else None)
        bar.addWidget(openb); bar.addWidget(folderb); l.addWidget(self.label,1); l.addLayout(bar); self.setCentralWidget(w)
    def open_photo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open photo", str(PHOTOS), "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if p:
            pix = QPixmap(p)
            if pix.isNull(): error(self, "Could not open this image."); return
            self.label.setPixmap(pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

class MailWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's Outlook", (900, 600))
        w = QWidget(); l = QHBoxLayout(w)
        self.folders = QListWidget(); self.folders.addItems(["Inbox (demo)", "Drafts", "Sent"])
        right = QVBoxLayout(); self.subject = QLineEdit(); self.subject.setPlaceholderText("Subject")
        self.to = QLineEdit(); self.to.setPlaceholderText("To")
        self.body = QTextEdit(); self.body.setPlaceholderText("Write an email...")
        send = QPushButton("Save draft"); send.clicked.connect(self.save_draft)
        right.addWidget(QLabel("Kai's Mail")); right.addWidget(self.to); right.addWidget(self.subject); right.addWidget(self.body,1); right.addWidget(send)
        l.addWidget(self.folders); l.addLayout(right,1); self.setCentralWidget(w)
    def save_draft(self):
        data = {"to": self.to.text(), "subject": self.subject.text(), "body": self.body.toPlainText()}
        name = safe_filename(self.subject.text(), "Draft") + ".json"
        (DOCUMENTS / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
        info(self, "Draft saved in KaiOS Documents. This demo is not connected to an email account.")

class CalculatorWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's Calculator", (350, 450))
        w=QWidget(); l=QVBoxLayout(w); self.display=QLineEdit(); self.display.setAlignment(Qt.AlignRight); self.display.setReadOnly(True); self.display.setText("0"); l.addWidget(self.display)
        grid=QGridLayout(); keys=["7","8","9","/","4","5","6","*","1","2","3","-","0",".","=","+","C"]
        for i,k in enumerate(keys):
            b=QPushButton(k); b.clicked.connect(lambda checked=False, key=k:self.press(key)); grid.addWidget(b,i//4,i%4)
        l.addLayout(grid); self.setCentralWidget(w)
    def press(self,k):
        if k=="C": self.display.setText("0")
        elif k=="=":
            try:
                expr=self.display.text()
                if not all(c in "0123456789.+-*/() " for c in expr): raise ValueError()
                self.display.setText(str(eval(expr, {"__builtins__":{}}, {})))
            except Exception: self.display.setText("Error")
        else: self.display.setText("" if self.display.text()=="0" else self.display.text()+k)

class CalendarWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's Calendar")
        w=QWidget(); l=QVBoxLayout(w); l.addWidget(QLabel("Kai's Calendar")); l.addWidget(QCalendarWidget()); self.setCentralWidget(w)

class StoreWindow(AppWindow):
    APP_NAMES = [
        "Kai's Photos", "Kai's Word", "Kai's Excel", "Kai's PowerPoint",
        "Kai's Outlook", "Kai's Calculator", "Kai's Calendar",
        "Kai's File Manager", "Kai's Settings", "Kai's ChatGPT"
    ]
    PROTECTED_APPS = {"Kai's Browser", "KaiOS Store", "Kai's Settings", "Kai's File Manager"}

    def __init__(self, installed_apps, on_change):
        super().__init__("KaiOS Store", (720, 570))
        self.installed_apps = installed_apps
        self.on_change = on_change
        w = QWidget()
        l = QVBoxLayout(w)
        heading = QLabel("KaiOS Store")
        heading.setStyleSheet("font-size:25px;font-weight:bold")
        l.addWidget(heading)
        l.addWidget(QLabel("Choose apps to install or uninstall. Browser and Store are core KaiOS apps."))
        self.list = QListWidget()
        l.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.install_button = QPushButton("Install")
        self.remove_button = QPushButton("Uninstall")
        self.install_button.clicked.connect(self.install_selected)
        self.remove_button.clicked.connect(self.uninstall_selected)
        row.addWidget(self.install_button)
        row.addWidget(self.remove_button)
        l.addLayout(row)
        self.setCentralWidget(w)
        self.refresh_list()

    def refresh_list(self):
        self.list.clear()
        for name in self.APP_NAMES:
            state = "Installed" if name in self.installed_apps else "Not installed"
            self.list.addItem(QListWidgetItem(f"{name}  —  {state}"))

    def selected_name(self):
        item = self.list.currentItem()
        if not item:
            return None
        return item.text().split("  —  ")[0]

    def install_selected(self):
        name = self.selected_name()
        if not name:
            return
        if name not in self.installed_apps:
            self.installed_apps.add(name)
            self.on_change()
        self.refresh_list()
        info(self, name + " installed successfully.")

    def uninstall_selected(self):
        name = self.selected_name()
        if not name:
            return
        if name in self.PROTECTED_APPS:
            info(self, 'That app is a core KaiOS app and cannot be uninstalled.')
            return
        if name in self.installed_apps:
            self.installed_apps.remove(name)
            self.on_change()
        self.refresh_list()
        info(self, name + " uninstalled.")

class FileManagerWindow(AppWindow):
    def __init__(self):
        super().__init__("Kai's File Manager")
        w=QWidget(); l=QVBoxLayout(w); self.path=QComboBox(); self.path.addItems([str(DESKTOP),str(DOCUMENTS),str(PHOTOS),str(DOWNLOADS)])
        self.list=QListWidget(); refresh=QPushButton("Refresh"); openb=QPushButton("Open selected")
        refresh.clicked.connect(self.refresh); openb.clicked.connect(self.open_selected)
        l.addWidget(self.path); l.addWidget(self.list,1); l.addWidget(refresh); l.addWidget(openb); self.setCentralWidget(w); self.refresh()
    def refresh(self):
        self.list.clear()
        for p in Path(self.path.currentText()).iterdir(): self.list.addItem(p.name)
    def open_selected(self):
        item=self.list.currentItem()
        if item:
            p=Path(self.path.currentText())/item.text()
            if sys.platform=="win32": os.startfile(str(p))
            else: error(self,"Use your system file manager to open: "+str(p))

class NotepadWindow(WordWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kai's Notepad")
        self.editor.setAcceptRichText(False)

class SettingsWindow(AppWindow):
    def __init__(self):
        super().__init__("KaiOS Settings")
        w=QWidget(); l=QVBoxLayout(w)
        l.addWidget(QLabel("KaiOS 11 Settings"))
        l.addWidget(QLabel("Your KaiOS files are stored in: "+str(ROOT)))
        l.addWidget(QLabel("Wallpaper: Red and blue gradient by default"))
        l.addWidget(QLabel("Browser: local Python search server on 127.0.0.1:8765"))
        self.setCentralWidget(w)

class KaiWallpaperHost(QWidget):
    """Full-size wallpaper surface including the space around desktop content.

    Painting at this level prevents the dark window-background gutters that
    were visible around all wallpapers on some Windows/Qt configurations.
    """
    def __init__(self):
        super().__init__()
        self.setObjectName('kaiosWallpaperSurface')
        self.wallpaper_pixmap = QPixmap()

    def change_background(self, path):
        self.wallpaper_pixmap = QPixmap(str(path)) if path else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.wallpaper_pixmap.isNull():
            painter.fillRect(self.rect(), QColor('#11152f'))
        else:
            painter.drawPixmap(self.rect(), self.wallpaper_pixmap, self.wallpaper_pixmap.rect())


class KaiOS(QMainWindow):
    APP_REGISTRY = {
        "Kai's Browser": ("WEB", None),
        "KaiOS Store": ("STORE", None),
        "Kai's Photos": ("PIC", PhotosWindow),
        "Kai's Word": ("W", WordWindow),
        "Kai's Excel": ("X", ExcelWindow),
        "Kai's PowerPoint": ("P", PowerPointWindow),
        "Kai's Outlook": ("MAIL", MailWindow),
        "Kai's Calculator": ("CALC", CalculatorWindow),
        "Kai's Calendar": ("DATE", CalendarWindow),
        "Kai's File Manager": ("FILES", FileManagerWindow),
        "Kai's Settings": ("SET", SettingsWindow),
        "Kai's ChatGPT": ("AI", None),
    }
    CORE_APPS = {"Kai's Browser", "KaiOS Store", "Kai's Settings", "Kai's File Manager"}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("KaiOS 11")
        self.setWindowIcon(kaios_icon())
        self.resize(1280, 800)
        self.open_windows = []
        self.settings_file = ROOT / "installed_apps.json"
        self.installed_apps = self.load_installed_apps()

        central = KaiWallpaperHost()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.desktop = KaiDesktopArea(self)
        self.grid = QGridLayout(self.desktop)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid.setSpacing(14)
        root.addWidget(self.desktop, 1)

        taskbar = QWidget()
        taskbar.setObjectName('kaiosTaskbar')
        taskbar.setStyleSheet('QWidget#kaiosTaskbar { background: #10162e; border: 0; }')
        task = QHBoxLayout(taskbar)
        task.setContentsMargins(16, 8, 16, 10)
        task.setSpacing(10)
        start = QPushButton("Start")
        start.setIcon(kaios_icon())
        start.setIconSize(QSize(31, 31))
        start.setToolTip('Open KaiOS Start menu')
        start.setStyleSheet("background:#5145d8;font-size:17px;font-weight:bold;padding:9px 16px")
        start.clicked.connect(self.start_menu)
        search = QLineEdit()
        search.setPlaceholderText("Search installed KaiOS apps...")
        search.returnPressed.connect(lambda: self.launch_named(search.text()))
        task.addWidget(start)
        task.addWidget(search, 1)
        self.clock = QLabel()
        task.addWidget(self.clock)
        root.addWidget(taskbar)
        self.setCentralWidget(central)

        self.setStyleSheet(f"""
            QMainWindow,QWidget {{ background:{BG}; color:{TEXT}; }}
            QPushButton {{ background:{PANEL}; color:{TEXT}; border:1px solid #46508a;
                border-radius:12px; padding:10px; }}
            QPushButton:hover {{ background:#39427e; }}
            QLineEdit {{ background:#f7f7ff; color:#17182c; padding:10px; border-radius:8px; }}
        """)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_clock)
        self.timer.start(1000)
        self.update_clock()
        self.refresh_desktop()
        self.set_wallpaper()

    def load_installed_apps(self):
        # Fresh installs contain only the two core apps. Existing old
        # installations are reset once to the requested clean desktop.
        if self.settings_file.exists():
            try:
                saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
                apps = set(saved.get("installed", []))
                if "ChatGPT" in apps:
                    apps.discard("ChatGPT")
                    apps.add("Kai's ChatGPT")
                apps.update(self.CORE_APPS)
                return apps
            except Exception:
                pass
        apps = set(self.CORE_APPS)
        self.save_installed_apps(apps)
        return apps

    def save_installed_apps(self, apps=None):
        current = apps if apps is not None else self.installed_apps
        self.settings_file.write_text(
            json.dumps({"installed": sorted(current)}, indent=2),
            encoding="utf-8"
        )

    def set_wallpaper(self):
        if WALLPAPER.exists():
            self.desktop.setStyleSheet(
                f"border-image: url('{WALLPAPER.as_posix()}') 0 0 0 0 stretch stretch;"
            )
        else:
            self.desktop.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #11152f,stop:0.5 #33266f,stop:1 #087fba);")

    def update_clock(self):
        from datetime import datetime
        self.clock.setText(datetime.now().strftime("%a %d %b   %H:%M"))

    def refresh_desktop(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        visible = ["Kai's Browser", "KaiOS Store", "Kai's Settings", "Kai's File Manager"]
        visible.extend(sorted(self.installed_apps - self.CORE_APPS))
        for i, name in enumerate(visible):
            button = QToolButton()
            button.setText(name)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setMinimumSize(145, 76)
            button.setStyleSheet("background:rgba(30,35,83,220);color:white;font-size:13px;font-weight:bold;border:1px solid #7772d8;border-radius:15px")
            button.clicked.connect(lambda checked=False, n=name: self.launch_named(n))
            self.grid.addWidget(button, i // 6, i % 6)

    def launch_named(self, name):
        match = next((app for app in self.APP_REGISTRY if name.lower() in app.lower()), None)
        if not match:
            error(self, "No installed KaiOS app matches that name.")
            return
        if match not in self.installed_apps:
            info(self, match + " is not installed. Open KaiOS Store to install it.")
            return
        if match == "Kai's Browser":
            win = BrowserWindow()
        elif match == "KaiOS Store":
            win = StoreWindow(self.installed_apps, self.apps_changed)
        elif match == "Kai's ChatGPT":
            win = BrowserWindow("https://chatgpt.com/")
        else:
            cls = self.APP_REGISTRY[match][1]
            win = cls()
        self.open_windows.append(win)
        win.show()
        win.raise_()
        win.activateWindow()

    def apps_changed(self):
        self.save_installed_apps()
        self.refresh_desktop()

    def start_menu(self):
        menu = QMenu(self)
        for name in ["Kai's Browser", "KaiOS Store", "Kai's Settings", "Kai's File Manager"] + sorted(self.installed_apps - self.CORE_APPS):
            menu.addAction(name, lambda n=name: self.launch_named(n))
        menu.addSeparator()
        menu.addAction("Open KaiOS folder", lambda: os.startfile(str(ROOT)) if sys.platform == "win32" else None)
        menu.addAction("Shut down KaiOS", self.close)
        menu.exec(self.cursor().pos())

    def closeEvent(self, event):
        event.accept()

# ================================================================
# Additional functionality for the KaiOS desktop simulator
# Keeps existing browser, Store, Word, Excel, PowerPoint, and tools.
# ================================================================
import shutil, subprocess, uuid, math, random, time, ssl, imaplib, smtplib
import email as email_module
from email import policy as email_policy
from email.message import EmailMessage
from datetime import datetime
from PySide6.QtCore import (QPoint, QRect, QSize, QFileInfo, QFileSystemWatcher)
from PySide6.QtGui import (QColor, QPainter, QPen, QBrush, QIcon, QFontMetrics,
                           QDesktopServices, QImage)
from PySide6.QtWidgets import (QFrame, QSlider, QCheckBox, QFileIconProvider,
                                QProgressBar, QColorDialog)
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont, ImageStat

ASSETS = Path(__file__).resolve().parent / 'wallpapers'
CUSTOM_WALLPAPERS = ROOT / 'Wallpapers'
MAIL_DIR = ROOT / 'Mail'
for folder in (CUSTOM_WALLPAPERS, MAIL_DIR):
    folder.mkdir(exist_ok=True, parents=True)
APPEARANCE_FILE = ROOT / 'appearance_settings.json'
SCORE_FILE = ROOT / 'game_scores.json'
DESKTOP_LAYOUT_FILE = ROOT / 'desktop_layout.json'
IMAGE_FILTER = 'Pictures (*.jpg *.jpeg *.png *.webp *.bmp *.gif)'
DEFAULT_WALLPAPER = ASSETS / 'KaiOS_Red_Blue_Default.jpg'


def activate_account(account):
    """Route files and preferences to the signed-in user's private KaiOS folder.

    This is local app-level separation, not OS filesystem encryption.
    """
    global ROOT, DESKTOP, DOCUMENTS, PHOTOS, DOWNLOADS
    global CUSTOM_WALLPAPERS, MAIL_DIR, APPEARANCE_FILE, SCORE_FILE, DESKTOP_LAYOUT_FILE
    ROOT = profile_directory(account)
    DESKTOP = ROOT / 'Desktop'
    DOCUMENTS = ROOT / 'Documents'
    PHOTOS = ROOT / 'Photos'
    DOWNLOADS = ROOT / 'Downloads'
    CUSTOM_WALLPAPERS = ROOT / 'Wallpapers'
    MAIL_DIR = ROOT / 'Mail'
    APPEARANCE_FILE = ROOT / 'appearance_settings.json'
    SCORE_FILE = ROOT / 'game_scores.json'
    DESKTOP_LAYOUT_FILE = ROOT / 'desktop_layout.json'
    for folder in (ROOT, DESKTOP, DOCUMENTS, PHOTOS, DOWNLOADS, CUSTOM_WALLPAPERS, MAIL_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def read_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        pass
    return default


def save_json(path, value):
    # Atomic replacement minimizes the possibility of corrupted user settings.
    target = Path(str(path) + '.tmp')
    target.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    target.replace(path)


def unique_file(path):
    path = Path(path)
    candidate = path
    n = 2
    while candidate.exists():
        candidate = path.with_name(f'{path.stem} ({n}){path.suffix}')
        n += 1
    return candidate


def open_system(path):
    try:
        if sys.platform == 'win32':
            os.startfile(str(path))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    except Exception as exc:
        error(None, 'Unable to open ' + str(path) + ': ' + str(exc))


def icon_for_file(path):
    return QFileIconProvider().icon(QFileInfo(str(path)))


def is_retired_moonlit_wallpaper(path):
    """Hide the old moonlit purple mountain lake wallpapers without deleting files."""
    if not path:
        return False
    stem = Path(path).stem.casefold().replace('_', ' ').replace('-', ' ')
    return ('moonlit purple mountain lake' in stem or
            stem.startswith('moonlit landscapes') or
            stem.startswith('moonlit purple') or
            'moonlit' in stem or
            stem == 'kaios wallpaper')


def selected_wallpaper(data):
    """Find saved wallpaper even if a newer KaiOS ZIP moved its location."""
    value = data.get('wallpaper', '')
    current = Path(value) if value else None
    if current and not is_retired_moonlit_wallpaper(current):
        if current.is_file():
            return current
        # A user's preference may point into a prior extracted KaiOS version.
        # Rebase safely by filename; never delete the original user picture.
        for folder in (CUSTOM_WALLPAPERS, ASSETS):
            alternate = folder / current.name
            if alternate.is_file():
                return alternate
    if DEFAULT_WALLPAPER.is_file():
        return DEFAULT_WALLPAPER
    return next(iter(built_in_wallpapers()), None)


def built_in_wallpapers():
    """Built-in wallpaper list: default gradients + classic neon + one from each other style."""
    selected = [DEFAULT_WALLPAPER] if DEFAULT_WALLPAPER.is_file() else []
    if not ASSETS.is_dir():
        return selected
    smooth_gradients = (
        'KaiOS_Blue_Green_Default', 'KaiOS_Purple_Orange_Default', 'KaiOS_Pink_Cyan_Default',
        'KaiOS_Green_Purple_Default', 'KaiOS_Gold_Blue_Default'
    )
    for stem in smooth_gradients:
        p = ASSETS / (stem + '.jpg')
        if p.is_file():
            selected.append(p)
    classic_neon = (
        'Neon_Pulse_Classic', 'Neon_Orbits_Classic', 'Neon_Halos_Classic',
        'Neon_Sunset_Classic', 'Neon_Purple_Green_Classic', 'Neon_Magenta_Cyan_Classic',
        'Neon_Aqua_Pink_Classic', 'Neon_Lime_Blue_Classic'
    )
    for stem in classic_neon:
        p = ASSETS / (stem + '.jpg')
        if p.is_file():
            selected.append(p)
    groups = ('Futuristic_Cities', 'Floating_Islands', 'Magical_Forests',
              'Northern_Lights', 'Space_and_Planets')
    for key in groups:
        matches = sorted(ASSETS.glob(key + '_*.jpg'))
        if matches:
            selected.append(matches[0])
    return selected


def average_luminance(path):
    try:
        with Image.open(path) as im:
            im = im.convert('RGB')
            im.thumbnail((64, 64))
            stat = ImageStat.Stat(im)
            r, g, b = stat.mean[:3]
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
    except Exception:
        return 110


def make_wallpaper_text(path):
    path = Path(path) if path else None
    if path and path.name.startswith(('Moonlit_', 'Futuristic_', 'Floating_', 'Northern_', 'Magical_', 'Space_', 'Neon_', 'KaiOS_')):
        return '#ffe4a8'
    if path and path.name in ('kaios_wallpaper.png', 'KaiOS_Red_Blue_Default.jpg'):
        return '#ffe4a8'
    if path and average_luminance(path) >= 150:
        return '#101324'
    return '#fff1cf'


class DesktopFileTile(QFrame):
    """A file in the *KaiOS* Desktop folder, not the Windows desktop."""
    def __init__(self, desktop, path, x, y):
        super().__init__(desktop)
        self.desktop = desktop
        self.path = path
        self.setGeometry(x, y, 114, 103)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet('QFrame {background:rgba(15,20,50,125);border:1px solid #a797d9;'
                           'border-radius:10px;} QLabel {border:0;background:transparent;color:#fff8de;font-weight:bold;}')
        ly = QVBoxLayout(self)
        ly.setContentsMargins(5,7,5,4)
        img = QLabel()
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setPixmap(icon_for_file(path).pixmap(QSize(37, 37)))
        label = QLabel(path.name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setToolTip(str(path))
        ly.addWidget(img)
        ly.addWidget(label,1)
        self.anchor = None
        self.moved = False
        self.show()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            open_system(self.path)
        super().mouseDoubleClickEvent(e)

    def mousePressEvent(self,e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.anchor = e.position().toPoint()
            self.moved = False
            self.raise_()
        super().mousePressEvent(e)

    def mouseMoveEvent(self,e):
        if (self.anchor is not None and
                e.buttons() & Qt.MouseButton.LeftButton):
            pos = self.mapToParent(e.position().toPoint()) - self.anchor
            pos.setX(max(0,min(pos.x(),self.desktop.width()-self.width())))
            pos.setY(max(0,min(pos.y(),self.desktop.height()-self.height())))
            self.move(pos)
            self.moved = True
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self,e):
        if self.moved:
            if self.desktop.drop_tile_on_trash(self):
                self.anchor=None
                super().mouseReleaseEvent(e)
                return
            self.desktop.save_tile_locations()
        self.anchor=None
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self,e):
        menu = QMenu(self)
        a_open = menu.addAction('Open')
        a_rename = menu.addAction('Rename')
        a_delete = menu.addAction('Delete')
        a_move = menu.addAction('Move to KaiOS Documents')
        picked=menu.exec(e.globalPos())
        if picked == a_open:
            open_system(self.path)
        elif picked == a_rename:
            name, ok = QInputDialog.getText(self,'Rename desktop file','New file name:',text=self.path.name)
            if ok and name.strip():
                if '/' in name or '\\' in name or name in ('.','..'):
                    error(self,'Please enter a valid file name.'); return
                dest = DESKTOP / safe_filename(name)
                if dest != self.path and dest.exists():
                    error(self,'A file with that name already exists.'); return
                try:
                    self.path.rename(dest); self.desktop.reload_files()
                except Exception as exc:
                    error(self,str(exc))
        elif picked == a_delete:
            if QMessageBox.question(self,'Delete desktop file',f'Delete {self.path.name}?') == QMessageBox.StandardButton.Yes:
                try:
                    if self.path.is_dir(): shutil.rmtree(self.path)
                    else: self.path.unlink()
                    self.desktop.reload_files()
                except Exception as exc: error(self,str(exc))
        elif picked == a_move:
            try:
                shutil.move(str(self.path),str(unique_file(DOCUMENTS/self.path.name)))
                self.desktop.reload_files()
            except Exception as exc: error(self,str(exc))


class KaiDesktopArea(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.bg = QPixmap()
        self.tiles = []
        self.trash_rect = QRect()
        self.locations = read_json(DESKTOP_LAYOUT_FILE,{})
        self.watcher = QFileSystemWatcher([str(DESKTOP)],self)
        self.watcher.directoryChanged.connect(lambda *_: QTimer.singleShot(250,self.reload_files))
        QTimer.singleShot(150,self.reload_files)

    def change_background(self,path):
        self.bg = QPixmap(str(path)) if path else QPixmap()
        self.update()

    def paintEvent(self,e):
        painter=QPainter(self)
        # Paint the EXACT SAME full-size wallpaper section as the parent.
        # This avoids seams when a platform/style inserts a nonzero inset.
        surface=self.parentWidget()
        if isinstance(surface, KaiWallpaperHost) and not surface.wallpaper_pixmap.isNull():
            origin=self.mapTo(surface,QPoint(0,0))
            full_rect=QRect(-origin.x(),-origin.y(),surface.width(),surface.height())
            painter.drawPixmap(full_rect,surface.wallpaper_pixmap,surface.wallpaper_pixmap.rect())
        elif not self.bg.isNull():
            painter.drawPixmap(self.rect(),self.bg,self.bg.rect())
        else:
            painter.fillRect(self.rect(),QColor('#242451'))
        self.trash_rect = QRect(18, max(18, self.height()-118), 118, 96)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor('#d8c58d'), 2))
        painter.setBrush(QColor(19, 24, 52, 170))
        painter.drawRoundedRect(self.trash_rect, 16, 16)
        painter.setBrush(QColor('#d46a8e'))
        body = QRect(self.trash_rect.x()+38, self.trash_rect.y()+30, 42, 40)
        painter.drawRoundedRect(body, 6, 6)
        painter.drawRect(self.trash_rect.x()+34, self.trash_rect.y()+24, 50, 8)
        painter.drawRect(self.trash_rect.x()+49, self.trash_rect.y()+18, 20, 6)
        painter.setPen(QColor('#fff1cf'))
        font = painter.font(); font.setPointSize(11); font.setBold(True); painter.setFont(font)
        painter.drawText(QRect(self.trash_rect.x(), self.trash_rect.y()+72, self.trash_rect.width(), 20), Qt.AlignmentFlag.AlignCenter, 'Bin')

    def resizeEvent(self,e):
        super().resizeEvent(e)
        self.position_unplaced_tiles()

    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dragMoveEvent(self,e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self,e):
        paths=[Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        copied=0
        for src in paths:
            if not src.exists(): continue
            try:
                if src.parent.resolve() == DESKTOP.resolve(): continue
                dest=unique_file(DESKTOP/src.name)
                if src.is_dir(): shutil.copytree(src,dest)
                else: shutil.copy2(src,dest)
                copied += 1
            except Exception as exc:
                error(self,f'Could not add {src.name}: {exc}')
        if copied:
            self.reload_files()
        e.acceptProposedAction()

    def is_over_trash(self, rect):
        return not self.trash_rect.isNull() and self.trash_rect.intersects(rect)

    def drop_tile_on_trash(self, tile):
        if not self.is_over_trash(tile.geometry()):
            return False
        if QMessageBox.question(self, 'Remove desktop file', f'Move {tile.path.name} to the bin and delete it?') != QMessageBox.StandardButton.Yes:
            tile.move(*self.locations.get(tile.path.name, [tile.x(), tile.y()]))
            return False
        try:
            if tile.path.is_dir():
                shutil.rmtree(tile.path)
            else:
                tile.path.unlink()
            self.reload_files()
            return True
        except Exception as exc:
            error(self, str(exc))
            return False


    def save_tile_locations(self):
        for tile in self.tiles:
            self.locations[tile.path.name] = [tile.x(),tile.y()]
        save_json(DESKTOP_LAYOUT_FILE,self.locations)

    def position_unplaced_tiles(self):
        for i,tile in enumerate(self.tiles):
            if tile.path.name not in self.locations and self.width()>250:
                col=i//max(1,(self.height()-25)//113)
                row=i%max(1,(self.height()-25)//113)
                tile.move(max(0,self.width()-130-col*121),18+row*113)

    def reload_files(self):
        if not self.isVisible() and self.width()<=0: return
        # Avoid work when the contents have not changed.
        files=sorted(DESKTOP.iterdir(),key=lambda p:p.name.lower())
        names=[str(p) for p in files]
        if names == [str(t.path) for t in self.tiles]: return
        for tile in self.tiles: tile.deleteLater()
        self.tiles=[]
        for i,path in enumerate(files):
            loc=self.locations.get(path.name)
            if not isinstance(loc,list) or len(loc)<2:
                col=i//max(1,(self.height()-25)//113)
                row=i%max(1,(self.height()-25)//113)
                loc=[max(0,self.width()-130-col*121),18+row*113]
            tile=DesktopFileTile(self,path,int(loc[0]),int(loc[1]))
            self.tiles.append(tile)
        self.position_unplaced_tiles()


def kaios_photo_font(size):
    """Choose an available Pillow font for movable photo captions."""
    fonts = [
        'C:/Windows/Fonts/arialbd.ttf',
        'C:/Windows/Fonts/arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for path in fonts:
        try:
            return ImageFont.truetype(path, max(8, int(size)))
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


class PhotoDrawingCanvas(QLabel):
    """Color drawing and directly draggable text overlays. No crop tool."""
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.mode = 'draw'
        self.points = []
        self.dragging_text = None
        self.drag_origin = None
        self.drag_snapshot_taken = False
        self.setMinimumSize(500, 340)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet('background:#15192c;border:1px solid #7678ba;')
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def image_rect(self):
        im = self.owner.image
        if im is None:
            return QRect()
        scale = min(self.width() / im.width, self.height() / im.height)
        sw = max(1, int(im.width * scale))
        sh = max(1, int(im.height * scale))
        return QRect((self.width() - sw)//2, (self.height() - sh)//2, sw, sh)

    def to_image(self, point):
        rect = self.image_rect()
        im = self.owner.image
        if rect.isEmpty() or im is None:
            return (0, 0)
        x = int((point.x() - rect.x()) * im.width / rect.width())
        y = int((point.y() - rect.y()) * im.height / rect.height())
        return (max(0, min(im.width-1, x)), max(0, min(im.height-1, y)))

    def text_font(self, layer):
        rect = self.image_rect()
        im = self.owner.image
        font = QFont('Arial')
        font.setBold(True)
        if im is not None and not rect.isEmpty():
            font.setPixelSize(max(9, round(layer['size'] * rect.width() / im.width)))
        else:
            font.setPixelSize(max(9, layer['size']))
        return font

    def text_rect(self, layer):
        rect = self.image_rect()
        im = self.owner.image
        if im is None or rect.isEmpty():
            return QRect()
        scale = rect.width() / im.width
        font = self.text_font(layer)
        metrics = QFontMetrics(font)
        x = rect.x() + int(layer['x'] * scale)
        y = rect.y() + int(layer['y'] * scale)
        return QRect(x - 4, y - 4, max(12, metrics.horizontalAdvance(layer['text']) + 8),
                     max(12, metrics.height() + 8))

    def text_at(self, point):
        for idx in range(len(self.owner.text_layers)-1, -1, -1):
            if self.text_rect(self.owner.text_layers[idx]).contains(point):
                return idx
        return None

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.owner.image is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for idx, layer in enumerate(self.owner.text_layers):
            box = self.text_rect(layer)
            font = self.text_font(layer)
            painter.setFont(font)
            fm = QFontMetrics(font)
            baseline = box.y() + 4 + fm.ascent()
            x = box.x() + 4
            painter.setPen(QColor('#101323'))
            painter.drawText(x + 1, baseline + 1, layer['text'])
            painter.setPen(QColor(layer['color']))
            painter.drawText(x, baseline, layer['text'])
            if idx == self.owner.selected_text and self.mode == 'text':
                painter.setPen(QPen(QColor('#ffe7a1'), 2, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(box.adjusted(-2,-2,2,2), 4, 4)
        if self.mode == 'draw' and self.points:
            rect = self.image_rect()
            im = self.owner.image
            width = max(2, round(self.owner.brush_width * rect.width() / im.width))
            pen = QPen(QColor(self.owner.current_color), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            if len(self.points) == 1:
                painter.drawPoint(self.points[0])
            for a, b in zip(self.points, self.points[1:]):
                painter.drawLine(a, b)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.owner.image is None:
            return
        pos = event.position().toPoint()
        if self.mode == 'text':
            idx = self.text_at(pos)
            self.owner.selected_text = idx
            self.dragging_text = idx
            self.drag_snapshot_taken = False
            if idx is not None:
                layer = self.owner.text_layers[idx]
                self.drag_origin = (self.to_image(pos), layer['x'], layer['y'])
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            return
        if self.image_rect().contains(pos):
            self.points = [pos]
            self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if (self.mode == 'text' and self.dragging_text is not None and
                self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton):
            idx = self.dragging_text
            previous_point, x0, y0 = self.drag_origin
            x, y = self.to_image(pos)
            new_x = max(0, min(self.owner.image.width-1, x0 + x - previous_point[0]))
            new_y = max(0, min(self.owner.image.height-1, y0 + y - previous_point[1]))
            layer = self.owner.text_layers[idx]
            if new_x != layer['x'] or new_y != layer['y']:
                if not self.drag_snapshot_taken:
                    self.owner.snapshot()
                    self.drag_snapshot_taken = True
                layer['x'] = new_x
                layer['y'] = new_y
                self.update()
        elif self.mode == 'draw' and self.points and event.buttons() & Qt.MouseButton.LeftButton:
            rect = self.image_rect()
            pos.setX(max(rect.x(), min(rect.right(), pos.x())))
            pos.setY(max(rect.y(), min(rect.bottom(), pos.y())))
            self.points.append(pos)
            self.update()
        elif self.mode == 'text':
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.text_at(pos) is not None
                           else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.mode == 'text':
            self.dragging_text = None
            self.drag_origin = None
            self.drag_snapshot_taken = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self.owner.image is None or not self.points:
            return
        coords = [self.to_image(pos) for pos in self.points]
        self.owner.snapshot()
        drawing = ImageDraw.Draw(self.owner.image)
        if len(coords) > 1:
            drawing.line(coords, fill=self.owner.current_color, width=self.owner.brush_width,
                         joint='curve')
        else:
            x, y = coords[0]
            radius = max(1, self.owner.brush_width // 2)
            drawing.ellipse((x-radius,y-radius,x+radius,y+radius),
                            fill=self.owner.current_color)
        self.points = []
        self.owner.refresh_preview()

    def mouseDoubleClickEvent(self, event):
        if self.mode == 'text' and event.button() == Qt.MouseButton.LeftButton:
            idx = self.text_at(event.position().toPoint())
            if idx is not None:
                self.owner.selected_text = idx
                self.owner.edit_selected_text()


class PhotoEditorWindow(AppWindow):
    COLOR_CHOICES = [
        ('White', '#ffffff'), ('Black', '#111111'), ('Red', '#ff4e65'),
        ('Orange', '#ff983c'), ('Yellow', '#ffe16b'), ('Green', '#52e88b'),
        ('Cyan', '#4addf7'), ('Blue', '#5285ff'), ('Purple', '#bc75f6'),
        ('Pink', '#fa78c8'),
    ]

    def __init__(self, path=None):
        super().__init__('KaiOS Photo Editor', (1120, 800))
        self.path = None
        self.image = None
        self.text_layers = []
        self.selected_text = None
        self.editor_stack = []
        self.current_color = '#ffe16b'
        self.brush_width = 8
        main = QWidget()
        layout = QVBoxLayout(main)
        row1 = QHBoxLayout()
        for label, callback in [
            ('Open', self.open_image), ('Rotate left', lambda: self.rotate(90)),
            ('Rotate right', lambda: self.rotate(-90)), ('Undo', self.undo),
            ('Save as copy', self.save_copy),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(callback)
            row1.addWidget(btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        for label, callback in [
            ('Draw', lambda: self.set_mode('draw')),
            ('Move text', lambda: self.set_mode('text')),
            ('Add text', self.add_text),
            ('Edit text', self.edit_selected_text),
            ('Delete text', self.delete_selected_text),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(callback)
            row2.addWidget(btn)
        row2.addWidget(QLabel('Brush size:'))
        self.brush_size_box = QSpinBox()
        self.brush_size_box.setRange(1, 80)
        self.brush_size_box.setValue(self.brush_width)
        self.brush_size_box.valueChanged.connect(self.set_brush_width)
        row2.addWidget(self.brush_size_box)
        layout.addLayout(row2)

        colors_row = QHBoxLayout()
        colors_row.addWidget(QLabel('Drawing color:'))
        for name, code in self.COLOR_CHOICES:
            swatch = QPushButton('')
            swatch.setFixedSize(29, 29)
            swatch.setToolTip(name)
            swatch.setStyleSheet('background:' + code + ';border:2px solid #bbc5ee;border-radius:8px;padding:0;')
            swatch.clicked.connect(lambda checked=False, color=code: self.choose_color(color))
            colors_row.addWidget(swatch)
        custom = QPushButton('More colors...')
        custom.clicked.connect(self.custom_color)
        colors_row.addWidget(custom)
        self.current_swatch = QLabel('Selected: Yellow')
        self.current_swatch.setMinimumWidth(140)
        colors_row.addWidget(self.current_swatch)
        colors_row.addStretch()
        layout.addLayout(colors_row)

        self.canvas = PhotoDrawingCanvas(self)
        layout.addWidget(self.canvas, 1)

        sliders = QHBoxLayout()
        self.sliders = {}
        for name in ('Brightness', 'Contrast', 'Saturation', 'Sharpness'):
            cell = QVBoxLayout()
            cell.addWidget(QLabel(name))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(10, 250)
            slider.setValue(100)
            slider.setToolTip('100 is unchanged. Use Apply adjustments to apply.')
            cell.addWidget(slider)
            sliders.addLayout(cell)
            self.sliders[name] = slider
        apply_button = QPushButton('Apply adjustments')
        apply_button.clicked.connect(self.apply_adjustments)
        sliders.addWidget(apply_button)
        layout.addLayout(sliders)

        filters = QHBoxLayout()
        filters.addWidget(QLabel('Filters:'))
        for name in ['Black and white', 'Vintage', 'Warm', 'Cool', 'Soft glow']:
            button = QPushButton(name)
            button.clicked.connect(lambda checked=False, key=name: self.apply_filter(key))
            filters.addWidget(button)
        layout.addLayout(filters)
        self.status = QLabel('Draw with the selected color. Add text, then drag it in Move text mode. No cropping.')
        layout.addWidget(self.status)
        self.setCentralWidget(main)
        if path is not None:
            self.load_image(path)

    def snapshot(self):
        if self.image is None:
            return
        self.editor_stack.append((self.image.copy(), [dict(layer) for layer in self.text_layers],
                                  self.selected_text))
        self.editor_stack = self.editor_stack[-15:]

    def load_image(self, path):
        try:
            with Image.open(path) as photo:
                self.image = photo.convert('RGB')
            self.path = Path(path)
            self.text_layers = []
            self.selected_text = None
            self.editor_stack = []
            self.set_mode('draw')
            self.refresh_preview()
            self.status.setText('Editing: ' + self.path.name + '  |  Draw colors, move text, save a copy.')
        except Exception as exc:
            error(self, 'Cannot load photo: ' + str(exc))

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open photo', str(PHOTOS), IMAGE_FILTER)
        if path:
            self.load_image(path)

    def refresh_preview(self):
        if self.image is None:
            return
        import io
        buffer = io.BytesIO()
        self.image.save(buffer, 'PNG')
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue(), 'PNG')
        self.canvas.setPixmap(pixmap.scaled(self.canvas.size(),
                                           Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        self.canvas.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.image is not None:
            QTimer.singleShot(60, self.refresh_preview)

    def set_brush_width(self, size):
        self.brush_width = int(size)

    def choose_color(self, color):
        if self.canvas.mode == 'text' and self.selected_text is not None:
            self.snapshot()
            self.text_layers[self.selected_text]['color'] = color
        self.current_color = color
        self.current_swatch.setText('Selected color')
        self.current_swatch.setStyleSheet('color:' + color + ';font-weight:bold;')
        self.canvas.update()

    def custom_color(self):
        chosen = QColorDialog.getColor(QColor(self.current_color), self, 'Choose drawing or text color')
        if chosen.isValid():
            self.choose_color(chosen.name())

    def rotate(self, degrees):
        if self.image is None:
            return
        self.snapshot()
        old_w, old_h = self.image.size
        self.image = self.image.rotate(degrees, expand=True)
        for layer in self.text_layers:
            x, y = layer['x'], layer['y']
            if degrees == 90:
                layer['x'], layer['y'] = y, old_w-x
            else:
                layer['x'], layer['y'] = old_h-y, x
            layer['x'] = max(0, min(self.image.width-1, layer['x']))
            layer['y'] = max(0, min(self.image.height-1, layer['y']))
        self.refresh_preview()

    def set_mode(self, mode):
        if mode not in ('draw', 'text'):
            return
        self.canvas.mode = mode
        self.canvas.points = []
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if mode == 'draw'
                              else Qt.CursorShape.OpenHandCursor)
        if mode == 'text' and self.selected_text is None and self.text_layers:
            self.selected_text = len(self.text_layers)-1
        if hasattr(self, 'status'):
            self.status.setText('Draw directly on your picture in the chosen color.' if mode == 'draw'
                                else 'Drag a caption to move it. Double-click to edit it. Pick a color to recolor it.')
        self.canvas.update()

    def add_text(self):
        if self.image is None:
            info(self, 'Open a photo before adding text.')
            return
        value, accepted = QInputDialog.getText(self, 'Add movable text', 'Write your text:')
        if not accepted or not value.strip():
            return
        self.snapshot()
        layer = {'text':value.strip(), 'x':self.image.width//5, 'y':self.image.height//2,
                 'size':max(22, min(110, self.image.width//25)), 'color':self.current_color}
        self.text_layers.append(layer)
        self.selected_text = len(self.text_layers)-1
        self.set_mode('text')
        self.status.setText('Text added! Drag it to position it before saving your edited photo.')
        self.canvas.update()

    def edit_selected_text(self):
        idx = self.selected_text
        if idx is None or idx >= len(self.text_layers):
            info(self, 'Click a text layer in Move text mode first.')
            return
        old = self.text_layers[idx]['text']
        value, accepted = QInputDialog.getText(self, 'Edit text', 'Text:', text=old)
        if accepted and value.strip() and value != old:
            self.snapshot()
            self.text_layers[idx]['text'] = value.strip()
            self.canvas.update()

    def delete_selected_text(self):
        idx = self.selected_text
        if idx is None or idx >= len(self.text_layers):
            info(self, 'Click a text layer in Move text mode first.')
            return
        self.snapshot()
        self.text_layers.pop(idx)
        self.selected_text = None
        self.canvas.update()

    def apply_adjustments(self):
        if self.image is None:
            return
        self.snapshot()
        photo = self.image
        for name, method in [('Brightness', ImageEnhance.Brightness),
                             ('Contrast', ImageEnhance.Contrast),
                             ('Saturation', ImageEnhance.Color),
                             ('Sharpness', ImageEnhance.Sharpness)]:
            photo = method(photo).enhance(self.sliders[name].value()/100)
            self.sliders[name].setValue(100)
        self.image = photo
        self.refresh_preview()

    def apply_filter(self, name):
        if self.image is None:
            return
        self.snapshot()
        photo = self.image
        if name == 'Black and white':
            photo = ImageOps.grayscale(photo).convert('RGB')
        elif name == 'Vintage':
            photo = ImageOps.colorize(ImageOps.grayscale(photo), '#40302b', '#ead4ac')
        elif name == 'Warm':
            r,g,b = photo.split()
            photo = Image.merge('RGB', (r.point(lambda v:min(255,int(v*1.09))),
                                        g, b.point(lambda v:int(v*.90))))
        elif name == 'Cool':
            r,g,b = photo.split()
            photo = Image.merge('RGB', (r.point(lambda v:int(v*.88)), g,
                                        b.point(lambda v:min(255,int(v*1.12)))))
        elif name == 'Soft glow':
            photo = Image.blend(photo, photo.filter(ImageFilter.GaussianBlur(8)), .24)
        self.image = photo
        self.refresh_preview()

    def render_edited_photo(self):
        """Combine the photo and current text positions only for exporting a copy."""
        if self.image is None:
            return None
        finished = self.image.copy()
        painter = ImageDraw.Draw(finished)
        for layer in self.text_layers:
            font = kaios_photo_font(layer['size'])
            painter.text((int(layer['x']), int(layer['y'])), layer['text'],
                         font=font, fill=layer['color'], anchor='lt',
                         stroke_width=max(1, layer['size']//35),
                         stroke_fill='#12162b')
        return finished

    def undo(self):
        if not self.editor_stack:
            info(self, 'No more edits to undo.')
            return
        self.image, self.text_layers, self.selected_text = self.editor_stack.pop()
        self.refresh_preview()

    def save_copy(self):
        if self.image is None:
            return
        stem = safe_filename(self.path.stem if self.path else 'Photo')
        suggested = unique_file(PHOTOS / (stem+'_edited.png'))
        path, _ = QFileDialog.getSaveFileName(self, 'Save edited copy', str(suggested),
                                              'PNG (*.png);;JPEG (*.jpg)')
        if not path:
            return
        try:
            dest = Path(path)
            if self.path and dest.resolve() == self.path.resolve():
                error(self, 'Save as a new file so the original photo is not overwritten.')
                return
            result = self.render_edited_photo()
            result.save(dest)
            info(self, 'Saved edited copy: ' + str(dest))
        except Exception as exc:
            error(self, str(exc))


class EnhancedPhotosWindow(PhotosWindow):
    def __init__(self):
        super().__init__()
        self.current_path=None
        self.editors=[]
        bar=self.centralWidget().layout().itemAt(1).layout()
        b=QPushButton('Edit photo')
        b.clicked.connect(self.edit_current)
        bar.addWidget(b)

    def open_photo(self):
        p,_=QFileDialog.getOpenFileName(self,'Open photo',str(PHOTOS),IMAGE_FILTER)
        if p:
            pix=QPixmap(p)
            if pix.isNull():error(self,'Could not open that picture.');return
            self.current_path=Path(p)
            self.label.setPixmap(pix.scaled(self.label.size(),Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    def edit_current(self):
        if self.current_path is None:
            self.open_photo()
        if self.current_path:
            win=PhotoEditorWindow(self.current_path)
            self.editors.append(win)
            win.show()


class EnhancedSettingsWindow(AppWindow):
    def __init__(self,host):
        super().__init__('KaiOS 11 Settings',(980,760))
        self.host=host
        self.thumbnails=[]
        w=QWidget();l=QVBoxLayout(w)
        h=QLabel('KaiOS 11 — Appearance & Performance')
        h.setStyleSheet('font-size:22px;font-weight:bold;')
        l.addWidget(h)
        l.addWidget(QLabel('Built-in wallpapers, including Classic Neon, are protected. Personal wallpaper copies can be deleted.'))
        row=QHBoxLayout()
        row.addWidget(QLabel('Performance:'))
        self.mode=QComboBox();self.mode.addItems(['Fast Mode','Balanced Mode','Beautiful Mode'])
        self.mode.setCurrentText(host.appearance.get('performance','Balanced Mode'))
        self.mode.currentTextChanged.connect(self.change_performance)
        row.addWidget(self.mode)
        row.addStretch()
        add=QPushButton('Add my picture');add.clicked.connect(self.add_picture)
        delete=QPushButton('Delete selected personal wallpaper');delete.clicked.connect(self.delete_picture)
        row.addWidget(add);row.addWidget(delete)
        l.addLayout(row)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        gallery=QWidget();self.grid=QGridLayout(gallery);self.grid.setSpacing(12)
        scroll.setWidget(gallery);l.addWidget(scroll,1)
        self.selected=None
        self.note=QLabel('Selected wallpaper is applied immediately.')
        l.addWidget(self.note)
        self.setCentralWidget(w)
        self.reload_gallery()

    def all_wallpapers(self):
        builtins=built_in_wallpapers()
        custom=sorted(p for p in CUSTOM_WALLPAPERS.iterdir()
                      if p.suffix.lower() in ('.jpg','.jpeg','.png','.webp','.bmp')
                      and not is_retired_moonlit_wallpaper(p))
        return builtins+custom

    def reload_gallery(self):
        while self.grid.count():
            item=self.grid.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.thumbnails=[]
        for i,p in enumerate(self.all_wallpapers()):
            card=QFrame()
            card.setStyleSheet('QFrame{background:#151b37;border:1px solid #9c83d2;border-radius:12px;}')
            box=QVBoxLayout(card)
            box.setContentsMargins(8,8,8,8)
            b=QPushButton()
            b.setMinimumSize(184,112)
            b.setIcon(QIcon(str(p)))
            b.setIconSize(QSize(167,94))
            b.setToolTip(str(p))
            b.setStyleSheet('background:#20274a;border:1px solid #8a79c6;border-radius:10px;')
            b.clicked.connect(lambda _,wall=p:self.select_picture(wall))
            label_text=p.stem.replace('_',' ')
            caption=QLabel(label_text)
            caption.setWordWrap(True)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setStyleSheet('color:#fff2c8;font-size:14px;font-weight:bold;padding:4px 2px;')
            box.addWidget(b)
            box.addWidget(caption)
            self.grid.addWidget(card,i//4,i%4)
            self.thumbnails.append(card)

    def select_picture(self,path):
        self.selected=Path(path)
        self.host.appearance['wallpaper']=str(path)
        self.host.save_appearance()
        self.host.set_wallpaper()
        self.note.setText('Applied: '+path.stem.replace('_',' '))

    def add_picture(self):
        p,_=QFileDialog.getOpenFileName(self,'Add custom wallpaper',str(PHOTOS),IMAGE_FILTER)
        if p:
            try:
                target=unique_file(CUSTOM_WALLPAPERS/Path(p).name)
                shutil.copy2(p,target)
                self.reload_gallery();self.select_picture(target)
            except Exception as exc:error(self,str(exc))

    def delete_picture(self):
        target=self.selected
        if target is None or target.parent.resolve()!=CUSTOM_WALLPAPERS.resolve():
            info(self,'Select a wallpaper that you personally added. Built-in wallpapers cannot be deleted.')
            return
        if QMessageBox.question(self,'Delete wallpaper copy',
                f'Delete your added wallpaper copy {target.name}?\nYour Photos original will not be touched.') != QMessageBox.StandardButton.Yes:
            return
        try:
            if selected_wallpaper(self.host.appearance)==target:
                remaining=next(iter(built_in_wallpapers()),None)
                self.host.appearance['wallpaper']=str(remaining) if remaining else ''
            target.unlink()
            self.selected=None
            self.host.save_appearance();self.host.set_wallpaper()
            self.reload_gallery()
        except Exception as exc:error(self,str(exc))

    def change_performance(self,mode):
        self.host.appearance['performance']=mode
        self.host.save_appearance()
        self.host.apply_performance()


class GmailWorker(QThread):
    """Network operations off the UI thread; passwords stay in memory only."""
    result = Signal(dict)

    def __init__(self,task,account,app_password,content=None):
        super().__init__()
        self.task=task
        self.account=account
        self.app_password=app_password
        self.content=content or {}

    def run(self):
        try:
            if self.task in ('connect','fetch'):
                with imaplib.IMAP4_SSL('imap.gmail.com',993,timeout=20) as box:
                    box.login(self.account,self.app_password)
                    if self.task == 'connect':
                        box.logout()
                        self.result.emit({'task':'connect','ok':True})
                        return
                    status,_=box.select('INBOX',readonly=True)
                    if status!='OK':raise RuntimeError('Gmail did not open the inbox.')
                    status,data=box.search(None,'ALL')
                    if status!='OK':raise RuntimeError('Gmail could not list messages.')
                    ids=(data[0] or b'').split()[-25:][::-1]
                    messages=[]
                    for mid in ids:
                        st,items=box.fetch(mid,'(RFC822)')
                        if st!='OK':continue
                        payload=next((part[1] for part in items if isinstance(part,tuple)),None)
                        if not payload:continue
                        mail=email_module.message_from_bytes(payload,policy=email_policy.default)
                        text_body=mail.get_body(preferencelist=('plain',))
                        if text_body:
                            body=text_body.get_content()
                        else:
                            html_body=mail.get_body(preferencelist=('html',))
                            if html_body:
                                parser=TextExtractor();parser.feed(html_body.get_content())
                                body=' '.join(parser.parts)
                            else:
                                body=mail.get_content() if not mail.is_multipart() else ''
                        messages.append({'from':str(mail.get('From','')),
                                         'subject':str(mail.get('Subject','(No subject)')),
                                         'date':str(mail.get('Date','')),
                                         'to':self.account,'body':str(body)[:100000]})
                    box.logout()
                    self.result.emit({'task':'fetch','ok':True,'messages':messages})
            elif self.task=='send':
                msg=EmailMessage()
                msg['From']=self.account
                msg['To']=self.content['to']
                msg['Subject']=self.content['subject']
                msg.set_content(self.content['body'])
                with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=25) as smtp:
                    smtp.login(self.account,self.app_password)
                    smtp.send_message(msg)
                self.result.emit({'task':'send','ok':True,'mail':self.content})
        except Exception as exc:
            self.result.emit({'task':self.task,'ok':False,'error':str(exc)})


class EnhancedMailWindow(AppWindow):
    """Gmail via TLS IMAP/SMTP + Google app password (not regular password).
    Local Drafts and Sent persist; inbox is fetched from Gmail on request.
    """
    def __init__(self):
        super().__init__("Kai's Outlook - Gmail",(1050,660))
        self.account='';self.app_password=''
        self.inbox=[]
        self.drafts=read_json(MAIL_DIR/'drafts.json',[])
        self.sent=read_json(MAIL_DIR/'sent.json',[])
        self.threads=[]
        self.active=False
        w=QWidget();l=QVBoxLayout(w)
        top=QHBoxLayout()
        self.status=QLabel('Not connected. Connect a Gmail account to send and receive mail.')
        connect=QPushButton('Connect Gmail')
        connect.clicked.connect(self.connect_gmail)
        self.refresh_button=QPushButton('Refresh Inbox')
        self.refresh_button.clicked.connect(self.refresh_inbox)
        top.addWidget(connect);top.addWidget(self.refresh_button)
        top.addWidget(self.status,1)
        l.addLayout(top)
        panes=QHBoxLayout()
        self.folders=QListWidget();self.folders.setMaximumWidth(145)
        self.folders.addItems(['Inbox','Drafts','Sent'])
        self.folders.currentRowChanged.connect(self.refresh_messages)
        self.messages=QListWidget();self.messages.setMaximumWidth(310)
        self.messages.itemClicked.connect(self.load_message)
        edit=QVBoxLayout()
        title=QLabel("Kai's Mail")
        title.setStyleSheet('font-size:19px;font-weight:bold')
        self.to=QLineEdit();self.to.setPlaceholderText('To: recipient@gmail.com')
        self.subject=QLineEdit();self.subject.setPlaceholderText('Subject')
        self.body=QTextEdit();self.body.setPlaceholderText('Write an email...')
        controls=QHBoxLayout()
        self.send_button=QPushButton('Send email');self.send_button.clicked.connect(self.send_email)
        draft_button=QPushButton('Save draft');draft_button.clicked.connect(self.save_draft)
        new_button=QPushButton('New');new_button.clicked.connect(self.new_message)
        for b in (self.send_button,draft_button,new_button):controls.addWidget(b)
        edit.addWidget(title);edit.addWidget(self.to);edit.addWidget(self.subject);edit.addWidget(self.body,1);edit.addLayout(controls)
        panes.addWidget(self.folders);panes.addWidget(self.messages);panes.addLayout(edit,1)
        l.addLayout(panes,1)
        note=QLabel('Requires a Gmail app password and two-step verification. Your password is not saved as plain text.')
        l.addWidget(note)
        self.setCentralWidget(w)
        self.folders.setCurrentRow(0)

    def connect_gmail(self):
        account,ok=QInputDialog.getText(self,'Gmail account','Gmail email address:',text=self.account)
        if not ok or not account.strip():return
        account=account.strip()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',account):
            error(self,'Please enter a complete email address.');return
        secret,ok=QInputDialog.getText(self,'Gmail app password',
            'Enter a Google app password (NOT your normal Gmail password):',
            QLineEdit.EchoMode.Password)
        if not ok or not secret.strip():return
        self.account=account
        self.app_password=secret.replace(' ','')
        self.begin_job('connect')

    def begin_job(self,task,content=None):
        if self.active:return
        if not self.account or not self.app_password:
            info(self,'First use Connect Gmail to sign in with a Google app password.')
            return
        self.active=True;self.send_button.setEnabled(False);self.refresh_button.setEnabled(False)
        self.status.setText({'connect':'Connecting securely...','fetch':'Fetching inbox...',
                             'send':'Sending message securely...'}[task])
        job=GmailWorker(task,self.account,self.app_password,content)
        self.threads.append(job)
        job.result.connect(self.on_job_result)
        job.finished.connect(lambda j=job: self.cleanup_thread(j))
        job.start()

    def cleanup_thread(self,job):
        if job in self.threads:self.threads.remove(job)
        job.deleteLater()

    def on_job_result(self,result):
        self.active=False;self.send_button.setEnabled(True);self.refresh_button.setEnabled(True)
        if not result.get('ok'):
            self.status.setText('Gmail operation failed.')
            error(self,'Gmail error: '+result.get('error','Unknown error')+
                  '\n\nCheck your internet, Gmail address, 2-Step Verification and app password.')
            return
        task=result['task']
        if task=='connect':
            self.status.setText('Connected to '+self.account)
            self.refresh_inbox()
        elif task=='fetch':
            self.inbox=result['messages']
            self.status.setText(f'Inbox refreshed: {len(self.inbox)} latest messages.')
            self.folders.setCurrentRow(0)
            self.refresh_messages(0)
        elif task=='send':
            record=dict(result['mail'])
            record['date']=datetime.now().isoformat(timespec='seconds')
            self.sent.insert(0,record)
            save_json(MAIL_DIR/'sent.json',self.sent)
            self.status.setText('Email sent successfully via Gmail.')
            self.new_message()
            self.folders.setCurrentRow(2)
            self.refresh_messages(2)
            info(self,'Email sent successfully.')

    def refresh_inbox(self):
        self.begin_job('fetch')

    def send_email(self):
        addr=self.to.text().strip()
        if not re.fullmatch(r'[^\s@,]+@[^\s@,]+\.[^\s@,]+',addr):
            error(self,'Please enter one valid recipient email address.');return
        if not self.body.toPlainText().strip():
            error(self,'Write an email before sending.');return
        data={'to':addr,'subject':self.subject.text(), 'body':self.body.toPlainText()}
        self.begin_job('send',data)

    def save_draft(self):
        record={'to':self.to.text(),'subject':self.subject.text(),
                'body':self.body.toPlainText(),'date':datetime.now().isoformat(timespec='seconds')}
        self.drafts.insert(0,record)
        save_json(MAIL_DIR/'drafts.json',self.drafts)
        self.status.setText('Draft saved locally in KaiOS Mail.')
        self.folders.setCurrentRow(1)
        self.refresh_messages(1)

    def new_message(self):
        self.to.clear();self.subject.clear();self.body.clear()

    def refresh_messages(self,index):
        if index<0:index=self.folders.currentRow()
        data=[self.inbox,self.drafts,self.sent][index] if 0<=index<=2 else []
        self.messages.clear()
        for item in data:
            brief=item.get('subject') or '(No subject)'
            sender=item.get('from') or item.get('to') or ''
            self.messages.addItem(brief[:45]+'\n'+sender[:35])

    def load_message(self,item):
        index=self.folders.currentRow()
        row=self.messages.row(item)
        groups=[self.inbox,self.drafts,self.sent]
        if not 0<=index<=2 or not 0<=row<len(groups[index]):return
        entry=groups[index][row]
        self.to.setText(entry.get('to',''))
        self.subject.setText(entry.get('subject',''))
        self.body.setPlainText(entry.get('body',''))


class CameraWindow(AppWindow):
    def __init__(self):
        super().__init__('KaiOS Camera',(860,650))
        w=QWidget();l=QVBoxLayout(w)
        self.info_label=QLabel('Starting the camera...')
        l.addWidget(self.info_label)
        self.camera=None;self.session=None;self.capture=None
        try:
            from PySide6.QtMultimedia import QCamera,QMediaDevices,QMediaCaptureSession,QImageCapture
            from PySide6.QtMultimediaWidgets import QVideoWidget
            cameras=QMediaDevices.videoInputs()
            if not cameras:
                self.info_label.setText('No camera detected. Connect a webcam and check Windows camera permissions.')
            else:
                self.camera=QCamera(cameras[0])
                self.session=QMediaCaptureSession()
                self.session.setCamera(self.camera)
                self.view=QVideoWidget()
                self.session.setVideoOutput(self.view)
                self.capture=QImageCapture()
                self.session.setImageCapture(self.capture)
                l.addWidget(self.view,1)
                self.camera.start()
                self.info_label.setText('Camera live preview. Photos are saved into KaiOS11/Photos.')
                self.capture.imageSaved.connect(lambda ident,path:self.info_label.setText('Saved photo: '+path))
                self.capture.errorOccurred.connect(lambda ident,code,message:error(self,message))
                take=QPushButton('Take Photo')
                take.clicked.connect(self.take_photo)
                l.addWidget(take)
        except Exception as exc:
            self.info_label.setText('Camera unavailable: '+str(exc))
        self.setCentralWidget(w)

    def take_photo(self):
        if not self.capture or not self.capture.isReadyForCapture():
            info(self,'Camera is not ready. Wait for a preview or check permissions.');return
        path=unique_file(PHOTOS/('Camera_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.jpg'))
        self.capture.captureToFile(str(path))

    def closeEvent(self,event):
        if self.camera:self.camera.stop()
        super().closeEvent(event)


# ---------------------------- FIVE GAMES ----------------------------
class GameCanvas(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host
        self.setMinimumSize(720,480)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet('background:#090f28')

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        self.host.paint_game(p,self.width(),self.height())

    def keyPressEvent(self,event):
        self.host.handle_key(event.key())
        self.update()

    def keyReleaseEvent(self,event):
        self.host.release_key(event.key())


class ArcadeWindow(AppWindow):
    def __init__(self,title,description):
        super().__init__(title,(950,710))
        w=QWidget();l=QVBoxLayout(w)
        row=QHBoxLayout()
        label=QLabel(description)
        label.setStyleSheet('color:#f7dca9;font-size:14px;')
        self.score_label=QLabel('Score: 0')
        self.score_label.setStyleSheet('font-size:16px;font-weight:bold;')
        reset=QPushButton('Restart Game')
        reset.clicked.connect(self.restart_game)
        row.addWidget(label,1);row.addWidget(self.score_label);row.addWidget(reset)
        l.addLayout(row)
        self.canvas=GameCanvas(self)
        l.addWidget(self.canvas,1)
        self.setCentralWidget(w)
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.keys=set()
        self.points=0
        self.over=False

    def restart_game(self):
        self.keys=set();self.points=0;self.over=False
        self.reset()
        self.timer.start(40)
        self.canvas.setFocus()
        self.update_score()
        self.canvas.update()

    def update_score(self):
        self.score_label.setText(f'Score: {self.points}')

    def stop_game(self):
        self.over=True;self.timer.stop();self.canvas.update()

    def paint_end(self,p,w,h,msg='GAME OVER - Restart to play'):
        if not self.over:return
        p.fillRect(0,0,w,h,QColor(7,8,22,145))
        p.setPen(QColor('#fff1c4'))
        font=p.font();font.setPointSize(23);font.setBold(True);p.setFont(font)
        p.drawText(QRect(10,0,w-20,h),Qt.AlignmentFlag.AlignCenter,msg)

    def handle_key(self,key):self.keys.add(key)
    def release_key(self,key):self.keys.discard(key)
    def reset(self):pass
    def tick(self):pass
    def paint_game(self,p,w,h):pass


class SnakeGame(ArcadeWindow):
    def __init__(self):
        super().__init__('KaiOS Snake','Arrow keys or WASD: steer the snake. Eat the glowing food.')
        self.restart_game()
    def reset(self):
        self.snake=[(10,8),(9,8),(8,8)]
        self.direction=(1,0);self.next_direction=(1,0)
        self.food=(15,6);self.frame=0
    def handle_key(self,key):
        mapping={Qt.Key.Key_Up:(0,-1),Qt.Key.Key_W:(0,-1),Qt.Key.Key_Down:(0,1),Qt.Key.Key_S:(0,1),
                 Qt.Key.Key_Left:(-1,0),Qt.Key.Key_A:(-1,0),Qt.Key.Key_Right:(1,0),Qt.Key.Key_D:(1,0)}
        if key in mapping:
            direction=mapping[key]
            if direction !=(-self.direction[0],-self.direction[1]):self.next_direction=direction
    def tick(self):
        self.frame+=1
        if self.frame%4:return
        self.direction=self.next_direction
        x,y=self.snake[0];dx,dy=self.direction
        head=(x+dx,y+dy)
        eating=head==self.food
        body=self.snake if eating else self.snake[:-1]
        if head[0]<0 or head[0]>=24 or head[1]<0 or head[1]>=17 or head in body:
            self.stop_game();return
        self.snake.insert(0,head)
        if eating:
            self.points+=10;self.update_score()
            options=[(a,b) for a in range(24) for b in range(17) if (a,b) not in self.snake]
            if not options:self.stop_game();return
            self.food=random.choice(options)
        else:self.snake.pop()
        self.canvas.update()
    def paint_game(self,p,w,h):
        p.fillRect(0,0,w,h,QColor('#0c1931'))
        cell=min((w-30)/24,(h-30)/17)
        x0=(w-24*cell)/2;y0=(h-17*cell)/2
        p.setPen(QColor('#1a3851'))
        for i in range(25):p.drawLine(QPoint(int(x0+i*cell),int(y0)),QPoint(int(x0+i*cell),int(y0+17*cell)))
        for i in range(18):p.drawLine(QPoint(int(x0),int(y0+i*cell)),QPoint(int(x0+24*cell),int(y0+i*cell)))
        for idx,(x,y) in enumerate(self.snake):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#a4ffcf' if idx==0 else '#37bcae'))
            p.drawRoundedRect(QRect(int(x0+x*cell+2),int(y0+y*cell+2),int(cell-4),int(cell-4)),5,5)
        x,y=self.food;p.setBrush(QColor('#ff85c9'))
        p.drawEllipse(QRect(int(x0+x*cell+5),int(y0+y*cell+5),int(cell-10),int(cell-10)))
        self.paint_end(p,w,h)


class SpaceExplorerGame(ArcadeWindow):
    def __init__(self):
        super().__init__('KaiOS Space Explorer','Arrow keys / WASD: fly. Space: fire lasers. Dodge asteroids!')
        self.restart_game()
    def reset(self):
        self.x=.5;self.y=.82;self.rocks=[];self.bullets=[];self.frame=0
        self.stars=[(random.random(),random.random(),random.choice([1,1,2,3])) for _ in range(130)]
    def handle_key(self,key):
        if key==Qt.Key.Key_Space:
            self.bullets.append([self.x,self.y-.06]);return
        super().handle_key(key)
    def tick(self):
        self.frame+=1
        speed=.018
        if Qt.Key.Key_Left in self.keys or Qt.Key.Key_A in self.keys:self.x-=speed
        if Qt.Key.Key_Right in self.keys or Qt.Key.Key_D in self.keys:self.x+=speed
        if Qt.Key.Key_Up in self.keys or Qt.Key.Key_W in self.keys:self.y-=speed
        if Qt.Key.Key_Down in self.keys or Qt.Key.Key_S in self.keys:self.y+=speed
        self.x=max(.06,min(.94,self.x));self.y=max(.13,min(.92,self.y))
        if self.frame%19==0:self.rocks.append([random.uniform(.05,.95),-.06,random.uniform(.02,.055)])
        for rock in self.rocks:rock[1]+=.006+self.points/100000
        for b in self.bullets:b[1]-=.024
        for rock in self.rocks[:]:
            if abs(rock[0]-self.x)<rock[2]+.025 and abs(rock[1]-self.y)<rock[2]+.02:
                self.stop_game();return
            for bullet in self.bullets[:]:
                if abs(rock[0]-bullet[0])<rock[2] and abs(rock[1]-bullet[1])<rock[2]:
                    self.rocks.remove(rock);self.bullets.remove(bullet)
                    self.points+=25;break
            if rock in self.rocks and rock[1]>1.1:
                self.rocks.remove(rock);self.points+=5
        self.bullets=[b for b in self.bullets if b[1]>-.05]
        if self.frame%25==0:self.update_score()
        self.canvas.update()
    def paint_game(self,p,w,h):
        p.fillRect(0,0,w,h,QColor('#0b102d'))
        p.setPen(Qt.PenStyle.NoPen)
        for x,y,r in self.stars:
            p.setBrush(QColor('#a5b8f9'));p.drawEllipse(QRect(int(x*w),int(y*h),r,r))
        for x,y,r in self.rocks:
            p.setBrush(QColor('#9b7698'));p.drawEllipse(QRect(int((x-r)*w),int((y-r)*h),int(r*2*w),int(r*2*h)))
            p.setBrush(QColor('#684f70'));p.drawEllipse(QRect(int((x-r/2)*w),int((y-r/3)*h),int(r*w),int(r*h)))
        p.setBrush(QColor('#ffcf6a'))
        for x,y in self.bullets:p.drawRect(int(x*w-2),int(y*h),4,17)
        x=self.x*w;y=self.y*h
        p.setBrush(QColor('#76e1fa'))
        p.drawPolygon([QPoint(int(x),int(y-32)),QPoint(int(x-21),int(y+25)),
                       QPoint(int(x),int(y+13)),QPoint(int(x+21),int(y+25))])
        p.setBrush(QColor('#ffa9e7'));p.drawEllipse(QRect(int(x-6),int(y-6),12,12))
        self.paint_end(p,w,h)


class AdventureQuestGame(ArcadeWindow):
    LEVELS = {
        'Level 1': {'goal': 1910, 'gems':[220,380,540,730,920,1110,1330,1540,1740], 'hazards':[480,850,1200,1570], 'speed':4.7, 'hp':3},
        'Level 2': {'goal': 2250, 'gems':[220,360,500,680,820,980,1140,1320,1490,1670,1860,2050], 'hazards':[420,640,880,1090,1310,1540,1790,2100], 'speed':5.0, 'hp':3},
        'Level 3': {'goal': 2550, 'gems':[180,320,470,610,770,930,1090,1250,1420,1600,1780,1970,2160,2360], 'hazards':[390,560,760,980,1180,1410,1650,1880,2130,2390], 'speed':5.3, 'hp':2},
    }
    def __init__(self):
        super().__init__('KaiOS Adventure Quest','Left/right or A/D to move. Space / Up to jump. Collect crystals!')
        header = self.centralWidget().layout().itemAt(0).layout()
        self.level_box = QComboBox()
        self.level_box.addItems(list(self.LEVELS.keys()))
        self.level_box.currentTextChanged.connect(lambda *_: self.restart_game())
        header.insertWidget(2, QLabel('Level:'))
        header.insertWidget(3, self.level_box)
        self.restart_game()
    def current_level(self):
        return self.level_box.currentText() if hasattr(self,'level_box') else 'Level 1'
    def reset(self):
        cfg=self.LEVELS[self.current_level()]
        self.x=80;self.y=425;self.vy=0;self.ground=450;self.frame=0;self.hp=cfg['hp']
        self.goal=cfg['goal']; self.run_speed=cfg['speed']
        self.gems=set(cfg['gems'])
        self.hazards=list(cfg['hazards'])
        self.jump_lock=False
    def handle_key(self,key):
        if key in (Qt.Key.Key_Space,Qt.Key.Key_Up,Qt.Key.Key_W):
            if self.y>=self.ground-25 and not self.jump_lock:
                self.vy=-13;self.jump_lock=True
        super().handle_key(key)
    def release_key(self,key):
        if key in (Qt.Key.Key_Space,Qt.Key.Key_Up,Qt.Key.Key_W):self.jump_lock=False
        super().release_key(key)
    def tick(self):
        if Qt.Key.Key_Left in self.keys or Qt.Key.Key_A in self.keys:self.x-=self.run_speed
        if Qt.Key.Key_Right in self.keys or Qt.Key.Key_D in self.keys:self.x+=self.run_speed
        self.x=max(20,min(self.goal+10,self.x))
        self.vy+=.6;self.y+=self.vy
        if self.y>self.ground-25:self.y=self.ground-25;self.vy=0
        for x in list(self.gems):
            if abs(x-self.x)<26 and abs((self.y+5)-(self.ground-66))<60:
                self.gems.remove(x);self.points+=15;self.update_score()
        for x in self.hazards:
            if abs(x-self.x)<18 and self.y>self.ground-55:
                self.hp-=1;self.x=max(30,self.x-100)
                if self.hp<=0:self.stop_game()
                break
        if self.x>=self.goal:self.over=True;self.timer.stop()
        self.canvas.update()
    def paint_game(self,p,w,h):
        p.fillRect(0,0,w,h,QColor('#19254f'))
        camera=max(0,min(max(0,self.goal-710),self.x-240))
        ground=min(h-45,450)
        p.setPen(Qt.PenStyle.NoPen)
        for xx in range(0,self.goal+300,130):
            x=int(xx-camera)
            p.setBrush(QColor('#292c6e'))
            p.drawPolygon([QPoint(x-70,int(ground)),QPoint(x+40,int(ground-180-(xx%3)*30)),QPoint(x+160,int(ground))])
        p.setBrush(QColor('#325871'));p.drawRect(0,int(ground),w,h-int(ground))
        p.setBrush(QColor('#48b8ab'));p.drawRect(0,int(ground),w,9)
        for x in self.gems:
            px=int(x-camera)
            if -20<=px<=w+20:
                p.setBrush(QColor('#7de4ff'))
                p.drawPolygon([QPoint(px, int(ground-84)),QPoint(px-11,int(ground-66)),
                               QPoint(px,int(ground-49)),QPoint(px+11,int(ground-66))])
        for x in self.hazards:
            px=int(x-camera)
            p.setBrush(QColor('#e96e9e'))
            p.drawPolygon([QPoint(px-20,int(ground)),QPoint(px,int(ground-32)),QPoint(px+20,int(ground))])
        ax=int(self.x-camera);ay=int(self.y+(ground-self.ground))
        p.setBrush(QColor('#ffcf8c'));p.drawEllipse(QRect(ax-13,ay-19,26,25))
        p.setBrush(QColor('#8773e5'));p.drawRoundedRect(QRect(ax-13,ay+6,26,26),6,6)
        p.setPen(QColor('#fff1c6'));p.drawText(20,30,f'Level: {self.current_level()}   Health: {self.hp}   Goal: reach the far right!')
        if self.over:
            self.paint_end(p,w,h,'QUEST COMPLETE!' if self.x>=self.goal else 'GAME OVER - Restart to play')


class KaiRacingGame(ArcadeWindow):
    LEVELS = {
        'Easy': {'spawn': 30, 'speed': .012},
        'Normal': {'spawn': 24, 'speed': .015},
        'Hard': {'spawn': 18, 'speed': .019},
    }
    def __init__(self):
        super().__init__('Kai Racing','Left/right arrows or A/D: change lanes. Avoid the cars!')
        self.scores=read_json(SCORE_FILE,{})
        header = self.centralWidget().layout().itemAt(0).layout()
        self.level_box = QComboBox()
        self.level_box.addItems(list(self.LEVELS.keys()))
        self.level_box.currentTextChanged.connect(lambda *_: self.restart_game())
        header.insertWidget(2, QLabel('Level:'))
        header.insertWidget(3, self.level_box)
        self.restart_game()
    def current_level(self):
        return self.level_box.currentText() if hasattr(self,'level_box') else 'Normal'
    def reset(self):
        self.lane=1;self.cars=[];self.frame=0
        self.best=self.scores.get('Kai Racing',0)
    def handle_key(self,key):
        if key in (Qt.Key.Key_Left,Qt.Key.Key_A):self.lane=max(0,self.lane-1)
        elif key in (Qt.Key.Key_Right,Qt.Key.Key_D):self.lane=min(2,self.lane+1)
    def tick(self):
        self.frame+=1
        cfg = self.LEVELS[self.current_level()]
        if self.frame % cfg['spawn'] == 0:self.cars.append([random.randint(0,2),-.18])
        for car in self.cars:car[1]+=cfg['speed']+min(.02,self.points/9000)
        for car in self.cars[:]:
            if car[0]==self.lane and abs(car[1]-.78)<.10:
                self.stop_game();self.best=max(self.best,self.points)
                self.scores['Kai Racing']=self.best;save_json(SCORE_FILE,self.scores)
                return
            if car[1]>1.2:self.cars.remove(car);self.points+=10
        if self.frame%25==0:self.update_score()
        self.canvas.update()
    def paint_game(self,p,w,h):
        p.fillRect(0,0,w,h,QColor('#192b3b'))
        roadw=min(460,w*.6);cx=w/2
        p.fillRect(QRect(int(cx-roadw/2),0,int(roadw),h),QColor('#202844'))
        p.setPen(QPen(QColor('#d2c0ee'),6));p.drawLine(int(cx-roadw/2),0,int(cx-roadw/2),h)
        p.drawLine(int(cx+roadw/2),0,int(cx+roadw/2),h)
        p.setPen(QPen(QColor('#f8e6b5'),3,Qt.PenStyle.DashLine))
        for i in (1,2):
            xx=int(cx-roadw/2+roadw*i/3)
            p.drawLine(xx,0,xx,h)
        lane_pos=lambda n:int(cx-roadw/2+roadw*(n+.5)/3)
        for lane,y in self.cars:
            x=lane_pos(lane)
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#d56b9e'))
            p.drawRoundedRect(QRect(x-28,int(y*h)-35,56,75),9,9)
        x=lane_pos(self.lane)
        p.setBrush(QColor('#66e7ec'))
        p.drawRoundedRect(QRect(x-27,int(h*.78)-34,54,75),9,9)
        p.setBrush(QColor('#173654'));p.drawRect(x-20,int(h*.78)-22,40,20)
        p.setPen(QColor('#e6f2ff'))
        p.drawText(20,28,'Level: '+self.current_level()+'   Best score: '+str(self.best))
        self.paint_end(p,w,h)


class CrystalMatchGame(AppWindow):
    COLORS=['#fc85bc','#78ccfd','#c596ff','#75e2bf','#ffd77b','#ee8e81']
    def __init__(self):
        super().__init__('Crystal Match',(720,760))
        self.score=0;self.selected=None;self.busy=False
        self.tiles=[]
        w=QWidget();l=QVBoxLayout(w)
        top=QHBoxLayout()
        self.label=QLabel('Score 0  |  Level 1  |  Select two neighbouring crystals to swap')
        reset=QPushButton('New Game');reset.clicked.connect(self.new_game)
        top.addWidget(self.label,1);top.addWidget(reset);l.addLayout(top)
        self.grid=QGridLayout();self.grid.setSpacing(4)
        for y in range(8):
            line=[]
            for x in range(8):
                button=QPushButton('◆')
                button.setMinimumSize(52,50)
                button.clicked.connect(lambda _,a=x,b=y:self.click_tile(a,b))
                self.grid.addWidget(button,y,x)
                line.append(button)
            self.tiles.append(line)
        l.addLayout(self.grid)
        l.addStretch()
        self.setCentralWidget(w)
        self.new_game()
    def new_game(self):
        self.score=0;self.selected=None;self.busy=False
        self.board=[[0]*8 for _ in range(8)]
        for y in range(8):
            for x in range(8):
                opts=list(range(len(self.COLORS)))
                if x>=2 and self.board[y][x-1]==self.board[y][x-2]:opts.remove(self.board[y][x-1])
                if y>=2 and self.board[y-1][x]==self.board[y-2][x] and self.board[y-1][x] in opts:opts.remove(self.board[y-1][x])
                self.board[y][x]=random.choice(opts)
        self.update_grid()
    def update_grid(self, highlights=None):
        self.label.setText(f'Score {self.score}  |  Level {1+self.score//200}  |  Match 3 or more!')
        highlights = highlights or set()
        for y in range(8):
            for x in range(8):
                color=self.COLORS[self.board[y][x]]
                chosen=(x,y)==self.selected
                extra='box-shadow: 0 0 12px white;' if (x,y) in highlights else ''
                border='4px solid #ffffff' if chosen else ('3px solid #fff1c6' if (x,y) in highlights else '1px solid #585188')
                self.tiles[y][x].setStyleSheet('background:'+color+';color:#25223d;'
                                  f'font-size:27px;font-weight:bold;border:{border};border-radius:10px;{extra}')
    def matches(self):
        found=set()
        for y in range(8):
            for x in range(6):
                if self.board[y][x]==self.board[y][x+1]==self.board[y][x+2]:
                    k=x
                    while k<8 and self.board[y][k]==self.board[y][x]:found.add((k,y));k+=1
        for x in range(8):
            for y in range(6):
                if self.board[y][x]==self.board[y+1][x]==self.board[y+2][x]:
                    k=y
                    while k<8 and self.board[k][x]==self.board[y][x]:found.add((x,k));k+=1
        return found
    def click_tile(self,x,y):
        if self.busy:
            return
        if self.selected is None:
            self.selected=(x,y);self.update_grid();return
        a,b=self.selected
        self.selected=None
        if abs(a-x)+abs(b-y)!=1:
            self.selected=(x,y);self.update_grid();return
        self.board[b][a],self.board[y][x]=self.board[y][x],self.board[b][a]
        matches=self.matches()
        if not matches:
            self.board[b][a],self.board[y][x]=self.board[y][x],self.board[b][a]
            self.update_grid()
            return
        self.process_matches(matches, 0)
    def process_matches(self, matches, cascade):
        self.busy=True
        self.update_grid(matches)
        QTimer.singleShot(130, lambda m=set(matches), c=cascade: self.resolve_matches(m, c))
    def resolve_matches(self, matches, cascade):
        self.score+=10*len(matches)*(1+cascade)
        for xx,yy in matches:self.board[yy][xx]=None
        for xx in range(8):
            values=[self.board[yy][xx] for yy in range(8) if self.board[yy][xx] is not None]
            refill=[random.randrange(len(self.COLORS)) for _ in range(8-len(values))]
            for yy,v in enumerate(refill+values):self.board[yy][xx]=v
        next_matches=self.matches()
        self.update_grid()
        if next_matches and cascade < 30:
            QTimer.singleShot(110, lambda m=set(next_matches), c=cascade+1: self.process_matches(m, c))
        else:
            self.busy=False
            self.update_grid()


class KaiOSPlus(KaiOS):
    """Build on the original desktop instead of replacing its applications."""
    APP_REGISTRY = dict(KaiOS.APP_REGISTRY)
    APP_REGISTRY.update({
        "Kai's Photos": ('PIC', EnhancedPhotosWindow),
        "Kai's Outlook": ('MAIL', EnhancedMailWindow),
        "Kai's Settings": ('SET', None),
        "Kai's File Manager": ('FILES', None),
        "Kai's Notepad": ('NOTE', NotepadWindow),
        'KaiOS Camera': ('CAM', CameraWindow),
        'Space Explorer': ('SPACE', SpaceExplorerGame),
        'Adventure Quest': ('QUEST', AdventureQuestGame),
        'Snake': ('SNAKE', SnakeGame),
        'Kai Racing': ('RACE', KaiRacingGame),
        'Crystal Match': ('GEMS', CrystalMatchGame),
    })

    def __init__(self):
        self.appearance=read_json(APPEARANCE_FILE,{})
        self.appearance.setdefault('performance','Balanced Mode')
        super().__init__()
        self.installed_apps.discard("Kai's Copilot")
        self.installed_apps.discard('KaiOS Voice Assistant')
        self.save_installed_apps()
        # The original version creates a plain QWidget desktop. Replace it with
        # a wallpaper-painted, file-aware desktop without rebuilding the taskbar.
        # Keep the same central layout and preserve the installed app choices.
        layout = self.centralWidget().layout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        original_desktop = self.desktop
        original_item = layout.takeAt(0)
        if original_item is not None:
            del original_item
        original_desktop.hide()
        original_desktop.setParent(None)
        original_desktop.deleteLater()
        self.desktop = KaiDesktopArea(self)
        self.grid = QGridLayout(self.desktop)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid.setSpacing(14)
        self.grid.setContentsMargins(14,14,14,14)
        layout.insertWidget(0, self.desktop, 1)
        StoreWindow.APP_NAMES=[name for name in self.APP_REGISTRY if name not in self.CORE_APPS]
        self.refresh_desktop()
        self.apply_performance()
        self.set_wallpaper()
        self.setWindowTitle('KaiOS 11')
        # Window-wide shortcut works even when a desktop button or search box has focus.
        self._was_maximized_before_fullscreen = False
        self.fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.fullscreen_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        self.setMinimumSize(900,600)
        # Allow manual refresh and creation of files via Windows Explorer.
        self.desktop.setToolTip('Drag files from File Explorer onto the KaiOS desktop to copy them here.')
        QTimer.singleShot(220,self.desktop.reload_files)

    def paintEvent(self, event):
        # Paint wallpaper beneath the central surface as well, so any
        # Windows/Qt main-window border inset cannot show a dark gutter.
        painter = QPainter(self)
        surface = self.centralWidget()
        if isinstance(surface, KaiWallpaperHost) and not surface.wallpaper_pixmap.isNull():
            painter.drawPixmap(self.rect(), surface.wallpaper_pixmap,
                               surface.wallpaper_pixmap.rect())
        else:
            painter.fillRect(self.rect(), QColor('#11152f'))

    def toggle_fullscreen(self):
        """F11 switches between true fullscreen and the previous window state."""
        if self.isFullScreen():
            if self._was_maximized_before_fullscreen:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized_before_fullscreen = self.isMaximized()
            self.showFullScreen()

    def save_appearance(self):
        save_json(APPEARANCE_FILE,self.appearance)

    def apply_performance(self):
        mode=self.appearance.get('performance','Balanced Mode')
        # Clock does not need to repaint each second, particularly in Fast Mode.
        self.timer.setInterval(15000 if mode=='Fast Mode' else 2000 if mode=='Balanced Mode' else 1000)
        # Faster mode skips smooth background interpolation on every resize.
        if hasattr(self.desktop,'high_quality'):
            self.desktop.high_quality=(mode!='Fast Mode')
        self.desktop.update()

    def set_wallpaper(self):
        current=self.appearance.get('wallpaper', '')
        if current and is_retired_moonlit_wallpaper(current):
            # Old accounts may still point at the retired wallpaper.
            # Reset the preference but leave the old picture file untouched.
            self.appearance['wallpaper']=str(DEFAULT_WALLPAPER) if DEFAULT_WALLPAPER.is_file() else ''
            self.save_appearance()
        path=selected_wallpaper(self.appearance)
        self.active_wallpaper=path
        # Refresh stored absolute paths after ZIP upgrades / folder moves.
        if path and self.appearance.get('wallpaper') and self.appearance['wallpaper'] != str(path):
            self.appearance['wallpaper'] = str(path)
            self.save_appearance()
        surface=self.centralWidget()
        if isinstance(surface,KaiWallpaperHost):
            surface.change_background(path)
        if isinstance(self.desktop,KaiDesktopArea):
            self.desktop.change_background(path)
        else:
            super().set_wallpaper()
        self.refresh_desktop()

    def refresh_desktop(self):
        super().refresh_desktop()
        wallpaper=selected_wallpaper(self.appearance)
        text=make_wallpaper_text(wallpaper)
        for i in range(self.grid.count()):
            w=self.grid.itemAt(i).widget()
            if w:
                w.setStyleSheet(f'background:rgba(30,35,83,220);color:{text};'
                                'font-size:13px;font-weight:bold;border:1px solid #7772d8;border-radius:15px;')
        if isinstance(self.desktop,KaiDesktopArea):
            for tile in self.desktop.tiles:
                tile.raise_()

    def open_browser(self,url='https://www.google.com/'):
        win=BrowserWindow(url)
        self.open_windows.append(win)
        win.show();win.raise_();win.activateWindow()

    def launch_named(self,name):
        needle=name.strip().lower()
        match=next((app for app in self.APP_REGISTRY if needle in app.lower()),None)
        if not match:
            error(self,'No KaiOS app matches '+name);return
        if match not in self.installed_apps:
            info(self,match+' is not installed yet. Open KaiOS Store to install it.');return
        if match=="Kai's File Manager":
            if sys.platform=='win32':
                try:subprocess.Popen(['explorer.exe',str(ROOT)])
                except Exception as exc:error(self,'File Explorer could not open: '+str(exc))
            else:open_system(ROOT)
            return
        if match=="Kai's Settings":win=EnhancedSettingsWindow(self)
        else:
            return super().launch_named(match)
        self.open_windows.append(win)
        win.show();win.raise_();win.activateWindow()

    def start_menu(self):
        menu=QMenu(self)
        menu.addAction('KaiOS 11').setEnabled(False)
        menu.addSeparator()
        for name in ["Kai's Browser",'KaiOS Store',"Kai's Settings","Kai's File Manager"]+sorted(self.installed_apps-self.CORE_APPS):
            menu.addAction(name,lambda n=name:self.launch_named(n))
        menu.addSeparator()
        menu.addAction('Open KaiOS Desktop folder',lambda:open_system(DESKTOP))
        menu.addAction('Open KaiOS files in Explorer',lambda:open_system(ROOT))
        menu.addAction('Add a text file to KaiOS Desktop',self.create_desktop_file)
        menu.addAction('Refresh desktop files',self.desktop.reload_files)
        menu.addSeparator()
        fullscreen_label = 'Exit Fullscreen (F11)' if self.isFullScreen() else 'Enter Fullscreen (F11)'
        menu.addAction(fullscreen_label, self.toggle_fullscreen)
        menu.addSeparator()
        if getattr(self, 'active_username', None):
            menu.addAction('Signed in as: ' + self.active_username).setEnabled(False)
        if getattr(self, 'session_controller', None) is not None:
            menu.addAction('Switch account / Sign out', self.session_controller.sign_out)
        menu.addSeparator()
        menu.addAction('Shut down KaiOS',self.close)
        menu.exec(self.cursor().pos())

    def create_desktop_file(self):
        target=unique_file(DESKTOP/'New text file.txt')
        target.write_text('',encoding='utf-8')
        self.desktop.reload_files()

    def closeEvent(self,event):
        self.desktop.save_tile_locations()
        self.save_appearance()
        for window in self.open_windows[:]:
            try:
                window.close()
            except RuntimeError:
                pass
        super().closeEvent(event)
        if event.isAccepted() and not getattr(self, '_logging_out', False):
            QTimer.singleShot(0, QApplication.instance().quit)


class CreateKaiOSAccountDialog(QDialog):
    """Create account with password confirmation and a strict five-account cap."""
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.created_account = None
        self.setWindowTitle('KaiOS 11 - Create account')
        self.setWindowIcon(kaios_icon())
        self.setMinimumWidth(460)
        self.setStyleSheet('QDialog{background:#18244a;color:#fff3df;}'
                           'QLabel{color:#fff3df;font-size:14px;}'
                           'QLineEdit{background:#f9f8ff;color:#172046;'
                           'padding:10px;border:2px solid #758cde;border-radius:9px;}'
                           'QPushButton{background:#4d57a7;color:white;padding:11px;border-radius:9px;}')
        v = QVBoxLayout(self)
        title = QLabel('Create your KaiOS account')
        title.setStyleSheet('font-size:23px;font-weight:bold;color:#ffdfbe;')
        v.addWidget(title)
        v.addWidget(QLabel('Each account has its own username, password and KaiOS files.'))
        v.addWidget(QLabel('Username'))
        self.username = QLineEdit()
        self.username.setPlaceholderText('Enter a username')
        self.username.setMaxLength(25)
        v.addWidget(self.username)
        v.addWidget(QLabel('Password (at least 4 characters)'))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Create a password or PIN')
        v.addWidget(self.password)
        v.addWidget(QLabel('Confirm password'))
        self.confirmation = QLineEdit()
        self.confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirmation.setPlaceholderText('Enter your password again')
        self.confirmation.returnPressed.connect(self.create)
        v.addWidget(self.confirmation)
        row = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        create = QPushButton('Create Account')
        create.clicked.connect(self.create)
        create.setDefault(True)
        row.addWidget(cancel);row.addWidget(create)
        v.addLayout(row)

    def create(self):
        try:
            first_account = len(self.store.accounts) == 0
            record = self.store.create(self.username.text(), self.password.text(),
                                       self.confirmation.text(), import_old=first_account)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Cannot create account', str(exc))
            return
        self.password.clear()
        self.confirmation.clear()
        self.created_account = record
        self.accept()


class KaiOSLoginDialog(QDialog):
    """Shows login on each launch and lets users create up to five accounts."""
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.selected_account = None
        self.setWindowTitle('KaiOS 11 - Sign in')
        self.setWindowIcon(kaios_icon())
        # Keep enough height for large fonts / Windows display scaling.
        self.setMinimumSize(580, 500)
        self.setStyleSheet(f'QDialog{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,'
                           'stop:0 #832f60,stop:.45 #38296b,stop:1 #1a48aa);color:#fff5e4;}}'
                           'QLabel{color:#fff5e4;font-size:15px;}'
                           # The password is an editable field; the username is a selector,
                           # and MUST NOT inherit QLineEdit padding (which clipped its text).
                           'QLineEdit{background:#faf9ff;color:#101827;padding:9px 12px;'
                           'border:2px solid #929de5;border-radius:9px;font-size:16px;}'
                           'QComboBox#accountUserBox{background:#ffffff;color:#101827;'
                           'padding:0px 52px 0px 16px;min-height:52px;'
                           'border:2px solid #a4ade9;border-radius:12px;font-size:18px;}'
                           'QComboBox#accountUserBox:hover{border-color:#c3c9f3;}'
                           'QComboBox#accountUserBox:on{border-color:#d1d6ff;}'
                           'QComboBox#accountUserBox::drop-down{width:50px;subcontrol-origin:padding;subcontrol-position:top right;'
                           'border-left:1px solid #d4d9ef;background:rgba(245,247,255,0.92);border-top-right-radius:10px;border-bottom-right-radius:10px;}'
                           'QComboBox#accountUserBox::down-arrow{image:url(' + ACCOUNT_ARROW_PNG.as_posix() + ');width:18px;height:12px;}'
                           'QComboBox QAbstractItemView{background:#ffffff;color:#101827;'
                           'border:1px solid #929de5;font-size:17px;'
                           'selection-background-color:#5268ab;selection-color:#ffffff;outline:none;}'
                           'QComboBox QAbstractItemView::item{min-height:36px;padding:7px 12px;}'
                           'QPushButton{background:#323c8e;color:white;padding:12px;'
                           'border:1px solid #a4a9ec;border-radius:10px;font-weight:bold;}')
        v = QVBoxLayout(self)
        v.setContentsMargins(38, 30, 38, 27)
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QPixmap(str(KAIOS_LOGO_PNG)).scaled(
            QSize(90, 90), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        logo.setMinimumHeight(96)
        v.addWidget(logo)
        title = QLabel('Welcome to KaiOS 11')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet('font-size:26px;font-weight:bold;')
        v.addWidget(title)
        self.hint = QLabel('')
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        v.addWidget(QLabel('Username'))
        self.user_box = QComboBox()
        self.user_box.setObjectName('accountUserBox')
        # Existing usernames are selected from a list. A non-editable combo
        # avoids an inner QLineEdit with two stacked padding rules, the cause
        # of text being visibly sliced on some Windows DPI settings.
        self.user_box.setEditable(False)
        self.user_box.setMinimumHeight(58)
        self.user_box.setMaxVisibleItems(MAX_ACCOUNTS)
        self.user_box.setToolTip('Choose your KaiOS account')
        self.user_box.view().setStyleSheet(
            'QListView{background:#ffffff;color:#101827;font-size:17px;'
            'border:1px solid #8797ce;}'
            'QListView::item{background:#ffffff;color:#101827;'
            'min-height:36px;padding:7px 12px;}'
            'QListView::item:selected{background:#5268ab;color:#ffffff;}')
        v.addWidget(self.user_box)
        v.addWidget(QLabel('Password / login code'))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Enter your password')
        self.password.returnPressed.connect(self.sign_in)
        v.addWidget(self.password)
        row = QHBoxLayout()
        self.login_button = QPushButton('Sign In')
        self.login_button.clicked.connect(self.sign_in)
        self.new_button = QPushButton('Create Account')
        self.new_button.clicked.connect(self.create_account)
        quit_button = QPushButton('Exit')
        quit_button.clicked.connect(self.reject)
        row.addWidget(self.login_button)
        row.addWidget(self.new_button)
        row.addWidget(quit_button)
        v.addLayout(row)
        self.delete_button = QPushButton('Delete Account')
        self.delete_button.setToolTip('Remove an account after confirming its password.')
        self.delete_button.setStyleSheet(
            'QPushButton{background:#713650;color:#ffffff;padding:10px;'
            'border:1px solid #d19ab0;border-radius:9px;font-weight:bold;}')
        self.delete_button.clicked.connect(self.delete_account)
        v.addWidget(self.delete_button)
        self.refresh_users()

    def refresh_users(self):
        current = self.user_box.currentText()
        self.user_box.clear()
        self.user_box.addItems(self.store.usernames())
        if current in self.store.usernames():
            self.user_box.setCurrentText(current)
        total = len(self.store.accounts)
        if total == 0:
            self.hint.setText('No accounts yet. Choose Create Account to begin!')
        else:
            self.hint.setText(f'{total} of {MAX_ACCOUNTS} accounts created. Select your account and sign in.')
        self.login_button.setEnabled(total > 0)
        self.new_button.setEnabled(total < MAX_ACCOUNTS)
        self.delete_button.setEnabled(total > 0)
        if total == 1:
            self.delete_button.setToolTip('Delete the final account using its password; KaiOS will then offer Create Account.')
        else:
            self.delete_button.setToolTip('Requires the selected account password and confirmation.')
        if total == MAX_ACCOUNTS:
            self.new_button.setToolTip('Maximum 5 accounts reached.')
        self.password.clear()

    def create_account(self):
        dialog = CreateKaiOSAccountDialog(self.store, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.created_account:
            self.selected_account = dialog.created_account
            self.accept()  # Creating an account signs into it immediately.

    def delete_account(self):
        username = self.user_box.currentText().strip()
        if username not in self.store.usernames():
            QMessageBox.warning(self, 'Choose an account',
                                'Select an existing username from the list first.')
            return
        dialog = DeleteKaiOSAccountDialog(self.store, username, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_users()
            QMessageBox.information(self, 'Account deleted', dialog.result_message)

    def sign_in(self):
        username = self.user_box.currentText().strip()
        record = self.store.authenticate(username, self.password.text())
        if record is None:
            QMessageBox.warning(self, 'Sign in failed', 'Wrong username or password.')
            self.password.clear()
            self.password.setFocus()
            return
        self.password.clear()
        self.selected_account = record
        self.accept()



class DeleteKaiOSAccountDialog(QDialog):
    '''Confirm account removal using its own password and a second confirmation.'''
    def __init__(self, store, username, parent=None):
        super().__init__(parent)
        self.store = store
        self.username = username
        self.result_message = ''
        self.setWindowTitle('KaiOS 11 - Delete account')
        self.setWindowIcon(kaios_icon())
        self.setMinimumWidth(490)
        self.setStyleSheet('QDialog{background:#1b2549;color:#fff1dd;}'
                           'QLabel,QCheckBox{color:#fff1dd;font-size:14px;}'
                           'QLineEdit{background:#ffffff;color:#101827;'
                           'padding:10px;border:2px solid #97a5dc;border-radius:8px;}'
                           'QPushButton{background:#4b5598;color:white;padding:10px;border-radius:9px;}')
        layout = QVBoxLayout(self)
        heading = QLabel('Delete KaiOS account: ' + username)
        heading.setWordWrap(True)
        heading.setStyleSheet('font-size:21px;font-weight:bold;color:#ffdfbd;')
        layout.addWidget(heading)
        is_last_account = len(store.accounts) == 1
        explanation = ('This is the last account. Deleting it will return KaiOS to '
                       'the Create Account screen.\n' if is_last_account else '')
        message = QLabel(explanation + 'Enter this account\'s password to remove it.\n'
                         'By default, its personal files are saved in a recovery folder.')
        message.setWordWrap(True)
        layout.addWidget(message)
        layout.addWidget(QLabel('Account password'))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Enter password to confirm')
        layout.addWidget(self.password)
        self.permanent = QCheckBox('Also permanently delete this account\'s files (cannot undo)')
        layout.addWidget(self.permanent)
        row = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        delete = QPushButton('Delete Account')
        delete.setStyleSheet('background:#9c3556;color:white;font-weight:bold;padding:12px;')
        delete.clicked.connect(self.confirm_delete)
        row.addWidget(cancel)
        row.addWidget(delete)
        layout.addLayout(row)

    def confirm_delete(self):
        if self.store.authenticate(self.username, self.password.text()) is None:
            QMessageBox.warning(self, 'Wrong password',
                                'Incorrect account password. Nothing was deleted.')
            self.password.clear()
            self.password.setFocus()
            return
        last_account_warning = ('\n\nThis is your final KaiOS account. '
                                'After deletion, KaiOS will have no accounts '
                                'until you create a new one.'
                                if len(self.store.accounts) == 1 else '')
        if self.permanent.isChecked():
            warning = (f'Delete account "{self.username}" and permanently destroy its '
                       'KaiOS Desktop, Documents, Photos, Downloads, and other account files?'
                       + last_account_warning + '\n\nThis action cannot be undone.')
        else:
            warning = (f'Delete account "{self.username}"?'
                       '\n\nIts login will be removed and its files saved to DeletedAccounts for recovery.'
                       + last_account_warning)
        answer = QMessageBox.question(self, 'Confirm account deletion', warning,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.result_message = self.store.delete_account(
                self.username, self.password.text(),
                permanently_delete_files=self.permanent.isChecked())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Account not deleted', str(exc))
            self.password.clear()
            return
        self.password.clear()
        self.accept()


class KaiOSSessionManager:
    def __init__(self, app):
        self.app = app
        self.window = None

    def open_login(self):
        try:
            store = AccountStore()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.critical(None, 'Account storage error',
                                 'KaiOS cannot read the existing accounts file: ' + str(exc))
            self.app.quit()
            return
        login = KaiOSLoginDialog(store)
        if login.exec() != QDialog.DialogCode.Accepted:
            self.app.quit()
            return
        account = login.selected_account
        if not account:
            self.app.quit()
            return
        try:
            activate_account(account)
            self.window = KaiOSPlus()
            self.window.active_username = account['username']
            self.window.session_controller = self
            self.window.setWindowTitle('KaiOS 11 - ' + account['username'])
            self.window.show()
        except Exception as exc:
            QMessageBox.critical(None, 'KaiOS startup problem', str(exc))
            self.app.quit()

    def sign_out(self):
        if self.window:
            old = self.window
            # Avoid deleting an active QThread in the Gmail app or letting
            # results from the old account arrive in the next account.
            active_mail = any(
                any(worker.isRunning() for worker in getattr(win, 'threads', []))
                for win in getattr(old, 'open_windows', [])
            )
            if active_mail:
                QMessageBox.information(old, 'KaiOS is finishing mail',
                    'A Gmail operation is in progress. Close or finish it, '
                    'then choose Switch Account again.')
                return
            # Prevent windows from one account remaining open after sign out.
            for app_window in list(getattr(old, 'open_windows', [])):
                try:
                    app_window.close()
                    app_window.deleteLater()
                except RuntimeError:
                    pass
            old.open_windows.clear()
            old._logging_out = True
            old.close()
            old.deleteLater()
            self.window = None
        QTimer.singleShot(0, self.open_login)


def main():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('KaiOS11.UniversalLogo')
        except (AttributeError, OSError):
            pass
    app=QApplication(sys.argv)
    app.setApplicationName('KaiOS 11')
    app.setWindowIcon(kaios_icon())
    # Keep process open while closing the desktop during account switching.
    app.setQuitOnLastWindowClosed(False)
    manager=KaiOSSessionManager(app)
    QTimer.singleShot(0,manager.open_login)
    sys.exit(app.exec())


if __name__=='__main__':
    main()
