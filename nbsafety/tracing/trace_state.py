# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional, Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType
    from .code_line import CodeLine


class TraceState(object):
    def __init__(self):
        self.call_depth = 0
        self.code_lines: Dict[int, CodeLine] = {}
        self.stack: List[CodeLine] = []
        self.source: Optional[str] = None
        self.cur_frame_last_line: Optional[CodeLine] = None
        self.last_event: Optional[str] = None
        self.prev_position: Optional[Tuple[int, int]] = None

    def _prev_line_done_executing(self, event: str, frame: FrameType):
        if event not in ('line', 'return') or self.last_event == 'call':
            return False
        return self.get_position(frame) != self.prev_position

    def update_hook(
            self,
            event: str,
            frame: FrameType,
            code_line: CodeLine
    ):
        if self._prev_line_done_executing(event, frame):
            line = self.cur_frame_last_line
            if line is not None:
                line.make_lhs_data_cells_if_has_lval()

        self.prev_position = self.get_position(frame)

        if code_line is None:
            return

        if event == 'line':
            self.cur_frame_last_line = code_line
        if event == 'call':
            self.stack.append(self.cur_frame_last_line)
            self.cur_frame_last_line = None
        if event == 'return':
            ret_line = self.stack.pop()
            assert ret_line is not None
            # reset 'cur_frame_last_line' for the previous frame, so that we push it again if it has another funcall
            self.cur_frame_last_line = ret_line
            # print('{} @@returning to@@ {}'.format(code_line.text, ret_line.text))
            ret_line.extra_dependencies |= code_line.compute_rval_dependencies()
        self.last_event = event

    @staticmethod
    def get_position(frame: FrameType):
        cell_num = int(frame.f_code.co_filename.split('-')[2])
        return cell_num, frame.f_lineno
