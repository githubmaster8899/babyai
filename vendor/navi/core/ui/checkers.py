from PyQt6.QtWidgets import QWidget, QApplication, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtGui import QPainter, QColor, QFont, QFontDatabase, QPixmap
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from pathlib import Path
import copy

BASE_DIR    = Path(__file__).parent.parent.parent
FONT_PATH   = BASE_DIR / "assets/fonts/pixelFont-7-8x14-sproutLands.ttf"
BOARD_IMG   = BASE_DIR / "assets/ui/checkers/board_plain_05.png"
PIECES_DIR  = BASE_DIR / "assets/ui/checkers/pieces"

C_SELECT = QColor(240, 220, 50, 180)
C_MOVE   = QColor(80,  200, 80, 150)

BOARD_SIZE = 8
BOARD_W   = 284
BOARD_H   = 284
BORDER_X  = 10
BORDER_Y  = 10
CELL_W    = (BOARD_W - 2 * BORDER_X) // BOARD_SIZE  # 33px
CELL_H    = CELL_W  # square

# logic
EMPTY  = 0
PLAYER = 1   
AI     = 2   
P_KING = 3   
A_KING = 4   

def _is_player(p): return p in (PLAYER, P_KING)
def _is_ai(p):     return p in (AI, A_KING)
def _is_king(p):   return p in (P_KING, A_KING)

def init_board():
    b = [[EMPTY]*8 for _ in range(8)]
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1:
                if r < 3:
                    b[r][c] = AI
                elif r > 4:
                    b[r][c] = PLAYER
    return b

def get_moves(board, piece_type):
    moves  = []
    jumps  = []
    is_own = _is_player if piece_type == PLAYER else _is_ai
    is_opp = _is_ai     if piece_type == PLAYER else _is_player

    dirs_fwd = [(-1,-1),(-1,1)] if piece_type == PLAYER else [(1,-1),(1,1)]
    dirs_all = [(-1,-1),(-1,1),(1,-1),(1,1)]

    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if not is_own(p):
                continue

            if _is_king(p):
                for dr, dc in dirs_all:
                    nr, nc = r+dr, c+dc
                    while 0 <= nr < 8 and 0 <= nc < 8:
                        if board[nr][nc] == EMPTY:
                            moves.append((r,c,nr,nc,None))
                        elif is_opp(board[nr][nc]):
                            jr, jc = nr+dr, nc+dc
                            if 0 <= jr < 8 and 0 <= jc < 8 and board[jr][jc] == EMPTY:
                                jumps.append((r,c,jr,jc,(nr,nc)))
                            break  
                        else:
                            break  
                        nr += dr
                        nc += dc
            else:
                for dr, dc in dirs_fwd:
                    nr, nc = r+dr, c+dc
                    if 0 <= nr < 8 and 0 <= nc < 8:
                        if board[nr][nc] == EMPTY:
                            moves.append((r,c,nr,nc,None))
                        elif is_opp(board[nr][nc]):
                            jr, jc = nr+dr, nc+dc
                            if 0 <= jr < 8 and 0 <= jc < 8 and board[jr][jc] == EMPTY:
                                jumps.append((r,c,jr,jc,(nr,nc)))

    return jumps if jumps else moves

def apply_move(board, move):
    b = copy.deepcopy(board)
    r,c,nr,nc,cap = move
    p = b[r][c]
    b[nr][nc] = p
    b[r][c]   = EMPTY
    if cap:
        b[cap[0]][cap[1]] = EMPTY
    if p == PLAYER and nr == 0:
        b[nr][nc] = P_KING
    if p == AI and nr == 7:
        b[nr][nc] = A_KING
    return b

def score_board(board):
    s = 0
    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if p == PLAYER:  s -= 1
            elif p == AI:    s += 1
            elif p == P_KING: s -= 2
            elif p == A_KING: s += 2
    return s

def minimax(board, depth, alpha, beta, maximizing):
    moves = get_moves(board, AI if maximizing else PLAYER)
    if depth == 0 or not moves:
        return score_board(board), None
    best_move = None
    if maximizing:
        best = -9999
        for m in moves:
            nb = apply_move(board, m)
            val, _ = minimax(nb, depth-1, alpha, beta, False)
            if val > best:
                best, best_move = val, m
            alpha = max(alpha, best)
            if beta <= alpha:
                break
        return best, best_move
    else:
        best = 9999
        for m in moves:
            nb = apply_move(board, m)
            val, _ = minimax(nb, depth-1, alpha, beta, True)
            if val < best:
                best, best_move = val, m
            beta = min(beta, best)
            if beta <= alpha:
                break
        return best, best_move

class BoardWidget(QWidget):
    status_changed = pyqtSignal(str)
    game_over      = pyqtSignal(str)  # "player" / "ai" / "draw"

    def __init__(self):
        super().__init__()
        self.setFixedSize(BOARD_W, BOARD_H)
        self.board       = init_board()
        self.selected    = None
        self.valid_moves = []
        self.turn        = PLAYER
        self.thinking    = False

        # load board asset
        self._board_img = QPixmap(str(BOARD_IMG)).scaled(
            BOARD_W, BOARD_H,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation
        )

        # load pieces
        piece_px = int(CELL_W * 0.75)
        self._pieces = {
            PLAYER: QPixmap(str(PIECES_DIR / "player.png")).scaled(
                piece_px, piece_px, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation),
            P_KING: QPixmap(str(PIECES_DIR / "player_king.png")).scaled(
                piece_px, piece_px, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation),
            AI:     QPixmap(str(PIECES_DIR / "ai.png")).scaled(
                piece_px, piece_px, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation),
            A_KING: QPixmap(str(PIECES_DIR / "ai_king.png")).scaled(
                piece_px, piece_px, Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation),
        }

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        # board asset
        p.drawPixmap(0, 0, self._board_img)

        for r in range(8):
            for c in range(8):
                # highlight selected
                if self.selected == (r,c):
                    p.fillRect(BORDER_X+c*CELL_W, BORDER_Y+r*CELL_H, CELL_W, CELL_H, C_SELECT)

                # highlight valid moves
                for m in self.valid_moves:
                    if (m[2],m[3]) == (r,c):
                        p.fillRect(BORDER_X+c*CELL_W, BORDER_Y+r*CELL_H, CELL_W, CELL_H, C_MOVE)

                piece = self.board[r][c]
                if piece != EMPTY:
                    px_sprite = self._pieces[piece]
                    margin_x = (CELL_W - px_sprite.width()) // 2
                    margin_y = (CELL_H - px_sprite.height()) // 2
                    p.drawPixmap(
                        BORDER_X + c*CELL_W + margin_x,
                        BORDER_Y + r*CELL_H + margin_y,
                        px_sprite
                    )

        p.end()

    def mousePressEvent(self, event):
        if self.turn != PLAYER or self.thinking:
            return
        c = (event.pos().x() - BORDER_X) // CELL_W
        r = (event.pos().y() - BORDER_Y) // CELL_H
        if not (0 <= r < 8 and 0 <= c < 8):
            return

        for m in self.valid_moves:
            if (m[2], m[3]) == (r, c):
                self.board = apply_move(self.board, m)
                self.selected    = None
                self.valid_moves = []
                self.turn = AI
                self.update()
                self.status_changed.emit("...")
                QTimer.singleShot(300, self._ai_move)
                return

        if _is_player(self.board[r][c]):
            self.selected = (r, c)
            all_moves = get_moves(self.board, PLAYER)
            self.valid_moves = [m for m in all_moves if m[0]==r and m[1]==c]
            self.update()
            self.status_changed.emit("Move Your Piece")
        else:
            self.selected    = None
            self.valid_moves = []
            self.update()

    def _ai_move(self):
        self.thinking = True
        moves = get_moves(self.board, AI)
        if not moves:
            self.game_over.emit("player")
            return
        _, best = minimax(self.board, 4, -9999, 9999, True)
        if best:
            self.board = apply_move(self.board, best)
        self.thinking = False
        self.turn = PLAYER

        if not get_moves(self.board, PLAYER):
            self.game_over.emit("ai")
            return

        self.update()
        self.status_changed.emit("Your turn")

    def reset(self):
        self.board       = init_board()
        self.selected    = None
        self.valid_moves = []
        self.turn        = PLAYER
        self.thinking    = False
        self.update()
        self.status_changed.emit("Your turn")

# Main window

class CheckersWindow(QWidget):

    def __init__(self, pet_name="Xander"):
        super().__init__()
        self.pet_name = pet_name

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        QApplication.instance().installEventFilter(self)

        font_id  = QFontDatabase.addApplicationFont(str(FONT_PATH))
        families = QFontDatabase.applicationFontFamilies(font_id)
        pf = QFont(families[0] if families else "Courier New", 8)
        pf_sm = QFont(families[0] if families else "Courier New", 7)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # board
        self.board_widget = BoardWidget()
        self.board_widget.status_changed.connect(self._on_status)
        self.board_widget.game_over.connect(self._on_game_over)
        outer.addWidget(self.board_widget, alignment=Qt.AlignmentFlag.AlignHCenter)

        # status + buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.btn_reset = QPushButton("PLAY AGAIN")
        self.btn_close = QPushButton("CLOSE")
        for btn in [self.btn_reset, self.btn_close]:
            btn.setFont(pf_sm)
            btn.setStyleSheet(
                "background: #7a5c3a; color: #f5e6c8; border: 1px solid #c8a87a;"
                "border-radius: 4px; padding: 4px 12px;"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_close.clicked.connect(self.hide)

        self.status_label = QLabel("Your turn")
        self.status_label.setFont(pf_sm)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(
            "color: #f5e6c8; background: #5a3e28; border: 1px solid #c8a87a;"
            "border-radius: 4px; padding: 4px 8px;"
        )

        btn_row.addWidget(self.btn_reset)
        btn_row.addWidget(self.status_label, stretch=1)
        btn_row.addWidget(self.btn_close)
        outer.addLayout(btn_row)

        self.setStyleSheet("background: #3d2b1f; border-radius: 10px;")
        self.adjustSize()

    def _on_status(self, msg):
        self.status_label.setText(msg)

    def _on_game_over(self, winner):
        if winner == "player":
            self.status_label.setText(f"You Win!")
        else:
            self.status_label.setText(f"{self.pet_name} Win!")

    def _on_reset(self):
        self.board_widget.reset()

    def eventFilter(self, obj, event):
        if self.isVisible() and event.type() == event.Type.MouseButtonPress:
            if not self.geometry().contains(event.globalPosition().toPoint()):
                self.hide()
        return False

    def show_centered(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2
        )
        self.show()
        self.raise_()