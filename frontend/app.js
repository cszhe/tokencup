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
  first: document.getElementById('first'),
  prev: document.getElementById('prev'),
  next: document.getElementById('next'),
  last: document.getElementById('last'),
  live: document.getElementById('live'),
  plyLabel: document.getElementById('ply-label'),
  leaderboardBody: document.getElementById('leaderboard-body'),
};

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

let ground = null;
let selectedId = null;
let orientation = 'white';
let lastRenderedFen = null;
let currentGame = null;
// Which ply is on the board: 0 = starting position, n = after the nth move.
// null means "follow the latest move", so a live game keeps advancing.
let viewPly = null;

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

async function refreshLeaderboard() {
  try {
    const data = await api('/leaderboard');
    el.leaderboardBody.replaceChildren();
    
    if (!data.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4;
      td.textContent = 'No agents yet.';
      td.style.color = 'var(--muted)';
      tr.append(td);
      el.leaderboardBody.append(tr);
      return;
    }

    for (const row of data) {
      const tr = document.createElement('tr');
      
      const tdAgent = document.createElement('td');
      tdAgent.textContent = row.agent_name;
      
      const tdPlayed = document.createElement('td');
      tdPlayed.textContent = row.games;
      
      const tdWDL = document.createElement('td');
      tdWDL.textContent = `${row.wins}-${row.draws}-${row.losses}`;
      
      const tdRate = document.createElement('td');
      tdRate.textContent = `${(row.win_rate * 100).toFixed(1)}%`;
      
      tr.append(tdAgent, tdPlayed, tdWDL, tdRate);
      el.leaderboardBody.append(tr);
    }
  } catch (err) {
    console.error('Leaderboard error:', err);
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

/** Board state after `ply` moves. ply 0 is the starting position. */
function positionAt(game, ply) {
  if (ply <= 0) {
    return { fen: STARTING_FEN, lastMove: undefined, turnColor: 'white' };
  }
  const move = game.moves[ply - 1];
  return {
    fen: move.fen_after,
    lastMove: [move.uci.slice(0, 2), move.uci.slice(2, 4)],
    turnColor: move.side === 'w' ? 'black' : 'white',
  };
}

/** The ply actually on the board: the latest one unless the user scrubbed away. */
function effectivePly(game) {
  if (viewPly === null) return game.moves.length;
  return Math.max(0, Math.min(viewPly, game.moves.length));
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
  const shown = effectivePly(game);
  let selectedCell = null;

  for (let i = 0; i < total; i += 2) {
    const num = document.createElement('li');
    num.className = 'num';
    num.textContent = `${i / 2 + 1}.`;
    el.moves.append(num);

    for (const index of [i, i + 1]) {
      const cell = document.createElement('li');
      cell.className = 'san';
      if (index < total) {
        const ply = index + 1;
        cell.textContent = game.moves[index].san;
        cell.tabIndex = 0;
        cell.classList.add('clickable');
        if (ply === shown) {
          cell.classList.add('last');
          selectedCell = cell;
        }
        cell.addEventListener('click', () => goTo(ply));
        cell.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            goTo(ply);
          }
        });
      }
      el.moves.append(cell);
    }
  }

  // if (selectedCell) selectedCell.scrollIntoView({ block: 'nearest' });
  // else el.moves.scrollTop = 0;
}

function renderControls(game) {
  const total = game.moves.length;
  const shown = effectivePly(game);

  el.first.disabled = shown === 0;
  el.prev.disabled = shown === 0;
  el.next.disabled = shown >= total;
  el.last.disabled = shown >= total;

  // "follow live" only means something while the game is still running.
  el.live.hidden = !(game.status === 'active' && viewPly !== null);

  if (shown === 0) {
    el.plyLabel.textContent = total ? `start (0/${total})` : 'start';
    return;
  }
  const move = game.moves[shown - 1];
  const number = Math.floor((shown - 1) / 2) + 1;
  const dots = move.side === 'w' ? '.' : '...';
  el.plyLabel.textContent = `${number}${dots} ${move.san}  (${shown}/${total})`;
}

function renderGame(game) {
  el.empty.hidden = true;
  el.game.hidden = false;
  currentGame = game;

  renderPlayers(game);
  renderStatus(game);
  renderMoves(game);
  renderControls(game);

  const board = ensureBoard();
  const position = positionAt(game, effectivePly(game));
  // Only touch the board when the position actually changed, so the animation
  // does not restart on every poll.
  if (position.fen !== lastRenderedFen) {
    board.set({
      fen: position.fen.split(' ')[0],
      lastMove: position.lastMove,
      turnColor: position.turnColor,
      check: false,
    });
    lastRenderedFen = position.fen;
  }
}

// ---------- replay navigation ----------

function goTo(ply) {
  if (!currentGame) return;
  const total = currentGame.moves.length;
  const target = Math.max(0, Math.min(ply, total));
  // Scrubbing to the final move of a live game means "follow it again".
  viewPly = (currentGame.status === 'active' && target === total) ? null : target;
  renderGame(currentGame);
}

function step(delta) {
  if (!currentGame) return;
  goTo(effectivePly(currentGame) + delta);
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
  currentGame = null;
  viewPly = null;
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
    currentGame = null;
    viewPly = null;
    refreshGame();
  }
}

el.filter.addEventListener('change', refreshList);
el.flip.addEventListener('click', () => {
  orientation = orientation === 'white' ? 'black' : 'white';
  ensureBoard().set({ orientation });
});

el.first.addEventListener('click', () => goTo(0));
el.prev.addEventListener('click', () => step(-1));
el.next.addEventListener('click', () => step(1));
el.last.addEventListener('click', () => goTo(currentGame ? currentGame.moves.length : 0));
el.live.addEventListener('click', () => {
  viewPly = null;
  if (currentGame) renderGame(currentGame);
});

document.addEventListener('keydown', (event) => {
  if (event.target instanceof HTMLSelectElement) return;
  const actions = {
    ArrowLeft: () => step(-1),
    ArrowRight: () => step(1),
    Home: () => goTo(0),
    End: () => goTo(currentGame ? currentGame.moves.length : 0),
  };
  const action = actions[event.key];
  if (!action) return;
  event.preventDefault();
  action();
});

window.addEventListener('hashchange', readHash);

readHash();
refreshList();
refreshGame();
refreshLeaderboard();
setInterval(() => { refreshList(); refreshLeaderboard(); }, POLL_MS * 2);
setInterval(refreshGame, POLL_MS);
