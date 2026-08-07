"""Pure rules tests -- no database, always runnable."""

import sys
from pathlib import Path

import chess
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import chess_logic as cl  # noqa: E402


def play(moves: list[str]) -> chess.Board:
    """Apply a list of SAN/UCI moves to a fresh board."""
    board = chess.Board()
    for m in moves:
        cl.apply_move(board, m)
    return board


class TestParsing:
    def test_accepts_san(self):
        board = chess.Board()
        assert cl.parse_move(board, "e4").uci() == "e2e4"
        assert cl.parse_move(board, "Nf3").uci() == "g1f3"

    def test_accepts_uci(self):
        board = chess.Board()
        assert cl.parse_move(board, "e2e4").uci() == "e2e4"

    def test_strips_whitespace(self):
        assert cl.parse_move(chess.Board(), "  e4  ").uci() == "e2e4"

    @pytest.mark.parametrize("bad", ["", "   ", "zz", "xyz123", "hello", "e9"])
    def test_unparseable(self, bad):
        with pytest.raises(cl.UnparseableMoveError):
            cl.parse_move(chess.Board(), bad)

    def test_null_move_rejected(self):
        # "0000" parses as a null move in python-chess; it must not be playable,
        # or an agent could pass its turn.
        with pytest.raises(cl.UnparseableMoveError):
            cl.parse_move(chess.Board(), "0000")

    @pytest.mark.parametrize("bad", ["e5", "O-O", "Qh5xf7", "a1a8"])
    def test_illegal_in_position(self, bad):
        with pytest.raises(cl.IllegalMoveError):
            cl.parse_move(chess.Board(), bad)

    def test_ambiguous_move_rejected(self):
        # Both knights can reach d2; bare "Nd2" is ambiguous.
        board = play(["Nf3", "e5", "Nc3", "e4"])
        with pytest.raises(cl.IllegalMoveError):
            cl.parse_move(board, "Nd2")


class TestTurnOrder:
    def test_cannot_move_twice_in_a_row(self):
        board = chess.Board()
        cl.apply_move(board, "e4")
        # d4 is a legal-looking white move, but it is black's turn now.
        with pytest.raises(cl.IllegalMoveError):
            cl.apply_move(board, "d4")

    def test_side_is_recorded(self):
        board = chess.Board()
        assert cl.apply_move(board, "e4").side == "w"
        assert cl.apply_move(board, "e5").side == "b"


class TestApplyMove:
    def test_san_computed_before_push(self):
        # If SAN were taken after pushing, this would come out wrong or raise.
        board = chess.Board()
        applied = cl.apply_move(board, "g1f3")
        assert applied.san == "Nf3"
        assert applied.uci == "g1f3"

    def test_fen_after_advances(self):
        board = chess.Board()
        applied = cl.apply_move(board, "e4")
        assert applied.fen_after == board.fen()
        assert cl.turn_of(applied.fen_after) == "b"

    def test_promotion(self):
        board = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
        applied = cl.apply_move(board, "a8=Q")
        assert applied.uci == "a7a8q"


class TestBuildBoard:
    def test_replay_reproduces_position(self):
        moves = ["e2e4", "e7e5", "g1f3", "b8c6"]
        assert cl.build_board(moves).fen() == play(moves).fen()

    def test_empty_is_starting_position(self):
        assert cl.build_board([]).fen() == cl.STARTING_FEN


class TestGameOver:
    def test_none_while_playing(self):
        assert cl.detect_game_over(chess.Board()) is None

    def test_checkmate(self):
        board = play(["f3", "e5", "g4", "Qh4"])  # Fool's mate
        over = cl.detect_game_over(board)
        assert over == cl.GameOver(result=cl.BLACK_WINS, termination=cl.CHECKMATE)

    def test_stalemate(self):
        board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        over = cl.detect_game_over(board)
        assert over.result == cl.DRAW
        assert over.termination == cl.STALEMATE

    def test_insufficient_material(self):
        board = chess.Board("7k/8/8/8/8/8/8/K6B w - - 0 1")
        over = cl.detect_game_over(board)
        assert over.result == cl.DRAW
        assert over.termination == cl.INSUFFICIENT_MATERIAL

    def test_threefold_repetition_detected(self):
        """Regression: a board built from FEN alone can never detect this."""
        shuffle = ["Nf3", "Nf6", "Ng1", "Ng8"] * 2
        board = play(shuffle)
        over = cl.detect_game_over(board)
        assert over is not None, "threefold repetition was not detected"
        assert over.result == cl.DRAW
        assert over.termination == cl.REPETITION

    def test_threefold_invisible_without_history(self):
        """Documents exactly why build_board() replay is mandatory."""
        board = play(["Nf3", "Nf6", "Ng1", "Ng8"] * 2)
        assert cl.detect_game_over(board) is not None
        # Same position, no move stack -> the draw becomes invisible.
        assert cl.detect_game_over(chess.Board(board.fen())) is None

    def test_fifty_move_rule_detected(self):
        # The rule is claimable once the side to move can *reach* a halfmove
        # clock of 100 by announcing their move -- so 99 already qualifies and
        # 98 does not.
        board = chess.Board("7k/8/8/3q4/8/8/8/K6R w - - 98 200")
        assert cl.detect_game_over(board) is None
        cl.apply_move(board, "Rh2")  # quiet move -> clock 99
        over = cl.detect_game_over(board)
        assert over is not None, "fifty-move rule was not detected"
        assert over.result == cl.DRAW
        assert over.termination == cl.FIFTY_MOVES

    def test_apply_move_reports_game_over(self):
        board = play(["f3", "e5", "g4"])
        applied = cl.apply_move(board, "Qh4")
        assert applied.game_over.termination == cl.CHECKMATE
        assert applied.game_over.result == cl.BLACK_WINS


class TestPGN:
    def test_roundtrip(self):
        moves = ["e2e4", "e7e5", "g1f3"]
        pgn = cl.to_pgn(moves, "alice", "bob", result=None, game_id="abc")
        assert 'White "alice"' in pgn
        assert 'Black "bob"' in pgn
        assert "1. e4 e5 2. Nf3" in pgn
        assert cl.read_pgn_moves(pgn) == moves

    def test_result_header(self):
        pgn = cl.to_pgn(["f2f3", "e7e5", "g2g4", "d8h4"], "a", "b", result=cl.BLACK_WINS)
        assert 'Result "0-1"' in pgn


class TestHelpers:
    def test_turn_of(self):
        assert cl.turn_of(cl.STARTING_FEN) == "w"

    def test_legal_moves_san(self):
        moves = cl.legal_moves_san(chess.Board())
        assert len(moves) == 20
        assert "e4" in moves and "Nf3" in moves
