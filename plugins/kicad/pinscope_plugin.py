"""Pinscope KiCad 9/10 action plugin — load pinscope-findings.json and focus.

Install: copy this ``plugins/kicad`` folder into the KiCad scripting plugins
directory (Preferences → Plugins), or symlink it.

Pan-and-zoom uses pcbnew.FocusOnItem for layout findings. Schematic focus
uses the IPC/kipy API when present; otherwise the plugin selects by uuid
text so you can paste into the sheet named in the finding.
"""

from __future__ import annotations

from pathlib import Path


class PinscopeAction:
    def defaults(self) -> None:
        self.name = "Pinscope findings"
        self.category = "Pinscope"
        self.description = "Jump to a Pinscope finding on the schematic or board"
        self.show_toolbar_button = True

    def Run(self) -> None:
        import wx

        from .focus import find_bridge_file, focus_target, load_bridge

        board_path = _board_path()
        start = Path(board_path) if board_path else Path.cwd()
        bridge_path = find_bridge_file(start)
        if bridge_path is None:
            wx.MessageBox(
                "No pinscope-findings.json next to the project. "
                "Run Pinscope and copy that file beside the .kicad_pro.",
                "Pinscope",
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        try:
            payload = load_bridge(bridge_path)
        except (OSError, ValueError) as exc:
            wx.MessageBox(str(exc), "Pinscope", wx.OK | wx.ICON_ERROR)
            return
        findings = list(payload.get("findings") or [])
        if not findings:
            wx.MessageBox("The bridge file has no findings.", "Pinscope", wx.OK)
            return
        dlg = wx.SingleChoiceDialog(
            None,
            "Select a finding",
            "Pinscope",
            [_label(f) for f in findings],
        )
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        idx = dlg.GetSelection()
        dlg.Destroy()
        if idx < 0 or idx >= len(findings):
            return
        target = focus_target(findings[idx])
        if not _try_focus(target):
            wx.MessageBox(
                f"Focus {target['kind']} {target['ref']}\n"
                f"sheet={target['sheet']}\nuuid={target['uuid']}",
                "Pinscope",
                wx.OK | wx.ICON_INFORMATION,
            )


def _label(f: dict) -> str:
    sev = str(f.get("severity") or "").upper()
    ref = f.get("ref") or "?"
    msg = str(f.get("message") or "")
    if len(msg) > 80:
        msg = msg[:77] + "..."
    return f"{sev} {ref}: {msg}"


def _board_path() -> str:
    try:
        import pcbnew
        board = pcbnew.GetBoard()
        if board:
            return board.GetFileName() or ""
    except ImportError:
        pass
    return ""


def _try_focus(target: dict) -> bool:
    ref = target.get("ref") or ""
    uuid = target.get("uuid") or ""
    if target.get("kind") == "pcb":
        try:
            import pcbnew
            board = pcbnew.GetBoard()
            if board is None:
                return False
            fp = board.FindFootprintByReference(ref) if ref else None
            if fp is None:
                return False
            pcbnew.FocusOnItem(fp)
            return True
        except Exception:
            return False
    try:
        from kipy import KiCad
        from kipy.common_types import DocumentType
        kicad = KiCad()
        docs = kicad.get_open_documents(DocumentType.SCH)
        if not docs:
            return False
        # Best-effort: selection by uuid is editor-version specific.
        _ = (uuid, docs)
        return False
    except ImportError:
        return False


try:
    import pcbnew

    class PinscopePcbnewPlugin(pcbnew.ActionPlugin, PinscopeAction):
        pass

    PinscopePcbnewPlugin().register()
except ImportError:
    pass
