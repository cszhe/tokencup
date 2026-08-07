import { Chessground } from 'https://cdn.jsdelivr.net/npm/chessground@9.2.1/dist/chessground.min.js';

const POLL_MS = 1500;

const el = {
  list: document.getElementById('game-list'),
  filter: document.getElementById('filter'),
  empty: document.getElementById('empty'),
  game: document.getElementById('game'),
  board: document.getElementById('board'),
  status: document.getElementById('status'),
  moves: document.getElementById('moves'),
  flip: document.getElementById('flip'),
  conn: document.getElementById('conn'),
  white: document.getElementById('white-player'),
  black: document.getElementById('black-player'),
};

let ground = null;
let selectedId = null;
let orientation = 'white';
let lastRenderedFen = null;

const TERMINATION_LABELS = {
  checkmate: 'checkmate',
  stalemate: 'stalemate',
  insufficient_material: 'insufficient material',
  fifty_moves: 'the fifty-move rule',
  repetition: 'threefold repetition',
  resignation: 'resignation',
  forfeit: 'forfeit',
  ply_limit: 'the ply limit',
  adjudicated: 'adjudication',
};

async function api(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function setConnected(ok) {
  el.conn.hidden = ok;
}

// ---------- game list ----------

function renderList(games) {
  el.list.replaceChildren();
  if (!games.length) {
    const li = document.createElement('li');
    li.textContent = 'No games yet.';
    li.style.color = 'var(--muted)';
    li.style.cursor = 'default';
    el.list.append(li);
    return;
  }

  for (const game of games) {
    const li = document.createElement('li');
    li.dataset.id = game.id;
    if (game.id === selectedId) li.classList.add('selected');

    const vs = document.createElement('span');
    vs.className = 'vs';
    vs.textContent = `${game.white_name} vs ${game.black_name}`;

    const meta = document.createElement('span');
    meta.className = 'meta';

    const badge = document.createElement('span');
    badge.className = `badge ${game.status}`;
    badge.textContent = game.status === 'active' ? 'active' : (game.result ?? 'done');
    meta.append(badge);

    const plies = document.createElement('span');
    plies.textContent = `${game.move_count} plies`;
    meta.append(plies);

    li.append(vs, meta);
    li.addEventListener('click', () => select(game.id));
    el.list.append(li);
  }
}

async function refreshList() {
  try {
    const games = await api(`/games?limit=50${el.filter.value ? `&status=${el.filter.value}` : ''}`);
    setConnected(true);
    renderList(games);
  } catch (err) {
    setConnected(false);
  }
}

// ---------- board ----------

function ensureBoard() {
  if (ground) return ground;
  ground = Chessground(el.board, {
    viewOnly: true,
    coordinates: true,
    orientation,
    animation: { enabled: true, duration: 180 },
  });
  return ground;
}

function lastMoveSquares(moves) {
  if (!moves.length) return undefined;
  const uci = moves[moves.length - 1].uci;
  return [uci.slice(0, 2), uci.slice(2, 4)];
}

function renderPlayers(game) {
  const setPlayer = (node, name, isToMove) => {
    node.querySelector('.name').textContent = name;
    node.querySelector('.to-move').hidden = !isToMove;
  };
  const active = game.status === 'active';
  setPlayer(el.white, game.white_name, active && game.turn === 'w');
  setPlayer(el.black, game.black_name, active && game.turn === 'b');
}

function renderStatus(game) {
  el.status.replaceChildren();
  if (game.status === 'active') {
    const who = game.turn === 'w' ? game.white_name : game.black_name;
    el.status.append(`Game in progress — ${who} to move (ply ${game.moves.length + 1}).`);
    return;
  }

  const result = document.createElement('span');
  result.className = 'result';
  result.textContent = game.result ?? '?';
  const reason = TERMINATION_LABELS[game.termination] ?? game.termination ?? 'unknown';

  let winner = 'Draw';
  if (game.result === '1-0') winner = `${game.white_name} wins`;
  else if (game.result === '0-1') winner = `${game.black_name} wins`;

  el.status.append(result, ` — ${winner} by ${reason}, ${game.moves.length} plies.`);
}

function renderMoves(game) {
  el.moves.replaceChildren();
  const total = game.moves.length;
  for (let i = 0; i < total; i += 2) {
    const num = document.createElement('li');
    num.className = 'num';
    num.textContent = `${i / 2 + 1}.`;
    el.moves.append(num);

    for (const ply of [i, i + 1]) {
      const cell = document.createElement('li');
      cell.className = 'san';
      if (ply < total) {
        cell.textContent = game.moves[ply].san;
        if (ply === total - 1) cell.classList.add('last');
      }
      el.moves.append(cell);
    }
  }
  el.moves.scrollTop = el.moves.scrollHeight;
}

function renderGame(game) {
  el.empty.hidden = true;
  el.game.hidden = false;

  renderPlayers(game);
  renderStatus(game);
  renderMoves(game);

  const board = ensureBoard();
  // Only touch the board when the position actually changed, so the animation
  // does not restart on every poll.
  if (game.fen !== lastRenderedFen) {
    board.set({
      fen: game.fen.split(' ')[0],
      lastMove: lastMoveSquares(game.moves),
      turnColor: game.turn === 'w' ? 'white' : 'black',
      check: false,
    });
    lastRenderedFen = game.fen;
  }
}

async function refreshGame() {
  if (!selectedId) return;
  try {
    const game = await api(`/games/${selectedId}`);
    setConnected(true);
    renderGame(game);
  } catch (err) {
    setConnected(false);
  }
}

// ---------- selection & routing ----------

function select(id) {
  if (id === selectedId) return;
  selectedId = id;
  lastRenderedFen = null;
  window.location.hash = id ?? '';
  for (const li of el.list.children) {
    li.classList.toggle('selected', li.dataset.id === id);
  }
  refreshGame();
}

function readHash() {
  const id = window.location.hash.replace(/^#/, '');
  if (id && id !== selectedId) {
    selectedId = id;
    lastRenderedFen = null;
    refreshGame();
  }
}

el.filter.addEventListener('change', refreshList);
el.flip.addEventListener('click', () => {
  orientation = orientation === 'white' ? 'black' : 'white';
  ensureBoard().set({ orientation });
});
window.addEventListener('hashchange', readHash);

readHash();
refreshList();
refreshGame();
setInterval(refreshList, POLL_MS * 2);
setInterval(refreshGame, POLL_MS);
