import sys
import os

# === Папка .vane для настроек и библиотек ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VANE_DIR = os.path.join(BASE_DIR, ".vane")

if os.path.exists(VANE_DIR):
    sys.path.insert(0, VANE_DIR)

import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
import threading
import time
import json
import subprocess
import traceback

try:
    import pydirectinput
    from pynput import keyboard
    import numpy as np
    import mss
    PIL_OK = True
except ImportError as e:
    import tkinter.messagebox as mb
    mb.showerror(
        "Ошибка библиотек", 
        "Не найдены библиотеки!\n\n"
        "Открой командную строку (CMD) в папке с игрой и запусти:\n\n"
        "pip install pydirectinput pynput numpy mss --target=.vane\n\n"
        f"Детали ошибки: {e}"
    )
    sys.exit()

pydirectinput.PAUSE = 0
pydirectinput.FAILSAFE = False

if not os.path.exists(VANE_DIR):
    try:
        os.makedirs(VANE_DIR)
        if os.name == 'nt':
            subprocess.call(["attrib", "+h", "+s", VANE_DIR])
    except Exception as e:
        print("Ошибка создания папки .vane:", e)

SETTINGS_FILE = os.path.join(VANE_DIR, "multi_clicker_settings.json")

DEFAULT_SETTINGS = {
    "key_toggle": "f9", "key_instant": "f10", "key_exit": "esc",
    "ac_delay": "1", "ac_unit": "мс", "ac_button": "left",
    "kh_keys": [], "kh_cycle_delay": "0", "kh_hold_method": "pynput",
    "px_color": "#ff0000", "px_tolerance": "10", "px_return": True, "px_move_speed": "0.05",
    "sc_left": "0", "sc_top": "0", "sc_right": "500", "sc_bottom": "500",
    "search_mode": "rgb", "hsv_min_h": "0", "hsv_max_h": "10",
    "ac_check_delay": "1000"
}

class MultiClickerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Multi Clicker (Turbo)")
        self.root.geometry("640x900")
        self.root.attributes("-topmost", True)

        self.alive = True
        self.settings = self.load_settings()

        self.ac_running = False
        self.ac_clicks = 0
        self.ac_button = self.settings["ac_button"]
        self.ac_delay_sec = 0.001
        self.ac_mode = "click"
        self.ac_busy = False
        self.ac_check_delay_sec = 1.0

        self.px_color_rgb = (255, 0, 0)
        self.px_tolerance = 10
        self.px_return = True
        self.px_move_speed = 0.05
        self.px_hits = 0
        
        self.sc_left = 0
        self.sc_top = 0
        self.sc_right = 500
        self.sc_bottom = 500
        
        self.search_mode = "rgb"
        self.hsv_min_h = 0
        self.hsv_max_h = 10

        self.kh_state = "idle"
        self.kh_cycle_count = 0
        self.kh_keys = list(self.settings["kh_keys"])
        self.kh_recording_key = None
        self.kh_hold_method = self.settings.get("kh_hold_method", "pynput")
        self.kh_cycle_delay_ms = 0

        self.key_toggle = self.settings["key_toggle"]
        self.key_instant = self.settings["key_instant"]
        self.key_exit = self.settings["key_exit"]
        self.capture_for = None
        self.listener = None

        self.crosshair_win = None
        self.crosshair_canvas = None
        self.crosshair_center_x = 0
        self.crosshair_center_y = 0

        self.crosshair_win2 = None
        self.crosshair_canvas2 = None
        self.crosshair_center_x2 = 0
        self.crosshair_center_y2 = 0

        self.ch_click_at_cross = tk.BooleanVar(value=False)
        self.ch_move_mouse_to_cross = tk.BooleanVar(value=False)

        self.build_ui()
        self.apply_settings()

        # Потоки
        threading.Thread(target=self.ac_loop, daemon=True).start()
        threading.Thread(target=self.kh_loop, daemon=True).start()
        self.start_listener()

        # Запускаем отдельный поток для умного кликера!
        self.search_thread = threading.Thread(target=self.smart_check_loop, daemon=True)
        self.search_thread.start()

        self.root.after(200, self.sync_settings)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        hf = tk.LabelFrame(self.root, text="Хоткеи")
        hf.pack(fill="x", padx=8, pady=4)
        row = tk.Frame(hf)
        row.pack(fill="x", padx=4, pady=2)
        self.hk_labels = {}
        for name, label in [("toggle", "Старт/Стоп"), ("instant", "Мгновенно"), ("exit", "Выход")]:
            tk.Label(row, text=label + ":").pack(side="left", padx=(6, 2))
            lbl = tk.Label(row, text="", fg="blue", width=6)
            lbl.pack(side="left")
            self.hk_labels[name] = lbl
            tk.Button(row, text="…", width=3, command=lambda n=name: self.start_capture(n)).pack(side="left", padx=2)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=4)
        
        self.tab_ac = tk.Frame(self.notebook)
        self.notebook.add(self.tab_ac, text="🖱 Автокликер")
        self.tab_sc = tk.Frame(self.notebook)
        self.notebook.add(self.tab_sc, text="🔍 Поиск в области")
        self.tab_kh = tk.Frame(self.notebook)
        self.notebook.add(self.tab_kh, text="⌨ Запись клавиш")
        
        self.build_ac_tab()
        self.build_sc_tab()
        self.build_kh_tab()

        self.status = tk.Label(self.root, text="Готов", fg="gray", wraplength=550)
        self.status.pack(fill="x", padx=8, pady=2)
        tk.Button(self.root, text="Сохранить настройки", command=self.save_settings).pack(pady=4)

    def build_ac_tab(self):
        self.ac_btn = tk.Button(self.tab_ac, text="▶ СТАРТ", font=("Arial", 14, "bold"), bg="#4CAF50", fg="white", command=self.ac_toggle)
        self.ac_btn.pack(fill="x", padx=8, pady=6)

        f = tk.LabelFrame(self.tab_ac, text="Режим")
        f.pack(fill="x", padx=8, pady=4)
        self.ac_mode_var = tk.StringVar(value="click")
        tk.Radiobutton(f, text="Обычные клики", variable=self.ac_mode_var, value="click", command=self.ac_change_mode).pack(anchor="w", padx=6)
        tk.Radiobutton(f, text="Только поиск цвета", variable=self.ac_mode_var, value="search", command=self.ac_change_mode).pack(anchor="w", padx=6)
        tk.Radiobutton(f, text="Умный кликер (Клики + Поиск)", variable=self.ac_mode_var, value="smart", command=self.ac_change_mode).pack(anchor="w", padx=6)

        f = tk.LabelFrame(self.tab_ac, text="Задержка кликов")
        f.pack(fill="x", padx=8, pady=4)
        row = tk.Frame(f)
        row.pack(fill="x", padx=4, pady=4)
        self.ac_delay_entry = tk.Entry(row, width=10)
        self.ac_delay_entry.insert(0, "1")
        self.ac_delay_entry.pack(side="left", padx=4)
        self.ac_unit_var = tk.StringVar(value="мс")
        ttk.Combobox(row, textvariable=self.ac_unit_var, values=["мс", "сек", "мин", "час"], state="readonly", width=6).pack(side="left", padx=4)
        tk.Button(row, text="Очень быстро", bg="#ffcc00", command=self.ac_turbo).pack(side="left", padx=4)

        self.smart_frame = tk.LabelFrame(self.tab_ac, text="Настройки умного кликера")
        row_smart = tk.Frame(self.smart_frame)
        row_smart.pack(fill="x", padx=4, pady=4)
        tk.Label(row_smart, text="Проверять цвет каждые (мс):").pack(side="left")
        self.ac_check_delay_entry = tk.Entry(row_smart, width=6)
        self.ac_check_delay_entry.insert(0, "1000")
        self.ac_check_delay_entry.pack(side="left", padx=4)

        f = tk.LabelFrame(self.tab_ac, text="Кнопка мыши")
        f.pack(fill="x", padx=8, pady=4)
        self.ac_button_var = tk.StringVar(value="left")
        tk.OptionMenu(f, self.ac_button_var, "left", "right", "middle").pack(side="left", padx=6, pady=4)

        f = tk.LabelFrame(self.tab_ac, text="Прицел на экране")
        f.pack(fill="x", padx=8, pady=4)
        
        row = tk.Frame(f)
        row.pack(fill="x", padx=4, pady=4)
        self.ch_enable = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Включить прицел", variable=self.ch_enable, command=self.toggle_crosshair).pack(side="left", padx=4)
        
        tk.Label(row, text="Размер:").pack(side="left", padx=(10, 2))
        self.ch_size = tk.Scale(row, from_=20, to=1000, orient="horizontal", showvalue=False, command=self.update_crosshair_ui)
        self.ch_size.set(200)
        self.ch_size.pack(side="left", padx=4)

        tk.Label(row, text="Прозрачность:").pack(side="left", padx=(10, 2))
        self.ch_alpha = tk.Scale(row, from_=0.1, to=1.0, resolution=0.1, orient="horizontal", showvalue=False, command=self.update_crosshair_ui)
        self.ch_alpha.set(0.5)
        self.ch_alpha.pack(side="left", padx=4)

        row2 = tk.Frame(f)
        row2.pack(fill="x", padx=4, pady=4)
        tk.Checkbutton(row2, text="Кликать по прицелу", variable=self.ch_click_at_cross).pack(side="left", padx=4)
        tk.Checkbutton(row2, text="Перемещать мышь к прицелу", variable=self.ch_move_mouse_to_cross).pack(side="left", padx=4)

        self.ac_counter = tk.Label(self.tab_ac, text="Кликов: 0 | Попаданий: 0", font=("Arial", 11, "bold"))
        self.ac_counter.pack(pady=6)

    def build_sc_tab(self):
        f1 = tk.LabelFrame(self.tab_sc, text="Область поиска (X1, Y1, X2, Y2)")
        f1.pack(fill="x", padx=8, pady=6)
        row = tk.Frame(f1)
        row.pack(padx=4, pady=4)
        
        tk.Label(row, text="X1:").pack(side="left")
        self.sc_l_entry = tk.Spinbox(row, from_=0, to=5000, width=6)
        self.sc_l_entry.insert(0, "0")
        self.sc_l_entry.pack(side="left", padx=(2, 6))
        
        tk.Label(row, text="Y1:").pack(side="left")
        self.sc_t_entry = tk.Spinbox(row, from_=0, to=5000, width=6)
        self.sc_t_entry.insert(0, "0")
        self.sc_t_entry.pack(side="left", padx=(2, 6))
        
        tk.Label(row, text="X2:").pack(side="left")
        self.sc_r_entry = tk.Spinbox(row, from_=0, to=5000, width=6)
        self.sc_r_entry.insert(0, "500")
        self.sc_r_entry.pack(side="left", padx=(2, 6))
        
        tk.Label(row, text="Y2:").pack(side="left")
        self.sc_b_entry = tk.Spinbox(row, from_=0, to=5000, width=6)
        self.sc_b_entry.insert(0, "500")
        self.sc_b_entry.pack(side="left", padx=(2, 6))

        f2 = tk.LabelFrame(self.tab_sc, text="Искомый цвет и режим")
        f2.pack(fill="x", padx=8, pady=6)
        
        row_mode = tk.Frame(f2)
        row_mode.pack(fill="x", padx=4, pady=2)
        self.search_mode_var = tk.StringVar(value="rgb")
        tk.Radiobutton(row_mode, text="RGB (точный)", variable=self.search_mode_var, value="rgb", command=self.update_search_mode).pack(side="left")
        tk.Radiobutton(row_mode, text="HSV (поиск оттенков)", variable=self.search_mode_var, value="hsv", command=self.update_search_mode).pack(side="left", padx=10)

        row = tk.Frame(f2)
        row.pack(padx=4, pady=2)
        self.px_color_entry = tk.Entry(row, width=10)
        self.px_color_entry.insert(0, "#ff0000")
        self.px_color_entry.pack(side="left", padx=4)
        self.px_color_preview = tk.Label(row, text="   ", bg="#ff0000", width=3)
        self.px_color_preview.pack(side="left", padx=4)
        tk.Button(row, text="...", command=self.pick_color).pack(side="left")
        tk.Button(row, text="Взять с экрана (5 сек)", command=self.pick_color_from_screen).pack(side="left", padx=10)

        row_tol = tk.Frame(f2)
        row_tol.pack(padx=4, pady=2)
        tk.Label(row_tol, text="Допуск RGB:").pack(side="left")
        self.px_tolerance_entry = tk.Entry(row_tol, width=5)
        self.px_tolerance_entry.insert(0, "10")
        self.px_tolerance_entry.pack(side="left", padx=4)

        self.hsv_frame = tk.Frame(f2)
        tk.Label(self.hsv_frame, text="Мин H:").pack(side="left")
        self.hsv_min_entry = tk.Entry(self.hsv_frame, width=5)
        self.hsv_min_entry.insert(0, "0")
        self.hsv_min_entry.pack(side="left", padx=4)
        tk.Label(self.hsv_frame, text="Макс H:").pack(side="left")
        self.hsv_max_entry = tk.Entry(self.hsv_frame, width=5)
        self.hsv_max_entry.insert(0, "10")
        self.hsv_max_entry.pack(side="left", padx=4)

        f3 = tk.LabelFrame(self.tab_sc, text="Куда кликать при находке")
        f3.pack(fill="x", padx=8, pady=6)
        row = tk.Frame(f3)
        row.pack(padx=4, pady=4)
        tk.Label(row, text="Режим:").pack(side="left")
        self.sc_click_mode = tk.StringVar(value="center")
        tk.Radiobutton(row, text="В центр найденного", variable=self.sc_click_mode, value="center").pack(side="left")
        tk.Radiobutton(row, text="В заданную точку", variable=self.sc_click_mode, value="point").pack(side="left", padx=10)
        
        row = tk.Frame(f3)
        row.pack(padx=4, pady=4)
        
        tk.Label(row, text="X:").pack(side="left")
        self.sc_cx_entry = tk.Spinbox(row, from_=0, to=5000, width=6)
        self.sc_cx_entry.insert(0, "0")
        self.sc_cx_entry.pack(side="left", padx=(2, 6))
        
        tk.Label(row, text="Y:").pack(side="left")
        self.sc_cy_entry = tk.Spinbox(row, from_=0, to=5000, width=6)
        self.sc_cy_entry.insert(0, "0")
        self.sc_cy_entry.pack(side="left", padx=(2, 6))

        self.btn_crosshair2 = tk.Button(f3, text="Включить прицел для выбора X/Y", bg="#2196F3", fg="white", command=self.toggle_crosshair2)
        self.btn_crosshair2.pack(pady=4)

        row = tk.Frame(f3)
        row.pack(padx=4, pady=4)
        self.px_return_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="Возвращать мышь", variable=self.px_return_var).pack(side="left")
        tk.Label(row, text="Скорость:").pack(side="left", padx=(10, 2))
        self.px_speed_entry = tk.Entry(row, width=5)
        self.px_speed_entry.insert(0, "0.05")
        self.px_speed_entry.pack(side="left")

        tk.Button(self.tab_sc, text="Тест поиска сейчас", command=self.test_search_now).pack(pady=10)

    def build_kh_tab(self):
        self.kh_btn = tk.Button(self.tab_kh, text="▶ ЗАПИСЬ", font=("Arial", 14, "bold"), bg="#4CAF50", fg="white", command=self.kh_big_button)
        self.kh_btn.pack(fill="x", padx=8, pady=6)

        f = tk.LabelFrame(self.tab_kh, text="Запись")
        f.pack(fill="x", padx=8, pady=4)
        row = tk.Frame(f)
        row.pack(fill="x", padx=4, pady=4)
        tk.Label(row, text="Клавиша:").pack(side="left")
        self.kh_current_label = tk.Label(row, text="—", fg="blue", width=6)
        self.kh_current_label.pack(side="left")
        tk.Label(row, text="Сек:").pack(side="left", padx=(10, 2))
        self.kh_hold_entry = tk.Entry(row, width=5)
        self.kh_hold_entry.insert(0, "1.0")
        self.kh_hold_entry.pack(side="left")
        tk.Button(row, text="Добавить", bg="#2196F3", fg="white", command=self.kh_add_current).pack(side="left", padx=10)

        f = tk.LabelFrame(self.tab_kh, text="Метод")
        f.pack(fill="x", padx=8, pady=4)
        self.kh_method_var = tk.StringVar(value=self.kh_hold_method)
        tk.Radiobutton(f, text="pynput", variable=self.kh_method_var, value="pynput", command=self.kh_change_method).pack(side="left", padx=6)
        tk.Radiobutton(f, text="pydirectinput", variable=self.kh_method_var, value="pydirect", command=self.kh_change_method).pack(side="left", padx=6)

        f = tk.LabelFrame(self.tab_kh, text="Список")
        f.pack(fill="both", expand=True, padx=8, pady=4)
        lf = tk.Frame(f)
        lf.pack(fill="both", expand=True, padx=4, pady=4)
        sb = tk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.kh_listbox = tk.Listbox(lf, yscrollcommand=sb.set, font=("Consolas", 11), height=5)
        self.kh_listbox.pack(side="left", fill="both", expand=True)
        sb.config(command=self.kh_listbox.yview)

        row = tk.Frame(f)
        row.pack(fill="x", padx=4, pady=4)
        tk.Button(row, text="Удалить", command=self.kh_remove_selected).pack(side="left", padx=2)
        tk.Button(row, text="Очистить", command=self.kh_clear_all).pack(side="left", padx=2)
        tk.Button(row, text="↑", command=lambda: self.kh_move(-1)).pack(side="left", padx=2)
        tk.Button(row, text="↓", command=lambda: self.kh_move(1)).pack(side="left", padx=2)

        f = tk.LabelFrame(self.tab_kh, text="Пауза цикла (мс)")
        f.pack(fill="x", padx=8, pady=4)
        self.kh_cycle_delay_entry = tk.Entry(f, width=8)
        self.kh_cycle_delay_entry.insert(0, "0")
        self.kh_cycle_delay_entry.pack(padx=4, pady=4)
        self.kh_counter = tk.Label(self.tab_kh, text="Циклов: 0", font=("Arial", 11, "bold"))
        self.kh_counter.pack(pady=4)

    def ac_change_mode(self):
        self.ac_mode = self.ac_mode_var.get()
        if self.ac_mode == "smart":
            self.smart_frame.pack(fill="x", padx=8, pady=4, before=self.ac_counter)
        else:
            self.smart_frame.pack_forget()

    def update_search_mode(self):
        mode = self.search_mode_var.get()
        if mode == "hsv":
            self.hsv_frame.pack(padx=4, pady=2)
            self.px_tolerance_entry.config(state="disabled")
        else:
            self.hsv_frame.pack_forget()
            self.px_tolerance_entry.config(state="normal")

    def rgb_to_hsv(self, r, g, b):
        r, g, b = r / 255.0, g / 255.0, b / 255.0
        mx = max(r, g, b)
        mn = min(r, g, b)
        df = mx - mn
        
        if mx == mn:
            h = 0
        elif mx == r:
            h = (60 * ((g - b) / df) + 360) % 360
        elif mx == g:
            h = (60 * ((b - r) / df) + 120) % 360
        else:
            h = (60 * ((r - g) / df) + 240) % 360
            
        if mx == 0:
            s = 0
        else:
            s = (df / mx) * 100
            
        v = mx * 100
        
        return int(h / 2), int(s), int(v)

    def pick_color_from_screen(self):
        self.safe_status("Жди 5 секунд, наведи мышь на нужный цвет...", "orange")
        def _grab():
            time.sleep(5)
            x, y = pydirectinput.position()
            try:
                with mss.mss() as sct:
                    monitor = {"top": y, "left": x, "width": 1, "height": 1}
                    img = np.array(sct.grab(monitor))
                    b, g, r = img[0, 0][:3]
                
                color_hex = f"#{r:02x}{g:02x}{b:02x}"
                self.px_color_entry.delete(0, tk.END)
                self.px_color_entry.insert(0, color_hex)
                self.px_color_preview.config(bg=color_hex)
                
                h, s, v = self.rgb_to_hsv(r, g, b)
                
                self.hsv_min_entry.delete(0, tk.END)
                self.hsv_min_entry.insert(0, str(max(0, h-10)))
                self.hsv_max_entry.delete(0, tk.END)
                self.hsv_max_entry.insert(0, str(min(179, h+10)))
                
                self.safe_status(f"Взято: RGB({r},{g},{b}) | H={h} S={s} V={v}", "green")
            except Exception as e:
                self.safe_status("Ошибка: " + str(e), "red")
        threading.Thread(target=_grab, daemon=True).start()

    def toggle_crosshair(self):
        if self.ch_enable.get():
            if not self.crosshair_win:
                self.create_crosshair()
        else:
            if self.crosshair_win:
                self.crosshair_win.destroy()
                self.crosshair_win = None

    def create_crosshair(self):
        self.crosshair_win = tk.Toplevel(self.root)
        self.crosshair_win.overrideredirect(True)
        self.crosshair_win.attributes("-topmost", True)
        self.crosshair_win.attributes("-alpha", self.ch_alpha.get())
        
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.crosshair_win.geometry(f"{sw}x{sh}+0+0")
        
        self.crosshair_win.config(bg="#010101")
        self.crosshair_win.wm_attributes("-transparentcolor", "#010101")
        
        self.crosshair_canvas = tk.Canvas(self.crosshair_win, width=sw, height=sh, bg="#010101", highlightthickness=0)
        self.crosshair_canvas.pack()
        
        self.draw_crosshair()
        self.crosshair_canvas.bind("<Button-1>", self.start_move)
        self.crosshair_canvas.bind("<B1-Motion>", self.do_move)
        
        self.crosshair_center_x = sw // 2
        self.crosshair_center_y = sh // 2

    def draw_crosshair(self):
        if not self.crosshair_canvas: return
        self.crosshair_canvas.delete("all")
        cx = self.crosshair_center_x
        cy = self.crosshair_center_y
        size = self.ch_size.get()
        thick = max(2, size // 15)
        length = size // 2 - thick // 2
        
        self.crosshair_canvas.create_line(cx, cy-length, cx, cy+length, fill="black", width=thick, arrow="both")
        self.crosshair_canvas.create_line(cx-length, cy, cx+length, cy, fill="black", width=thick, arrow="both")

    def update_crosshair_ui(self, _=None):
        if self.crosshair_win:
            self.crosshair_win.attributes("-alpha", self.ch_alpha.get())
            self.draw_crosshair()

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        self.crosshair_center_x += deltax
        self.crosshair_center_y += deltay
        self.draw_crosshair()
        self.x = event.x
        self.y = event.y

    def toggle_crosshair2(self):
        if self.crosshair_win2:
            self.crosshair_win2.destroy()
            self.crosshair_win2 = None
            return
            
        self.crosshair_win2 = tk.Toplevel(self.root)
        self.crosshair_win2.overrideredirect(True)
        self.crosshair_win2.attributes("-topmost", True)
        self.crosshair_win2.attributes("-alpha", 0.6)
        
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.crosshair_win2.geometry(f"{sw}x{sh}+0+0")
        
        self.crosshair_win2.config(bg="#010101")
        self.crosshair_win2.wm_attributes("-transparentcolor", "#010101")
        
        self.crosshair_canvas2 = tk.Canvas(self.crosshair_win2, width=sw, height=sh, bg="#010101", highlightthickness=0)
        self.crosshair_canvas2.pack()
        
        self.crosshair_canvas2.bind("<Button-1>", self.start_move2)
        self.crosshair_canvas2.bind("<B1-Motion>", self.do_move2)
        
        self.crosshair_center_x2 = sw // 2
        self.crosshair_center_y2 = sh // 2
        self.draw_crosshair2()

    def draw_crosshair2(self):
        if not self.crosshair_canvas2: return
        self.crosshair_canvas2.delete("all")
        cx = self.crosshair_center_x2
        cy = self.crosshair_center_y2
        size = 200 
        thick = max(2, size // 15)
        length = size // 2 - thick // 2
        
        self.crosshair_canvas2.create_line(cx, cy-length, cx, cy+length, fill="blue", width=thick, arrow="both")
        self.crosshair_canvas2.create_line(cx-length, cy, cx+length, cy, fill="blue", width=thick, arrow="both")

    def start_move2(self, event):
        self.x2 = event.x
        self.y2 = event.y

    def do_move2(self, event):
        deltax = event.x - self.x2
        deltay = event.y - self.y2
        self.crosshair_center_x2 += deltax
        self.crosshair_center_y2 += deltay
        
        self.sc_cx_entry.delete(0, tk.END)
        self.sc_cx_entry.insert(0, str(int(self.crosshair_center_x2)))
        self.sc_cy_entry.delete(0, tk.END)
        self.sc_cy_entry.insert(0, str(int(self.crosshair_center_y2)))
        
        self.draw_crosshair2()
        self.x2 = event.x
        self.y2 = event.y

    def start_listener(self):
        def runner():
            while self.alive:
                try:
                    listener = keyboard.Listener(on_press=self.on_press)
                    listener.start()
                    self.listener = listener
                    listener.join()
                except Exception:
                    time.sleep(1)
        threading.Thread(target=runner, daemon=True).start()

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                merged = dict(DEFAULT_SETTINGS)
                merged.update(data)
                return merged
            except Exception: pass
        return dict(DEFAULT_SETTINGS)

    def save_settings(self):
        data = {
            "key_toggle": self.key_toggle, "key_instant": self.key_instant, "key_exit": self.key_exit,
            "ac_delay": self.ac_delay_entry.get(), "ac_unit": self.ac_unit_var.get(), "ac_button": self.ac_button_var.get(),
            "ac_check_delay": self.ac_check_delay_entry.get(),
            "kh_keys": self.kh_keys, "kh_cycle_delay": self.kh_cycle_delay_entry.get(), "kh_hold_method": self.kh_hold_method,
            "px_color": self.px_color_entry.get(), "px_tolerance": self.px_tolerance_entry.get(),
            "px_return": bool(self.px_return_var.get()), "px_move_speed": self.px_speed_entry.get(),
            "sc_left": self.sc_l_entry.get(), "sc_top": self.sc_t_entry.get(), "sc_right": self.sc_r_entry.get(), "sc_bottom": self.sc_b_entry.get(),
            "search_mode": self.search_mode_var.get(), "hsv_min_h": self.hsv_min_entry.get(), "hsv_max_h": self.hsv_max_entry.get()
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            self.safe_status("Сохранено в .vane!", "green")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def apply_settings(self):
        s = self.settings
        self.hk_labels["toggle"].config(text=self.key_toggle.upper())
        self.hk_labels["instant"].config(text=self.key_instant.upper())
        self.hk_labels["exit"].config(text=self.key_exit.upper())
        
        def set_e(e, val):
            e.delete(0, tk.END)
            e.insert(0, str(val))

        set_e(self.ac_delay_entry, s.get("ac_delay", "1"))
        self.ac_unit_var.set(s.get("ac_unit", "мс"))
        self.ac_button_var.set(s.get("ac_button", "left"))
        set_e(self.ac_check_delay_entry, s.get("ac_check_delay", "1000"))
        set_e(self.kh_hold_entry, "1.0")
        set_e(self.kh_cycle_delay_entry, s.get("kh_cycle_delay", "0"))
        self.kh_method_var.set(self.kh_hold_method)
        set_e(self.px_color_entry, s.get("px_color", "#ff0000"))
        set_e(self.px_tolerance_entry, s.get("px_tolerance", "10"))
        set_e(self.px_speed_entry, s.get("px_move_speed", "0.05"))
        self.px_return_var.set(bool(s.get("px_return", True)))
        
        set_e(self.sc_l_entry, s.get("sc_left", "0"))
        set_e(self.sc_t_entry, s.get("sc_top", "0"))
        set_e(self.sc_r_entry, s.get("sc_right", "500"))
        set_e(self.sc_b_entry, s.get("sc_bottom", "500"))
        set_e(self.sc_cx_entry, "0")
        set_e(self.sc_cy_entry, "0")
        
        self.search_mode_var.set(s.get("search_mode", "rgb"))
        set_e(self.hsv_min_entry, s.get("hsv_min_h", "0"))
        set_e(self.hsv_max_entry, s.get("hsv_max_h", "10"))
        self.update_search_mode()
        self.ac_change_mode()

        self.kh_refresh_listbox()
        self.ac_update_btn()
        self.kh_update_btn()
        self.update_color_preview()

    def sync_settings(self):
        if not self.alive: return
        try:
            self.ac_delay_sec = self.ac_get_delay_sec()
            self.ac_button = self.ac_button_var.get()
            try: self.ac_check_delay_sec = float(self.ac_check_delay_entry.get() or 1000) / 1000.0
            except: self.ac_check_delay_sec = 1.0
            self.kh_cycle_delay_ms = float(self.kh_cycle_delay_entry.get() or 0)
            self.kh_hold_method = self.kh_method_var.get()
            self.px_tolerance = int(float(self.px_tolerance_entry.get() or 0))
            self.px_return = bool(self.px_return_var.get())
            self.px_move_speed = float(self.px_speed_entry.get() or 0)
            self.px_color_rgb = self.parse_color(self.px_color_entry.get())
            
            self.sc_left = int(float(self.sc_l_entry.get() or 0))
            self.sc_top = int(float(self.sc_t_entry.get() or 0))
            self.sc_right = int(float(self.sc_r_entry.get() or 0))
            self.sc_bottom = int(float(self.sc_b_entry.get() or 0))
            
            self.search_mode = self.search_mode_var.get()
            self.hsv_min_h = int(float(self.hsv_min_entry.get() or 0))
            self.hsv_max_h = int(float(self.hsv_max_entry.get() or 0))
        except Exception: pass
        self.root.after(200, self.sync_settings)

    def safe_status(self, text, color="gray"):
        if not self.alive: return
        try: self.status.config(text=text, fg=color)
        except Exception: pass

    def parse_color(self, s):
        s = (s or "").strip()
        try:
            if s.startswith("#"): s = s[1:]
            if len(s) == 6: return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
            parts = [p.strip() for p in s.split(",")]
            if len(parts) == 3: return tuple(int(float(p)) for p in parts)
        except Exception: pass
        return (255, 0, 0)

    def update_color_preview(self):
        try:
            r, g, b = self.parse_color(self.px_color_entry.get())
            self.px_color_preview.config(bg=f"#{r:02x}{g:02x}{b:02x}")
        except Exception: pass

    def pick_color(self):
        try:
            color = colorchooser.askcolor(title="Выбери цвет")[1]
            if color:
                self.px_color_entry.delete(0, tk.END)
                self.px_color_entry.insert(0, color)
                self.update_color_preview()
        except Exception: pass

    def find_color_in_area(self, l, t, r, b, target, tol, mode, h_min, h_max):
        if not PIL_OK: return None
        try:
            with mss.mss() as sct:
                monitor = {"top": t, "left": l, "width": r-l, "height": b-t}
                img = np.array(sct.grab(monitor))
                # В mss цвета в формате BGRA, переводим в RGB
                img_rgb = img[:, :, :3][:, :, ::-1]
            
            if mode == "hsv":
                arr_float = img_rgb[:, :, :3].astype('float') / 255.0
                r_val = arr_float[:, :, 0]
                g_val = arr_float[:, :, 1]
                b_val = arr_float[:, :, 2]
                
                mx = np.max(arr_float, axis=2)
                mn = np.min(arr_float, axis=2)
                df = mx - mn
                
                h = np.zeros_like(mx)
                
                idx_r = (mx == r_val) & (df != 0)
                idx_g = (mx == g_val) & (df != 0)
                idx_b = (mx == b_val) & (df != 0)
                
                h[idx_r] = (60 * ((g_val[idx_r] - b_val[idx_r]) / df[idx_r]) + 360) % 360
                h[idx_g] = (60 * ((b_val[idx_g] - r_val[idx_g]) / df[idx_g]) + 120) % 360
                h[idx_b] = (60 * ((r_val[idx_b] - g_val[idx_b]) / df[idx_b]) + 240) % 360
                
                h = (h / 2).astype('uint8')
                
                if h_min <= h_max:
                    mask = (h >= h_min) & (h <= h_max)
                else:
                    mask = (h >= h_min) | (h <= h_max)
            else:
                tgt = np.array(target, dtype=int)
                tol_arr = np.array([tol, tol, tol], dtype=int)
                mask = np.all(np.abs(img_rgb - tgt) <= tol_arr, axis=-1)
            
            y_idxs, x_idxs = np.where(mask)
            if len(x_idxs) > 0:
                return (x_idxs[0] + l, y_idxs[0] + t)
        except Exception as e:
            print("Find error:", e)
        return None

    def test_search_now(self):
        if not PIL_OK:
            self.safe_status("mss/numpy не установлены", "red")
            return
        coords = self.find_color_in_area(self.sc_left, self.sc_top, self.sc_right, self.sc_bottom, self.px_color_rgb, self.px_tolerance, self.search_mode, self.hsv_min_h, self.hsv_max_h)
        if coords:
            self.safe_status("Найдено в X:" + str(coords[0]) + " Y:" + str(coords[1]), "green")
        else:
            self.safe_status("Цвет не найден", "orange")

    def smart_check_loop(self):
        # Этот цикл работает в отдельном потоке и не вешает игру
        while self.alive:
            if self.ac_mode == "smart" and self.ac_running and not self.ac_busy and PIL_OK:
                coords = self.find_color_in_area(self.sc_left, self.sc_top, self.sc_right, self.sc_bottom, self.px_color_rgb, self.px_tolerance, self.search_mode, self.hsv_min_h, self.hsv_max_h)
                if coords:
                    self.do_search_click(coords[0], coords[1])
            time.sleep(max(self.ac_check_delay_sec, 0.05))

    def do_search_click(self, find_x, find_y):
        self.ac_busy = True
        try:
            old_x, old_y = pydirectinput.position()
            
            if self.sc_click_mode.get() == "center":
                click_x, click_y = find_x, find_y
            else:
                click_x = int(float(self.sc_cx_entry.get() or 0))
                click_y = int(float(self.sc_cy_entry.get() or 0))

            self.move_to(click_x, click_y, self.px_move_speed)
            pydirectinput.mouseDown(button=self.ac_button)
            pydirectinput.mouseUp(button=self.ac_button)
            
            if self.px_return:
                self.move_to(old_x, old_y, self.px_move_speed)
                
            self.ac_clicks += 1
            self.px_hits += 1
            self.root.after(0, self.update_counter_label)
        except Exception: pass
        finally: self.ac_busy = False

    def move_to(self, x, y, duration):
        if duration <= 0:
            pydirectinput.moveTo(x, y)
            return
        sx, sy = pydirectinput.position()
        steps = max(int(duration * 60), 1)
        for i in range(1, steps + 1):
            t = i / steps
            nx = int(sx + (x - sx) * t)
            ny = int(sy + (y - sy) * t)
            pydirectinput.moveTo(nx, ny)
            time.sleep(duration / steps)

    def update_counter_label(self):
        if not self.alive: return
        try: self.ac_counter.config(text="Кликов: " + str(self.ac_clicks) + " | Попаданий: " + str(self.px_hits))
        except Exception: pass

    def start_capture(self, name):
        self.capture_for = name
        self.safe_status("Нажми клавишу для: " + name, "orange")

    def on_press(self, key):
        try:
            if self.capture_for:
                name = self.capture_for
                self.capture_for = None
                key_str = self.key_to_str(key)
                if key_str:
                    if name == "toggle": self.key_toggle = key_str
                    elif name == "instant": self.key_instant = key_str
                    elif name == "exit": self.key_exit = key_str
                    self.root.after(0, lambda: self._update_hotkey_label(name, key_str))
                return
            key_str = self.key_to_str(key)
            if not key_str: return
            if self.kh_state == "recording" and key_str not in (self.key_toggle, self.key_instant, self.key_exit):
                self.kh_recording_key = key_str
                self.root.after(0, lambda: self.kh_current_label.config(text=key_str.upper()))
                return
            if key_str == self.key_toggle: self.root.after(0, self._toggle_active_tab)
            elif key_str == self.key_instant: self.root.after(0, self.ac_instant_toggle)
            elif key_str == self.key_exit: self.root.after(0, self.on_close)
        except Exception: pass

    def _update_hotkey_label(self, name, key_str):
        if not self.alive: return
        self.hk_labels[name].config(text=key_str.upper())
        self.ac_update_btn()
        self.kh_update_btn()
        self.save_settings()

    def _toggle_active_tab(self):
        if not self.alive: return
        try:
            if self.notebook.index(self.notebook.select()) == 0 or self.notebook.index(self.notebook.select()) == 1:
                self.ac_toggle()
            else: self.kh_big_button()
        except Exception: pass

    def key_to_str(self, key):
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r): return "ctrl"
        if key in (keyboard.Key.shift_l, keyboard.Key.shift_r): return "shift"
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr): return "alt"
        if getattr(key, "name", "") in ("cmd", "cmd_l", "cmd_r"): return "win"
        name = getattr(key, "name", None)
        if name: return name.lower()
        try:
            if key.char: return key.char.lower()
        except Exception: pass
        return None

    def to_pynput_key(self, key_str):
        if len(key_str) == 1: return key_str
        k = getattr(keyboard.Key, key_str, None)
        if k is None:
            aliases = {"esc": "esc", "escape": "esc", "return": "enter"}
            k = getattr(keyboard.Key, aliases.get(key_str, key_str), None)
        if k is None: raise ValueError("Клавиша: " + key_str)
        return k

    def ac_toggle(self):
        self.ac_running = not self.ac_running
        self.ac_update_btn()

    def ac_instant_toggle(self):
        self.ac_running = not self.ac_running
        self.ac_update_btn()

    def ac_update_btn(self):
        if not self.alive: return
        try:
            if self.ac_running: self.ac_btn.config(text="⏸ СТОП", bg="#f44336")
            else: self.ac_btn.config(text="▶ СТАРТ", bg="#4CAF50")
        except Exception: pass

    def ac_turbo(self):
        self.ac_delay_entry.delete(0, tk.END)
        self.ac_delay_entry.insert(0, "0")
        self.ac_unit_var.set("мс")
        self.ac_delay_sec = 0
        if not self.ac_running: self.ac_running = True
        self.ac_update_btn()

    def ac_get_delay_sec(self):
        try: val = float(self.ac_delay_entry.get())
        except Exception: return 0.0
        u = self.ac_unit_var.get()
        if u == "мс": return val / 1000.0
        if u == "сек": return val
        if u == "мин": return val * 60.0
        if u == "час": return val * 3600.0
        return 0.0

    def ac_loop(self):
        while self.alive:
            if not self.ac_running or self.ac_mode == "search" or self.ac_busy:
                time.sleep(0.005)
                continue
            
            if self.ac_mode == "smart":
                if self.ac_busy:
                    time.sleep(0.005)
                    continue
                    
            target = time.perf_counter() + max(self.ac_delay_sec, 0)
            
            click_x, click_y = None, None
            
            if self.ch_enable.get():
                if self.crosshair_win:
                    click_x = self.crosshair_center_x
                    click_y = self.crosshair_center_y
            
            try:
                if self.ch_click_at_cross.get() and click_x is not None:
                    if self.ch_move_mouse_to_cross.get():
                        pydirectinput.moveTo(click_x, click_y)
                    pydirectinput.mouseDown(button=self.ac_button)
                    pydirectinput.mouseUp(button=self.ac_button)
                else:
                    pydirectinput.mouseDown(button=self.ac_button)
                    pydirectinput.mouseUp(button=self.ac_button)
            except Exception: pass
            
            self.ac_clicks += 1
            if self.ac_clicks % 50 == 0 and self.alive:
                try: self.root.after(0, self.update_counter_label)
                except Exception: pass
            remaining = target - time.perf_counter()
            if remaining > 0: time.sleep(remaining)

    def kh_big_button(self):
        if self.kh_state == "idle":
            self.kh_state = "recording"
            self.kh_recording_key = None
            self.kh_current_label.config(text="ждём…")
            self.safe_status("ЗАПИСЬ: нажми клавишу…", "orange")
        elif self.kh_state == "recording":
            self.kh_state = "stopped"
            self.kh_recording_key = None
            self.kh_current_label.config(text="—")
            self.safe_status("Запись стоп. Жми еще раз - ПОВТОР", "gray")
        elif self.kh_state == "stopped":
            if not self.kh_keys:
                self.safe_status("Список пуст", "red")
                return
            self.kh_state = "running"
            self.safe_status("Повтор ▶", "green")
        elif self.kh_state == "running":
            self.kh_state = "idle"
            self.safe_status("Остановлено", "gray")
        self.kh_update_btn()

    def kh_update_btn(self):
        if not self.alive: return
        try:
            k = self.key_toggle.upper()
            if self.kh_state == "idle": self.kh_btn.config(text="▶ ЗАПИСЬ (" + k + ")", bg="#4CAF50")
            elif self.kh_state == "recording": self.kh_btn.config(text="■ СТОП ЗАПИСИ (" + k + ")", bg="#f44336")
            elif self.kh_state == "stopped": self.kh_btn.config(text="▶ ПОВТОР (" + k + ")", bg="#2196F3")
            elif self.kh_state == "running": self.kh_btn.config(text="⏸ СТОП ПОВТОРА (" + k + ")", bg="#FF9800")
        except Exception: pass

    def kh_change_method(self):
        self.kh_hold_method = self.kh_method_var.get()
        self.save_settings()

    def kh_refresh_listbox(self):
        self.kh_listbox.delete(0, tk.END)
        for i, item in enumerate(self.kh_keys, 1):
            self.kh_listbox.insert(tk.END, str(i) + ". " + item['key'].upper() + " - " + str(item['hold']) + " сек")

    def kh_add_current(self):
        if not self.kh_recording_key:
            self.safe_status("Сначала нажми клавишу", "orange")
            return
        try:
            hold = float(self.kh_hold_entry.get())
            if hold < 0: raise ValueError
        except ValueError:
            self.safe_status("Неверное время", "red")
            return
        self.kh_keys.append({"key": self.kh_recording_key, "hold": hold})
        self.safe_status("Добавлено", "green")
        self.kh_recording_key = None
        self.kh_current_label.config(text="—")
        self.kh_refresh_listbox()
        self.save_settings()

    def kh_remove_selected(self):
        sel = self.kh_listbox.curselection()
        if not sel: return
        del self.kh_keys[sel[0]]
        self.kh_refresh_listbox()
        self.save_settings()

    def kh_clear_all(self):
        if messagebox.askyesno("Очистить", "Удалить все?"):
            self.kh_keys.clear()
            self.kh_refresh_listbox()
            self.save_settings()

    def kh_move(self, delta):
        sel = self.kh_listbox.curselection()
        if not sel: return
        idx = sel[0]
        new_idx = idx + delta
        if 0 <= new_idx < len(self.kh_keys):
            self.kh_keys[idx], self.kh_keys[new_idx] = self.kh_keys[new_idx], self.kh_keys[idx]
            self.kh_refresh_listbox()
            self.kh_listbox.select_set(new_idx)
            self.save_settings()

    def kh_press_key(self, key_str):
        if self.kh_hold_method == "pydirect":
            try:
                pydirectinput.keyDown(key_str)
                return
            except Exception: pass
        pk = self.to_pynput_key(key_str)
        keyboard.Controller().press(pk)

    def kh_release_key(self, key_str):
        if self.kh_hold_method == "pydirect":
            try:
                pydirectinput.keyUp(key_str)
                return
            except Exception: pass
        pk = self.to_pynput_key(key_str)
        keyboard.Controller().release(pk)

    def kh_loop(self):
        while self.alive:
            if self.kh_state != "running" or not self.kh_keys:
                time.sleep(0.01)
                continue
            try:
                for item in list(self.kh_keys):
                    if self.kh_state != "running" or not self.alive: break
                    key_str = item["key"]
                    hold_sec = float(item["hold"])
                    try:
                        self.kh_press_key(key_str)
                        end_time = time.perf_counter() + hold_sec
                        while time.perf_counter() < end_time:
                            if self.kh_state != "running" or not self.alive: break
                            time.sleep(0.005)
                    finally:
                        try: self.kh_release_key(key_str)
                        except Exception: pass
                if self.kh_cycle_delay_ms > 0:
                    end_time = time.perf_counter() + self.kh_cycle_delay_ms / 1000.0
                    while time.perf_counter() < end_time:
                        if self.kh_state != "running" or not self.alive: break
                        time.sleep(0.005)
                self.kh_cycle_count += 1
                if self.alive:
                    try: self.root.after(0, self.kh_update_counter)
                    except Exception: pass
            except Exception: time.sleep(0.1)

    def kh_update_counter(self):
        if not self.alive: return
        try: self.kh_counter.config(text="Циклов: " + str(self.kh_cycle_count))
        except Exception: pass

    def on_close(self):
        self.ac_running = False
        self.kh_state = "idle"
        self.alive = False
        try:
            if self.listener: self.listener.stop()
        except Exception: pass
        try: self.save_settings()
        except Exception: pass
        try: self.root.destroy()
        except Exception: pass

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = MultiClickerApp(root)
        root.mainloop()
    except Exception:
        traceback.print_exc()
        try: input("Enter...")
        except Exception: pass