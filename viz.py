#!/usr/bin/env python3
"""push_swap vizualisator.

Usage: ./viz.py [path of push_swap program]

The program can also be chosen from the GUI (Browse...). Its output
(the list of instructions) is displayed in the right-hand panel; the
instruction currently executed is highlighted and a double-click on a
line jumps to that point of the sort.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog

CANVAS_W, CANVAS_H = 800, 900
PANEL_W = 340
TIMEOUT = 12  # seconds allowed for the push_swap program
MAKE_TIMEOUT = 120  # seconds allowed for `make`
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".push_swap_viz.json")

KNOWN = {"sa", "sb", "ss", "pa", "pb", "ra", "rb", "rr", "rra", "rrb", "rrr"}


# --------------------------------------------------------------------------
# Stack operations (mutate the lists in place)
# --------------------------------------------------------------------------
def _swap(s):
    if len(s) >= 2:
        s[0], s[1] = s[1], s[0]


def _rot(s):
    if len(s) >= 2:
        s.append(s.pop(0))


def _rrot(s):
    if len(s) >= 2:
        s.insert(0, s.pop())


def apply_op(op, a, b):
    """Apply one push_swap instruction. Returns False if it is unknown."""
    if op not in KNOWN:
        return False
    if op == "pa":
        if b:
            a.insert(0, b.pop(0))
    elif op == "pb":
        if a:
            b.insert(0, a.pop(0))
    else:
        if op in ("sa", "ss"):
            _swap(a)
        if op in ("sb", "ss"):
            _swap(b)
        if op in ("ra", "rr"):
            _rot(a)
        if op in ("rb", "rr"):
            _rot(b)
        if op in ("rra", "rrr"):
            _rrot(a)
        if op in ("rrb", "rrr"):
            _rrot(b)
    return True


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_numbers(text):
    """Integers separated by spaces, ',' or ';' -> (numbers, error message)."""
    numbers = []
    for tok in text.replace(",", " ").replace(";", " ").split():
        try:
            if "_" in tok:
                raise ValueError
            numbers.append(int(tok))
        except ValueError:
            return None, f"not an integer: {tok[:20]}"
    if len(numbers) < 2:
        return None, "need at least 2 numbers"
    if len(set(numbers)) != len(numbers):
        return None, "duplicate numbers are not allowed"
    return numbers, None


def color_for(t):
    """Map t in [0, 1] to a blue -> green -> yellow -> red-ish gradient."""
    r = 255 * (t - 0.3) * (t > 0.3)
    g = 255 * t - 510 * (t - 0.6) * (t > 0.6)
    b = (255 - 510 * t) * (t < 0.5)
    clamp = lambda x: max(0, min(255, int(x)))
    return "#%02x%02x%02x" % (clamp(r), clamp(g), clamp(b))


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class Visualizer:
    def __init__(self, root, program=""):
        self.root = root
        root.title("push_swap vizualisator")
        root.geometry(f"{CANVAS_W + PANEL_W}x{CANVAS_H}")
        root.resizable(True, True)
        root.minsize(PANEL_W + 300, 400)

        self.backup = []      # initial numbers
        self.a = []
        self.b = []
        self.moves = []       # instructions printed by the program
        self.index = 0        # number of instructions already applied
        self.loaded = False   # True when `moves` come from a successful run
        self.playing = False
        self.after_id = None

        cfg = load_config()
        program = program or str(cfg.get("program", ""))
        try:
            delay = int(cfg.get("delay", 50))
        except (TypeError, ValueError):
            delay = 50

        self.prog_var = tk.StringVar(value=program)
        self.size_var = tk.StringVar(value=str(cfg.get("size", 30)))
        self.min_var = tk.StringVar(value=str(cfg.get("min", -5000)))
        self.max_var = tk.StringVar(value=str(cfg.get("max", 5000)))
        self.delay_var = tk.IntVar(value=delay)
        self.count_var = tk.StringVar()
        self.result_var = tk.StringVar()
        self.error_var = tk.StringVar()

        self._build_ui()
        self.prog_var.trace_add("write", lambda *a: self.update_buttons())
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # reuse the numbers of the last session if they are valid
        saved = cfg.get("numbers")
        if (isinstance(saved, list) and len(saved) >= 2
                and all(type(n) is int for n in saved)
                and len(set(saved)) == len(saved)):
            self.backup = saved
            self.seek(0)
            self.show_numbers()
        else:
            self.generate()
        self.update_buttons()
        self.root.after(1000, self._poll)

    # ---- layout ----------------------------------------------------------
    def _build_ui(self):
        # the side panel keeps a fixed width, the canvas takes the rest.
        # It is scrollable: all controls live in `panel`, inside a canvas.
        outer = tk.Frame(self.root, width=PANEL_W)
        outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=4)
        outer.pack_propagate(False)
        self.panel_vsb = tk.Scrollbar(outer, orient=tk.VERTICAL)
        self.panel_canvas = tk.Canvas(outer, width=1, height=1, highlightthickness=0,
                                      yscrollincrement=20,
                                      yscrollcommand=self.panel_vsb.set)
        self.panel_vsb.config(command=self.panel_canvas.yview)
        self.panel_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._vsb_shown = False
        panel = tk.Frame(self.panel_canvas)
        self.panel = panel
        self._panel_win = self.panel_canvas.create_window((0, 0), window=panel,
                                                          anchor="nw")
        panel.bind("<Configure>", self._fit_panel)
        self.panel_canvas.bind("<Configure>", self._fit_panel)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._on_wheel)

        # one framed canvas per stack, sharing the width equally
        stacks = tk.Frame(self.root)
        stacks.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=4)
        stacks.grid_rowconfigure(0, weight=1)
        self.canvases = []
        for col, name in enumerate(("stack a", "stack b")):
            stacks.grid_columnconfigure(col, weight=1, uniform="stacks")
            lf = tk.LabelFrame(stacks, text=name)
            lf.grid(row=0, column=col, sticky="nsew", padx=2)
            c = tk.Canvas(lf, bg="black", width=CANVAS_W // 2, height=CANVAS_H,
                          highlightthickness=0)
            c.pack(fill=tk.BOTH, expand=True)
            c.bind("<Configure>", lambda e: self.redraw())
            self.canvases.append(c)

        # program chooser
        f = tk.LabelFrame(panel, text="Program")
        f.pack(fill=tk.X, pady=2)
        entry = tk.Entry(f, textvariable=self.prog_var)
        entry.pack(fill=tk.X, padx=4, pady=(4, 2))
        entry.bind("<Return>", lambda e: self.rerun())
        row = tk.Frame(f)
        row.pack(fill=tk.X, padx=4, pady=(0, 4))
        tk.Button(row, text="Browse...", command=self.browse).pack(side=tk.LEFT)
        self.rerun_btn = tk.Button(row, text="Run program", command=self.rerun)
        self.rerun_btn.pack(side=tk.LEFT, padx=4)
        self.make_btn = tk.Button(row, text="Make & run",
                                  command=self.make_and_run)
        self.make_btn.pack(side=tk.LEFT)

        # numbers
        f = tk.LabelFrame(panel, text="Numbers")
        f.pack(fill=tk.X, pady=2)
        for r, (label, var) in enumerate((("size", self.size_var),
                                          ("min", self.min_var),
                                          ("max", self.max_var))):
            tk.Label(f, text=label, width=6, anchor="w").grid(row=r, column=0, padx=4)
            tk.Entry(f, textvariable=var, width=10).grid(row=r, column=1, pady=1)
        tk.Button(f, text="Generate", width=12, command=self.generate).grid(
            row=0, column=2, padx=10, pady=1)
        self.gen_run_btn = tk.Button(f, text="Generate & Run", width=12,
                                     command=self.generate_and_run)
        self.gen_run_btn.grid(row=1, column=2, padx=10, pady=1)
        tk.Label(f, text="Numbers (paste or edit; separators: space , ;)",
                 anchor="w").grid(row=3, column=0, columnspan=3, sticky="w",
                                  padx=4, pady=(4, 0))
        box = tk.Frame(f)
        box.grid(row=4, column=0, columnspan=3, sticky="ew", padx=4)
        nsb = tk.Scrollbar(box)
        nsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.nums_text = tk.Text(box, width=1, height=4, wrap="word",
                                 font=("Courier", 9), yscrollcommand=nsb.set)
        self.nums_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        nsb.config(command=self.nums_text.yview)
        self.nums_text.bind("<Control-Return>",
                            lambda e: (self.apply_numbers(), "break")[1])
        row = tk.Frame(f)
        row.grid(row=5, column=0, columnspan=3, sticky="w", padx=4, pady=(2, 4))
        tk.Button(row, text="Apply numbers", command=self.apply_numbers).pack(side=tk.LEFT)
        copy_btn = tk.Button(row, text="Copy numbers")
        copy_btn.config(command=lambda: self.copy(
            self.nums_text.get("1.0", "end").strip(), copy_btn, "Copy numbers"))
        copy_btn.pack(side=tk.LEFT, padx=4)

        # playback
        f = tk.LabelFrame(panel, text="Playback")
        f.pack(fill=tk.X, pady=2)
        row = tk.Frame(f)
        row.pack(pady=2)
        self.run_btn = tk.Button(row, text="Play", width=6, command=self.toggle_play)
        self.play_buttons = [
            tk.Button(row, text="< Step", width=6, command=self.step_back),
            self.run_btn,
            tk.Button(row, text="Step >", width=6, command=self.step_forward),
            tk.Button(row, text="Restart", width=6, command=self.restart),
        ]
        for b in self.play_buttons:
            b.pack(side=tk.LEFT)
        self._slider_guard = False
        self._slider_max = 1
        self.slider = tk.Scale(f, from_=0, to=1, orient=tk.HORIZONTAL,
                               label="Position in command list",
                               command=self.on_slide, state=tk.DISABLED)
        self.slider.pack(fill=tk.X, padx=4)
        tk.Scale(f, from_=0, to=500, orient=tk.HORIZONTAL, variable=self.delay_var,
                 label="Delay between moves (ms)").pack(fill=tk.X, padx=4)

        # manual operations
        f = tk.LabelFrame(panel, text="Manual operations")
        f.pack(fill=tk.X, pady=2)
        layout = (("sa", "ra", "rra", "pa"),
                  ("sb", "rb", "rrb", "pb"),
                  ("ss", "rr", "rrr", None))
        for r, ops_row in enumerate(layout):
            for c, op in enumerate(ops_row):
                if op:
                    tk.Button(f, text=op, width=6,
                              command=lambda o=op: self.manual(o)).grid(
                        row=r, column=c, padx=2, pady=1)

        # status
        self.status_row = tk.Frame(panel)
        self.status_row.pack(fill=tk.X)
        tk.Label(self.status_row, textvariable=self.count_var).pack(side=tk.LEFT)
        self.result_lbl = tk.Label(self.status_row, textvariable=self.result_var,
                                   font=("TkDefaultFont", 10, "bold"))
        self.result_lbl.pack(side=tk.RIGHT)
        # the error line only takes room while there is an error to show
        self.err_lbl = tk.Label(panel, textvariable=self.error_var, fg="red",
                                wraplength=PANEL_W - 40, justify="left")
        self.error_var.trace_add("write", self._toggle_error)

        # output of the program
        f = tk.LabelFrame(panel, text="Program output (double-click = jump)")
        f.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=2)
        sb = tk.Scrollbar(f)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        # small requested size: the frame takes whatever space is left in the
        # panel, and the panel scrolls when even this minimum does not fit
        self.out = tk.Text(f, width=1, height=8, font=("Courier", 10),
                           state="disabled", yscrollcommand=sb.set, wrap="none",
                           cursor="arrow")
        self.out.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self.out.yview)
        self.out.tag_config("err", foreground="red")
        self.out.tag_config("cur", background="#ffd54a", foreground="black")
        self.out.tag_raise("cur")
        self.out.bind("<Double-Button-1>", self.on_output_click)

    # ---- scrollable panel ---------------------------------------------------
    def _fit_panel(self, event=None):
        c = self.panel_canvas
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw <= 1 or ch <= 1:
            return
        req_h = self.panel.winfo_reqheight()
        height = max(req_h, ch)  # taller window: the output list takes the rest
        c.itemconfig(self._panel_win, width=cw, height=height)
        c.configure(scrollregion=(0, 0, cw, height))
        need = req_h > ch
        if need != self._vsb_shown:
            self._vsb_shown = need
            if need:
                self.panel_vsb.pack(side=tk.RIGHT, fill=tk.Y, before=c)
            else:
                self.panel_vsb.pack_forget()

    def _on_wheel(self, event):
        w = self.root.winfo_containing(event.x_root, event.y_root)
        if w is None or isinstance(w, (tk.Text, tk.Scale)):
            return  # those widgets handle the wheel themselves
        while w is not None and w is not self.panel:
            w = w.master
        if w is None:
            return  # pointer is not over the right-hand panel
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.panel_canvas.yview_scroll(step, "units")

    # ---- program handling ---------------------------------------------------
    def program_ok(self):
        """True if the selected program exists and can be launched."""
        prog = self.prog_var.get().strip()
        if not prog:
            return False
        if prog.endswith(".py"):
            return os.path.isfile(prog)
        return shutil.which(prog) is not None

    def make_dir(self):
        """Folder of the program if it contains a Makefile, else None."""
        prog = self.prog_var.get().strip()
        if not prog:
            return None
        folder = os.path.dirname(os.path.abspath(prog))
        for name in ("Makefile", "makefile", "GNUmakefile"):
            if os.path.isfile(os.path.join(folder, name)):
                return folder
        return None

    def make_and_run(self):
        """Run `make -C <program folder>`, then run the program."""
        folder = self.make_dir()
        if folder is None:
            return
        self.pause()
        self.error_var.set(f"Running make -C {folder} ...")
        self.root.update_idletasks()
        try:
            res = subprocess.run(["make", "-C", folder, 're'], capture_output=True,
                                 text=True, timeout=MAKE_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.error_var.set(f"make timed out after {MAKE_TIMEOUT}s")
            return
        except OSError as e:
            self.error_var.set(f"Cannot run make: {e}")
            return
        if res.returncode != 0:
            self.moves = []
            self.loaded = False
            self.fill_output((res.stdout + res.stderr).strip(), "make output")
            self.seek(0)
            self.error_var.set(f"make failed (exit code {res.returncode})")
            self.update_buttons()
            return
        self.error_var.set("")
        self.rerun()

    def update_buttons(self):
        ok = self.program_ok()
        # "Run program" needs a valid program; playback also needs its output
        run_state = tk.NORMAL if ok else tk.DISABLED
        play_state = tk.NORMAL if ok and self.loaded else tk.DISABLED
        self.rerun_btn.config(state=run_state)
        self.gen_run_btn.config(state=run_state)
        self.make_btn.config(state=tk.NORMAL if self.make_dir() else tk.DISABLED)
        for b in self.play_buttons:
            b.config(state=play_state)
        self.slider.config(state=play_state)
        if play_state == tk.DISABLED and self.playing:
            self.pause()

    def _poll(self):
        # the program file may be (re)built or removed while the app is open
        self.update_buttons()
        self.root.after(1000, self._poll)

    def browse(self):
        path = filedialog.askopenfilename(title="Choose the push_swap program")
        if path:
            self.prog_var.set(path)
            self.rerun()

    def rerun(self):
        """(Re)run the selected program on the numbers of the numbers field."""
        self.pause()
        if not self.sync_numbers_from_field():
            return
        if self.backup:
            self.run_program()
            self.seek(0)
            self.save_config()

    def run_program(self):
        self.moves = []
        self.loaded = False
        self.error_var.set("")
        stderr = ""
        prog = self.prog_var.get().strip()
        if not prog:
            self.error_var.set("No program selected: use Browse... to choose one.")
        elif not self.program_ok():
            self.error_var.set(f"Program not found or not executable: {prog}")
        else:
            cmd = [sys.executable, prog] if prog.endswith(".py") else [prog]
            try:
                res = subprocess.run(cmd + [str(n) for n in self.backup],
                                     capture_output=True, text=True, timeout=TIMEOUT)
                self.moves = [l.strip() for l in res.stdout.splitlines() if l.strip()]
                stderr = res.stderr
                self.loaded = True
                if res.returncode != 0:
                    self.error_var.set(f"Program exited with code {res.returncode}")
            except subprocess.TimeoutExpired:
                self.error_var.set(f"Program timed out after {TIMEOUT}s")
            except OSError as e:
                self.error_var.set(f"Cannot run program: {e}")
        self.fill_output(stderr)
        self.update_buttons()

    def fill_output(self, stderr="", title="stderr"):
        t = self.out
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "\n".join(self.moves))
        for i, m in enumerate(self.moves, 1):
            if m not in KNOWN:
                t.tag_add("err", f"{i}.0", f"{i}.end")
        if stderr.strip():
            prefix = "\n" if self.moves else ""
            t.insert("end", f"{prefix}--- {title} ---\n{stderr.strip()}", "err")
        t.config(state="disabled")

    # ---- numbers --------------------------------------------------------------
    def generate(self):
        self.pause()
        try:
            size = int(self.size_var.get())
            lo = int(self.min_var.get())
            hi = int(self.max_var.get())
        except ValueError:
            self.error_var.set("size, min and max must be integers")
            return False
        if size < 2 or hi <= lo:
            self.error_var.set("need: size > 1 and max > min")
            return False
        if size > hi - lo + 1:
            self.error_var.set("range too small for that many distinct numbers")
            return False
        self.error_var.set("")
        self.set_numbers(random.sample(range(lo, hi + 1), size))
        return True

    def set_numbers(self, numbers):
        """Use these numbers; the previous program output is forgotten until
        "Run program" is pressed again."""
        self.pause()
        self.backup = list(numbers)
        self.moves = []
        self.loaded = False
        self.error_var.set("")
        self.fill_output()
        self.seek(0)
        self.show_numbers()
        self.update_buttons()
        self.save_config()

    def show_numbers(self):
        """Display the current numbers in the numbers field."""
        self.nums_text.delete("1.0", "end")
        self.nums_text.insert("end", self.numbers_string())

    def sync_numbers_from_field(self):
        """Use the numbers typed or pasted in the field. False if invalid."""
        numbers, err = parse_numbers(self.nums_text.get("1.0", "end"))
        if err:
            self.error_var.set(err)
            return False
        if numbers != self.backup:
            self.set_numbers(numbers)
        return True

    def apply_numbers(self):
        self.pause()
        self.sync_numbers_from_field()

    def generate_and_run(self):
        """Generate new numbers, then run the program on them."""
        if self.generate():
            self.rerun()

    # ---- numbers string / clipboard ---------------------------------------
    def numbers_string(self):
        return " ".join(str(n) for n in self.backup)

    def copy(self, text, button, label):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # keep the clipboard content after the app closes
        button.config(text="Copied!")
        self.root.after(1500, lambda: button.config(text=label))

    def _toggle_error(self, *args):
        if self.error_var.get():
            self.err_lbl.pack(anchor="w", after=self.status_row)
        else:
            self.err_lbl.pack_forget()

    # ---- saved settings ------------------------------------------------------
    def save_config(self):
        data = {
            "program": self.prog_var.get().strip(),
            "size": self.size_var.get(),
            "min": self.min_var.get(),
            "max": self.max_var.get(),
            "delay": self.delay_var.get(),
            "numbers": self.backup,
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def on_close(self):
        self.save_config()
        self.root.destroy()

    # ---- playback ---------------------------------------------------------------
    def seek(self, n):
        """Put the stacks in the state they have after n instructions."""
        self.a = list(self.backup)
        self.b = []
        n = max(0, min(n, len(self.moves)))
        for op in self.moves[:n]:
            apply_op(op, self.a, self.b)
        self.index = n
        self.refresh()

    def goto(self, n):
        """Move to the state after n instructions (replays only if going back)."""
        n = max(0, min(n, len(self.moves)))
        if n < self.index:
            self.seek(n)
            return
        while self.index < n:
            self.advance()
        self.refresh()

    def on_slide(self, value):
        if self._slider_guard or not self.loaded:
            return
        n = int(float(value))
        if n != self.index:
            self.pause()
            self.goto(n)

    def sync_slider(self):
        top = max(len(self.moves), 1)
        state = str(self.slider.cget("state"))
        self._slider_guard = True
        try:
            self.slider.config(state=tk.NORMAL)  # a disabled scale ignores set()
            if top != self._slider_max:
                self.slider.config(to=top)
                self._slider_max = top
            self.slider.set(self.index)
            self.slider.config(state=state)
        finally:
            self._slider_guard = False

    def advance(self):
        """Apply the next instruction without redrawing."""
        if self.index < len(self.moves):
            apply_op(self.moves[self.index], self.a, self.b)
            self.index += 1

    def step_forward(self):
        self.pause()
        self.advance()
        self.refresh()

    def step_back(self):
        self.pause()
        self.seek(self.index - 1)

    def restart(self):
        self.pause()
        self.seek(0)

    def toggle_play(self):
        if self.playing:
            self.pause()
            return
        if not self.moves:
            return
        if self.index >= len(self.moves):
            self.seek(0)
        self.playing = True
        self.run_btn.config(text="Pause")
        self.tick()

    def pause(self):
        self.playing = False
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.run_btn.config(text="Play")

    def tick(self):
        if not self.playing:
            return
        delay = self.delay_var.get()
        for _ in range(1 if delay >= 5 else 10):  # batch moves when very fast
            self.advance()
        self.refresh()
        if self.index >= len(self.moves):
            self.pause()
            return
        self.after_id = self.root.after(max(1, delay), self.tick)

    def manual(self, op):
        self.pause()
        apply_op(op, self.a, self.b)
        self.refresh()

    def on_output_click(self, event):
        if not (self.loaded and self.program_ok()):
            return
        line = int(self.out.index(f"@{event.x},{event.y}").split(".")[0])
        if 1 <= line <= len(self.moves):
            self.pause()
            self.seek(line)

    # ---- drawing ------------------------------------------------------------------
    def refresh(self):
        self.redraw()
        self.out.tag_remove("cur", "1.0", "end")
        if 1 <= self.index <= len(self.moves):
            self.out.tag_add("cur", f"{self.index}.0", f"{self.index}.end")
            self.out.see(f"{self.index}.0")
        self.count_var.set(f"move {self.index} / {len(self.moves)}")
        self.sync_slider()
        self.update_result()

    def update_result(self):
        if self.loaded and self.index == len(self.moves):
            ok = not self.b and self.a == sorted(self.backup)
            self.result_var.set("SORTED" if ok else "NOT SORTED")
            self.result_lbl.config(fg="green" if ok else "red")
        else:
            self.result_var.set("")

    def redraw(self):
        n = len(self.backup)
        lo, hi = (min(self.backup), max(self.backup)) if n else (0, 1)
        span = (hi - lo) or 1
        for c, stack in zip(self.canvases, (self.a, self.b)):
            c.delete("all")
            if n == 0:
                continue
            cw, ch = c.winfo_width(), c.winfo_height()
            if cw <= 1 or ch <= 1:  # window not mapped yet
                cw, ch = CANVAS_W // 2, CANVAS_H
            max_bar = max(cw - 12, 1)
            h = ch / n
            for i, v in enumerate(stack):
                t = (v - lo) / span
                c.create_rectangle(0, i * h, 10 + max_bar * t, (i + 1) * h,
                                   fill=color_for(t), outline="")


def main():
    program = sys.argv[1] if len(sys.argv) > 1 else ""
    root = tk.Tk()
    Visualizer(root, program)
    root.mainloop()


if __name__ == "__main__":
    main()