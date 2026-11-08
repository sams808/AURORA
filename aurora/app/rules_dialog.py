"""Reduce > Rules…: batch settings. An ordered list of rules with enable ticks, an editor (scope, scan filter, the
fields a rule writes) and a live table of every field that would change (old -> new). 'Apply now' writes the enabled
rules into the existing settings in one session edit; 'Save as default' stores them in the session ('rules' block),
so the zone x scan settings created later (a stack that arrives tomorrow) inherit them. Fields set by hand on a scan
card are pinned and kept unless 'override pinned fields' is ticked. The rules themselves live in aurora.core.rules.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from aurora.app.help_text import explain, explain_all, explain_buttons, header_tips, labelled
from aurora.app.vocab import DET_LABELS, NORM_LABELS, plural, unit_suffix
from aurora.app.widgets import ElidedLabel, FlowLayout
from aurora.core import rules as rl
from aurora.core.constants import DETS
from aurora.core.names import short_time, split_stack_id

SCOPES = (("project", "the whole project"), ("holders", "these holders"), ("groups", "these groups"),
          ("zones", "the selected zone"))
FLAG_KINDS = (("sdd_saturated", "SDD saturated"), ("i0_invalid", "I0 invalid"), ("high_rate", "high count rate"))
TABLE_HEAD = ("Holder", "Zone", "Scan", "Field", "Now", "After")


def parse_windows(text: str):
    """'60:74' (all four SDDs) or 'sdd1 60:74, sdd2 61:75, ...' -> windows; '' -> None (the line's fixed bins)."""
    t = (text or "").strip()
    if not t:
        return None
    pairs = re.findall(r"(?:(sdd\s*\d)\s*)?(\d+)\s*[:\-]\s*(\d+)", t, re.I)
    if not pairs:
        raise ValueError(f"{text!r}: write '60:74' or 'sdd1 60:74, sdd2 61:75, ...'")
    if len(pairs) == 1 and not pairs[0][0]:
        return {d: [int(pairs[0][1]), int(pairs[0][2])] for d in DETS}
    out = {}
    for det, lo, hi in pairs:
        if not det:
            raise ValueError(f"{text!r}: name each detector ('sdd1 60:74, ...')")
        out[det.replace(" ", "").lower()] = [int(lo), int(hi)]
    return out


def windows_text(roi) -> str:
    if not roi:
        return ""
    from aurora.core import xrf_windows as xw
    w = xw.normalise_windows(roi)
    if xw.same_for_all(w):
        return f"{w[DETS[0]][0]}:{w[DETS[0]][1]}"
    return ", ".join(f"{d} {w[d][0]}:{w[d][1]}" for d in DETS)


class RulesDialog(QDialog):
    def __init__(self, session, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings rules")
        self.setObjectName("rules_dialog")
        self.resize(1100, 720)
        self.session = session
        self.rules: list[rl.Rule] = rl.rules_from_json(session.zones.rules)
        self._loading = False
        root = QVBoxLayout(self)
        intro = QLabel("Rules write the same settings into many zone x scan cards. They run in this order; a later rule "
                       "wins. Fields set by hand on a card are kept unless 'override pinned fields' is ticked.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        root.addWidget(intro)
        split = QSplitter(Qt.Horizontal)
        # ---- the ordered list
        left = QWidget()
        ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0)
        self.rule_list = QListWidget()
        self.rule_list.currentRowChanged.connect(self._load_rule)
        self.rule_list.itemChanged.connect(self._ticked)
        ll.addWidget(self.rule_list, 1)
        bl = FlowLayout(spacing=6)
        lb = QWidget(); lb.setLayout(bl)
        self.add_btn = QPushButton("Add"); self.add_btn.clicked.connect(self.add_rule)
        self.remove_btn = QPushButton("Remove"); self.remove_btn.clicked.connect(self.remove_rule)
        self.up_btn = QPushButton("Up"); self.up_btn.clicked.connect(lambda: self.move_rule(-1))
        self.down_btn = QPushButton("Down"); self.down_btn.clicked.connect(lambda: self.move_rule(1))
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn):
            bl.addWidget(b)
        ll.addWidget(lb)
        split.addWidget(left)
        # ---- the editor
        ed = QWidget()
        form = QFormLayout(ed)
        self.name = QLineEdit()
        form.addRow(labelled("name", self.name, "reduce.rules.name"), self.name)
        self.scope = QComboBox()
        for k, text in SCOPES:
            self.scope.addItem(text, k)
        self.scope_text = QLineEdit(); self.scope_text.setPlaceholderText("N5, N6  or  Minerals, Simple Glass")
        sr = QHBoxLayout(); sr.addWidget(self.scope); sr.addWidget(self.scope_text, 1)
        form.addRow(labelled("applies to", self.scope, "reduce.rules.scope"), sr)
        explain(self.scope_text, "reduce.rules.scope_text")
        self.edge = QLineEdit(); self.edge.setPlaceholderText("F K")
        form.addRow(labelled("edge", self.edge, "reduce.rules.edge"), self.edge)
        self.plan = QLineEdit(); self.plan.setPlaceholderText("_TEY  (a regular expression)")
        form.addRow(labelled("scan name", self.plan, "reduce.rules.plan"), self.plan)
        self.span_lo = QDoubleSpinBox(); self.span_hi = QDoubleSpinBox()
        for s in (self.span_lo, self.span_hi):
            s.setRange(0, 100000); s.setDecimals(1); s.setSuffix(unit_suffix("eV")); s.setSpecialValueText("any")
        spr = QHBoxLayout(); spr.addWidget(self.span_lo); spr.addWidget(QLabel("to")); spr.addWidget(self.span_hi)
        form.addRow(labelled("energies within", self.span_lo, "reduce.rules.span"), spr)
        explain(self.span_hi, "reduce.rules.span")
        self.flag_boxes = {}
        fr = QHBoxLayout()
        for k, text in FLAG_KINDS:
            c = QCheckBox(text); fr.addWidget(c); self.flag_boxes[k] = c
            explain(c, "reduce.rules.flags")
        form.addRow(QLabel("flagged"), fr)
        # the fields a rule writes: a tick per field and its value
        grid = QGridLayout()
        self.use: dict[str, QCheckBox] = {}
        self.val: dict[str, QWidget] = {}
        self.inc = QCheckBox("included")
        self.sdd_boxes = [QCheckBox(d) for d in DET_LABELS]
        sddw = QWidget(); sl = QHBoxLayout(sddw); sl.setContentsMargins(0, 0, 0, 0)
        for c in self.sdd_boxes:
            sl.addWidget(c)
        self.line = QComboBox(); self.line.setEditable(True)
        self.line.addItems(self._known_lines())
        self.fn = QComboBox(); self.tn = QComboBox()
        for box in (self.fn, self.tn):
            for code, words in NORM_LABELS.items():
                box.addItem(words, code)
            box.setCurrentIndex(list(NORM_LABELS).index("i0jump"))
        self.tey = QCheckBox("on")
        self.ds = QCheckBox("on")
        self.k = QDoubleSpinBox(); self.k.setRange(1, 50); self.k.setValue(6.0); self.k.setSuffix(unit_suffix("MAD"))
        self.i0src = QComboBox(); self.i0src.addItem("own", "own"); self.i0src.addItem("good-I0 mean", "good")
        self.pre = QLineEdit(); self.pre.setPlaceholderText("auto, 680-684 or E0-15..E0-5")
        self.post = QLineEdit(); self.post.setPlaceholderText("auto, 700-720 or E0+15..E0+35")
        self.roi = QLineEdit(); self.roi.setPlaceholderText("60:74  or  sdd1 60:74, sdd2 61:75, …  (blank: fixed bins)")
        self.sig = QComboBox(); self.sig.addItem("PFY", "pfy"); self.sig.addItem("IPFY", "ipfy")
        rows = [("inc", self.inc), ("sdd", sddw), ("line", self.line), ("roi", self.roi), ("fn", self.fn),
                ("tey", self.tey), ("tn", self.tn), ("ds", self.ds), ("k", self.k), ("i0src", self.i0src),
                ("pre", self.pre), ("post", self.post), ("sig", self.sig)]
        for r, (key, w) in enumerate(rows):
            c = QCheckBox(rl.FIELD_LABELS[key])
            explain(c, "reduce.rules.field")
            c.setAccessibleName(f"the rule writes the {rl.FIELD_LABELS[key]}")
            grid.addWidget(c, r, 0)
            grid.addWidget(w, r, 1)
            self.use[key] = c
            self.val[key] = w
            c.toggled.connect(lambda on, ww=w: ww.setEnabled(on))
            w.setEnabled(False)
        for key, w in rows:
            if key == "sdd":
                continue
            w.setAccessibleName(f"{rl.FIELD_LABELS[key]}, the value written")
            w.setToolTip(f"The value the rule writes for the {rl.FIELD_LABELS[key]}")
        for c, d in zip(self.sdd_boxes, DET_LABELS):
            c.setAccessibleName(f"{d} in the sum, value")
            c.setToolTip(f"{d} ticked or not in the fluorescence sum")
        gw = QWidget(); gw.setLayout(grid)
        form.addRow(QLabel("the rule writes"), gw)
        self.override = QCheckBox("override pinned fields (set by hand on a card)")
        form.addRow(self.override)
        self.problem = ElidedLabel("", Qt.ElideRight)
        self.problem.setProperty("tone", "warn")
        form.addRow(self.problem)
        split.addWidget(ed)
        split.setSizes([260, 820])
        root.addWidget(split, 3)
        # ---- what would change
        self.count = ElidedLabel("", Qt.ElideRight)
        root.addWidget(self.count)
        self.table = QTableWidget(0, len(TABLE_HEAD))
        self.table.setHorizontalHeaderLabels(list(TABLE_HEAD))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAccessibleName("fields the enabled rules would change")
        header_tips(self.table, {i: f"reduce.rules.col.{i}" for i in range(len(TABLE_HEAD))})
        root.addWidget(self.table, 2)
        bb = QHBoxLayout()
        self.apply_btn = QPushButton("Apply now"); self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply_now)
        self.save_btn = QPushButton("Save as default"); self.save_btn.clicked.connect(self.save_default)
        bb.addWidget(self.apply_btn); bb.addWidget(self.save_btn); bb.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        explain_buttons(box)
        bb.addWidget(box)
        root.addLayout(bb)
        explain_all([(self.rule_list, "reduce.rules.list"), (self.add_btn, "reduce.rules.add"),
                     (self.remove_btn, "reduce.rules.remove"), (self.up_btn, "reduce.rules.up"),
                     (self.down_btn, "reduce.rules.down"), (self.override, "reduce.rules.override"),
                     (self.apply_btn, "reduce.rules.apply"), (self.save_btn, "reduce.rules.save")])
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh)
        for w in (self.name, self.scope_text, self.edge, self.plan, self.pre, self.post, self.roi):
            w.textChanged.connect(self._edited)
        for w in (self.scope, self.fn, self.tn, self.i0src, self.sig):
            w.currentIndexChanged.connect(self._edited)
        self.line.currentTextChanged.connect(self._edited)
        for w in (self.span_lo, self.span_hi, self.k):
            w.valueChanged.connect(self._edited)
        for w in [self.inc, self.tey, self.ds, self.override, *self.sdd_boxes, *self.flag_boxes.values(), *self.use.values()]:
            w.toggled.connect(self._edited)
        self._fill_list()
        if self.rules:
            self.rule_list.setCurrentRow(0)
        else:
            self._load_rule(-1)
        self.refresh()

    # ------------------------------------------------------------------ data
    def _known_lines(self) -> list[str]:
        out = []
        proj = self.session.project
        if proj is not None and proj.roi_bins is not None:
            out += list(proj.roi_bins.labels)
        for sd in getattr(self.session, "_stacks", {}).values():
            out += [l for l in sd.labels if l not in out]
        if "total XRF" not in out:
            out.append("total XRF")
        return out

    def scans(self) -> dict:
        proj = self.session.project
        return {f"{s.day}_{s.name}": s for s in proj.scans} if proj is not None else {}

    def enabled_rules(self) -> list[rl.Rule]:
        return [r for r in self.rules if r.enabled]

    def dry_run(self) -> list:
        return rl.dry_run(self.enabled_rules(), self.session.zones, self.scans(), self.override.isChecked(),
                          flags_of=self.session.flags_of)

    # ------------------------------------------------------------------ the list
    def _fill_list(self) -> None:
        self._loading = True
        cur = self.rule_list.currentRow()
        self.rule_list.clear()
        for i, r in enumerate(self.rules):
            it = QListWidgetItem(r.name or f"rule {i + 1}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if r.enabled else Qt.Unchecked)
            it.setToolTip("ticked: the rule is used")
            self.rule_list.addItem(it)
        self._loading = False
        if self.rules:
            self.rule_list.setCurrentRow(min(max(cur, 0), len(self.rules) - 1))

    def _ticked(self, it) -> None:
        if self._loading:
            return
        i = self.rule_list.row(it)
        if 0 <= i < len(self.rules):
            self.rules[i].enabled = it.checkState() == Qt.Checked
            self._timer.start()

    def add_rule(self) -> rl.Rule:
        r = rl.Rule(name=f"rule {len(self.rules) + 1}")
        self.rules.append(r)
        self._fill_list()
        self.rule_list.setCurrentRow(len(self.rules) - 1)
        return r

    def remove_rule(self) -> None:
        i = self.rule_list.currentRow()
        if 0 <= i < len(self.rules):
            del self.rules[i]
            self._fill_list()
            if not self.rules:
                self._load_rule(-1)
            self.refresh()

    def move_rule(self, d: int) -> None:
        i = self.rule_list.currentRow()
        j = i + d
        if 0 <= i < len(self.rules) and 0 <= j < len(self.rules):
            self.rules[i], self.rules[j] = self.rules[j], self.rules[i]
            self._fill_list()
            self.rule_list.setCurrentRow(j)
            self.refresh()

    # ------------------------------------------------------------------ the editor
    def current(self) -> rl.Rule | None:
        i = self.rule_list.currentRow()
        return self.rules[i] if 0 <= i < len(self.rules) else None

    def _load_rule(self, i: int) -> None:
        r = self.rules[i] if 0 <= i < len(self.rules) else None
        self._loading = True
        try:
            editable = r is not None
            for w in (self.name, self.scope, self.scope_text, self.edge, self.plan, self.span_lo, self.span_hi,
                      *self.flag_boxes.values(), *self.use.values()):
                w.setEnabled(editable)
            if r is None:
                return
            self.name.setText(r.name)
            kind = r.scope.get("kind", "project")
            keys = [k for k, _t in SCOPES]
            self.scope.setCurrentIndex(keys.index(kind) if kind in keys else 0)
            self.scope_text.setText(", ".join(r.scope.get("holders", []) or r.scope.get("groups", [])))
            f = r.scan_filter
            self.edge.setText(str(f.get("edge", "")))
            self.plan.setText(str(f.get("plan", "")))
            span = f.get("span") or [0, 0]
            self.span_lo.setValue(float(span[0])); self.span_hi.setValue(float(span[1]))
            for k, c in self.flag_boxes.items():
                c.setChecked(k in (f.get("flags") or []))
            for key, c in self.use.items():
                c.setChecked(key in r.patch)
                self.val[key].setEnabled(key in r.patch)
            p = r.patch
            self.inc.setChecked(bool(p.get("inc", True)))
            for c, on in zip(self.sdd_boxes, p.get("sdd", [True] * 4)):
                c.setChecked(bool(on))
            self.line.setCurrentText(str(p.get("line", "")))
            for box, key in ((self.fn, "fn"), (self.tn, "tn")):
                box.setCurrentIndex(max(0, box.findData(p.get(key, "i0jump"))))
            self.tey.setChecked(bool(p.get("tey", True)))
            self.ds.setChecked(bool(p.get("ds", True)))
            self.k.setValue(float(p.get("k", 6.0)))
            self.i0src.setCurrentIndex(max(0, self.i0src.findData(p.get("i0src", "own"))))
            for le, key in ((self.pre, "pre"), (self.post, "post")):
                v = p.get(key)
                le.setText(v if isinstance(v, str) else (f"{v[0]:g}-{v[1]:g}" if v else ""))
            self.roi.setText(windows_text(p.get("roi")))
            self.sig.setCurrentIndex(max(0, self.sig.findData(p.get("sig", "pfy"))))
        finally:
            self._loading = False

    def _edited(self, *_a) -> None:
        if self._loading:
            return
        self._store()
        self._timer.start()

    def _store(self) -> None:
        """The editor into the current rule (problems are listed, never raised)."""
        r = self.current()
        if r is None:
            return
        problems = []
        r.name = self.name.text().strip()
        it = self.rule_list.currentItem()
        if it is not None:
            self._loading = True
            it.setText(r.name or f"rule {self.rule_list.currentRow() + 1}")
            self._loading = False
        kind = self.scope.currentData()
        items = [s.strip() for s in self.scope_text.text().split(",") if s.strip()]
        if kind == "holders":
            r.scope = {"kind": "holders", "holders": items}
        elif kind == "groups":
            r.scope = {"kind": "groups", "groups": items}
        elif kind == "zones":
            zid = self.session.current_zone
            r.scope = {"kind": "zones", "zones": [[self.session.current_holder, zid]] if zid is not None else []}
            if zid is None:
                problems.append("no zone is selected")
        else:
            r.scope = {"kind": "project"}
        self.scope_text.setEnabled(kind in ("holders", "groups"))
        f = {}
        if self.edge.text().strip():
            f["edge"] = self.edge.text().strip()
        if self.plan.text().strip():
            try:
                re.compile(self.plan.text().strip())
                f["plan"] = self.plan.text().strip()
            except re.error as err:
                problems.append(f"scan name: {err}")
        if self.span_lo.value() > 0 or self.span_hi.value() > 0:
            f["span"] = [self.span_lo.value(), self.span_hi.value() or 1e9]
        flags = [k for k, c in self.flag_boxes.items() if c.isChecked()]
        if flags:
            f["flags"] = flags
        r.scan_filter = f
        patch = {}
        values = {"inc": self.inc.isChecked(), "sdd": [c.isChecked() for c in self.sdd_boxes],
                  "line": self.line.currentText().strip(), "fn": self.fn.currentData(), "tey": self.tey.isChecked(),
                  "tn": self.tn.currentData(), "ds": self.ds.isChecked(), "k": float(self.k.value()),
                  "i0src": self.i0src.currentData(), "sig": self.sig.currentData()}
        for key, c in self.use.items():
            if not c.isChecked():
                continue
            if key in ("pre", "post"):
                txt = (self.pre if key == "pre" else self.post).text().strip()
                try:
                    if not rl.is_relative(txt):
                        rl.parse_range(txt)
                    patch[key] = txt if rl.is_relative(txt) else rl.parse_range(txt)
                except ValueError as err:
                    problems.append(str(err))
            elif key == "roi":
                try:
                    patch[key] = parse_windows(self.roi.text())
                except ValueError as err:
                    problems.append(str(err))
            elif key == "line" and not values["line"]:
                problems.append("line: name a line")
            else:
                patch[key] = values[key]
        r.patch = patch
        self.problem.setText(" · ".join(problems))

    # ------------------------------------------------------------------ the table and the buttons
    def refresh(self) -> int:
        """Fill the table from the dry run of the enabled rules; returns the row count."""
        rows = self.dry_run()
        names = {}
        for hk, hz in self.session.zones.holders.items():
            for z in hz.zones:
                names[(hk, z.id)] = z.label
        self.table.setRowCount(len(rows))
        for i, c in enumerate(rows):
            cells = (c.holder, f"{c.zone}  {names.get((c.holder, c.zone), '')}",
                     f"{short_time(c.sid) or ''}  {split_stack_id(c.sid)[1]}", rl.FIELD_LABELS.get(c.field, c.field),
                     rl.describe_value(c.field, c.old), rl.describe_value(c.field, c.new))
            for j, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                it.setToolTip(str(text))
                self.table.setItem(i, j, it)
        cards = {(c.holder, c.zone, c.sid) for c in rows}
        zones = {(c.holder, c.zone) for c in rows}
        self.count.setText(f"{plural(len(rows), 'field')} would change in {plural(len(cards), 'card')} of "
                           f"{plural(len(zones), 'zone')}" if rows else "nothing would change")
        self.apply_btn.setEnabled(bool(rows))
        return len(rows)

    def apply_now(self) -> list:
        """Apply the enabled rules to the existing settings, in one edit."""
        self._store()
        rules = self.enabled_rules()
        if not rules:
            return []
        with self.session.edit(f"apply {plural(len(rules), 'settings rule')}"):
            changes = rl.apply(rules, self.session.zones, self.scans(), self.override.isChecked(),
                               flags_of=self.session.flags_of)
        self.refresh()
        self.count.setText(f"applied: {plural(len(changes), 'field')} in {rl.summary(changes)}" if changes else
                           "nothing changed")
        return changes

    def save_default(self) -> None:
        """Store the rule list in the session: new zone x scan settings inherit the enabled rules."""
        self._store()
        data = rl.rules_to_json(self.rules)
        if data == self.session.zones.rules:
            self.count.setText("the saved rules are already these")
            return
        with self.session.edit("save the settings rules"):
            self.session.zones.rules = data
        self.count.setText(f"{plural(len(self.enabled_rules()), 'rule')} saved: new scans inherit them")
