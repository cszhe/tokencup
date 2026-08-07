"""API tests. These require a live MariaDB, configured via TOKENCUP_CONFIG.

Each test runs against the configured database and cleans up the games it
created, so it is safe to point at the normal development database.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

import main as app_module  # noqa: E402
from config import ConfigError, load_config  # noqa: E402
from db import Database  # noqa: E402


@pytest.fixture(scope="session")
def client():
    try:
        config = load_config()
    except ConfigError as exc:
        pytest.skip(f"no usable config: {exc}")

    database = Database(config)
    try:
        database.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"MariaDB unreachable: {exc}")

    app_module.database = database
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def new_game(client):
    """Create a game and delete it (and its moves, via cascade) afterwards."""
    created = []

    def _create(white="test-white", black="test-black", **kwargs):
        payload = {"white_name": white, "black_name": black, **kwargs}
        response = client.post("/games", json=payload)
        assert response.status_code == 201, response.text
        game = response.json()
        created.append(game["id"])
        return game

    yield _create

    with app_module.database.transaction() as cur:
        for game_id in created:
            cur.execute("DELETE FROM games WHERE id = %s", (game_id,))


def play(client, game_id, moves):
    """Submit a sequence of moves, asserting each is accepted."""
    state = None
    for move in moves:
        response = client.post(f"/games/{game_id}/moves", json={"move": move})
        assert response.status_code == 200, f"{move}: {response.text}"
        state = response.json()
    return state


class TestCreateAndFetch:
    def test_create_returns_starting_position(self, new_game):
        game = new_game("alice", "bob")
        assert game["status"] == "active"
        assert game["turn"] == "w"
        assert game["result"] is None
        assert game["fen"].startswith("rnbqkbnr/pppppppp")
        assert game["moves"] == []
        assert game["white_name"] == "alice"

    def test_get_game(self, client, new_game):
        game = new_game()
        fetched = client.get(f"/games/{game['id']}").json()
        assert fetched["id"] == game["id"]

    def test_get_missing_is_404(self, client):
        response = client.get("/games/does-not-exist")
        assert response.status_code == 404

    def test_list_includes_new_game(self, client, new_game):
        game = new_game()
        listed = client.get("/games?limit=200").json()
        assert game["id"] in [g["id"] for g in listed]

    def test_list_filter_by_status(self, client, new_game):
        new_game()
        for entry in client.get("/games?status=finished&limit=50").json():
            assert entry["status"] == "finished"

    def test_match_id_roundtrips(self, new_game):
        game = new_game(match_id="00000000-0000-0000-0000-000000000123")
        assert game["match_id"] == "00000000-0000-0000-0000-000000000123"

    def test_create_rejects_blank_name(self, client):
        response = client.post("/games", json={"white_name": "", "black_name": "b"})
        assert response.status_code == 422


class TestMoves:
    def test_legal_san_move(self, client, new_game):
        game = new_game()
        state = play(client, game["id"], ["e4"])
        assert state["turn"] == "b"
        assert state["moves"][0]["san"] == "e4"
        assert state["moves"][0]["uci"] == "e2e4"
        assert state["moves"][0]["ply"] == 1

    def test_legal_uci_move(self, client, new_game):
        game = new_game()
        state = play(client, game["id"], ["e2e4"])
        assert state["moves"][0]["san"] == "e4"

    def test_history_is_persisted_and_ordered(self, client, new_game):
        game = new_game()
        play(client, game["id"], ["e4", "e5", "Nf3", "Nc6"])
        fetched = client.get(f"/games/{game['id']}").json()
        assert [m["san"] for m in fetched["moves"]] == ["e4", "e5", "Nf3", "Nc6"]
        assert [m["ply"] for m in fetched["moves"]] == [1, 2, 3, 4]
        assert [m["side"] for m in fetched["moves"]] == ["w", "b", "w", "b"]

    def test_pgn_is_updated(self, client, new_game):
        game = new_game("alice", "bob")
        state = play(client, game["id"], ["e4", "e5"])
        assert "1. e4 e5" in state["pgn"]
        assert 'White "alice"' in state["pgn"]

    def test_illegal_move_rejected_409(self, client, new_game):
        game = new_game()
        response = client.post(f"/games/{game['id']}/moves", json={"move": "e5"})
        assert response.status_code == 409
        assert "not legal" in response.json()["detail"]

    def test_unparseable_move_rejected_400(self, client, new_game):
        game = new_game()
        response = client.post(f"/games/{game['id']}/moves", json={"move": "Zz9"})
        assert response.status_code == 400

    def test_null_move_rejected(self, client, new_game):
        game = new_game()
        response = client.post(f"/games/{game['id']}/moves", json={"move": "0000"})
        assert response.status_code == 400

    def test_turn_order_enforced(self, client, new_game):
        """The same side cannot move twice in a row."""
        game = new_game()
        play(client, game["id"], ["e4"])
        response = client.post(f"/games/{game['id']}/moves", json={"move": "d4"})
        assert response.status_code == 409

    def test_rejected_move_is_not_recorded(self, client, new_game):
        game = new_game()
        client.post(f"/games/{game['id']}/moves", json={"move": "e5"})
        assert client.get(f"/games/{game['id']}").json()["moves"] == []

    def test_move_on_missing_game_404(self, client):
        response = client.post("/games/nope/moves", json={"move": "e4"})
        assert response.status_code == 404


class TestGameEnd:
    def test_checkmate_finishes_game(self, client, new_game):
        game = new_game()
        state = play(client, game["id"], ["f3", "e5", "g4", "Qh4"])  # Fool's mate
        assert state["status"] == "finished"
        assert state["result"] == "0-1"
        assert state["termination"] == "checkmate"

    def test_no_moves_after_finish(self, client, new_game):
        game = new_game()
        play(client, game["id"], ["f3", "e5", "g4", "Qh4"])
        response = client.post(f"/games/{game['id']}/moves", json={"move": "e4"})
        assert response.status_code == 409
        assert "already finished" in response.json()["detail"]

    def test_threefold_repetition_finishes_game(self, client, new_game):
        """End-to-end proof that the server replays history, not just the FEN."""
        game = new_game()
        # The shuffle repeats the start position twice; the draw becomes
        # claimable on the move that would produce the third occurrence, so the
        # game ends on ply 7 rather than 8.
        shuffle = (["Nf3", "Nf6", "Ng1", "Ng8"] * 2)[:7]
        state = play(client, game["id"], shuffle)
        assert state["status"] == "finished"
        assert state["result"] == "1/2-1/2"
        assert state["termination"] == "repetition"


class TestResignAndAdjudicate:
    def test_white_resigns(self, client, new_game):
        game = new_game()
        response = client.post(f"/games/{game['id']}/resign", json={"player": "white"})
        assert response.status_code == 200
        state = response.json()
        assert state["status"] == "finished"
        assert state["result"] == "0-1"
        assert state["termination"] == "resignation"

    def test_black_forfeits(self, client, new_game):
        game = new_game()
        state = client.post(
            f"/games/{game['id']}/resign",
            json={"player": "black", "termination": "forfeit"},
        ).json()
        assert state["result"] == "1-0"
        assert state["termination"] == "forfeit"

    def test_cannot_resign_twice(self, client, new_game):
        game = new_game()
        client.post(f"/games/{game['id']}/resign", json={"player": "white"})
        response = client.post(f"/games/{game['id']}/resign", json={"player": "white"})
        assert response.status_code == 409

    def test_adjudicate_draw(self, client, new_game):
        game = new_game()
        state = client.post(
            f"/games/{game['id']}/adjudicate",
            json={"result": "1/2-1/2", "termination": "ply_limit"},
        ).json()
        assert state["status"] == "finished"
        assert state["result"] == "1/2-1/2"
        assert state["termination"] == "ply_limit"

    def test_adjudicate_rejects_bad_result(self, client, new_game):
        game = new_game()
        response = client.post(
            f"/games/{game['id']}/adjudicate", json={"result": "winner: white"}
        )
        assert response.status_code == 422


class TestHealth:
    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}
